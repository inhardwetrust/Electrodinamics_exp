# layer_views.py
#
# VisPy implementations of the layer configs in layers.py.
#
# Each view gets a `ctx` (the renderer) providing:
#     ctx.source, ctx.base_step, ctx.prefer_gpu,
#     ctx.heatmap_sample_px, ctx.heatmap_margin_fraction, ctx.smooth_texture,
#     ctx.view_bounds() -> (x_min, x_max, y_min, y_max),
#     ctx.units_per_pixel() -> (ux, uy), ctx.view_size() -> (w_px, h_px)
#
# Hooks:
#     on_view_changed() -> cheap, every zoom/pan event
#     on_view_settled() -> expensive work, once the view stops moving
#     on_data_changed() -> the source describes a new state (time step)

import numpy as np

from vispy import scene
from vispy.color import Color
from vispy.visuals.transforms import STTransform

from color_mapping import (
    apply_scale,
    compute_clim,
    get_lut,
    glsl_scale,
    map_to_rgba,
    running_clim,
)
from display_grid import LatticeGrid, lattice_step
from heatmap_visuals import FieldShaderHeatmap, GridScalarHeatmap
from layers import ScalarLayer, VectorLayer


class ScalarLayerView:
    """
    Heatmap with three backends, best available first:

        "gpu"     -> source.glsl_scalar(): evaluated per pixel in a shader
        "grid"    -> source.grid_array(): solver grid uploaded as a float
                     texture, colored on the GPU (fast path for simulations)
        "texture" -> CPU sampling on a pixel-sized lattice (any source)
    """

    # Lattice used to estimate percentiles for clim policies.
    CLIM_SAMPLE_PX = 6.0

    # "running" limits never fall more than this many decades below the
    # strongest limit seen since the layer was created (i.e. since reset).
    PEAK_FLOOR_DECADES = 3.0

    def __init__(self, layer, ctx, parent):
        self.layer = layer
        self.ctx = ctx

        quantity = ctx.source.quantity(layer.quantity)
        if quantity.kind != "scalar":
            raise ValueError(f"{layer.quantity!r} is not a scalar quantity")

        self._lut = get_lut(layer.colormap)
        self._parent = parent
        self.visual = None

        policy = layer.clim
        if policy.mode == "fixed":
            self.clim = (float(policy.lo), float(policy.hi))
        elif policy.mode == "running":
            # Current data if there is any; a zero field at t = 0 has no
            # meaningful percentiles, then (lo, hi) is the starting point.
            current = self._clim_from_view(allow_empty=True)
            self.clim = (
                current
                if current is not None
                else (float(policy.lo), float(policy.hi))
            )
        else:
            self.clim = self._clim_from_view()

        self._build_visual()

    def _build_visual(self):
        if self.visual is not None:
            self.visual.parent = None

        ctx, layer = self.ctx, self.layer
        scale_body = glsl_scale(layer.scale, layer.linthresh)

        glsl_field = ctx.source.glsl_scalar(layer.quantity) if ctx.prefer_gpu else None
        grid = ctx.source.grid_array(layer.quantity) if ctx.prefer_gpu else None

        if glsl_field is not None:
            self.mode = "gpu"
            self.visual = FieldShaderHeatmap(
                glsl_field, scale_body, self._lut, self.clim,
                parent=self._parent,
            )
            self.visual.set_rect(*ctx.view_bounds())

        elif grid is not None:
            self.mode = "grid"
            self.visual = GridScalarHeatmap(
                scale_body, self._lut, self.clim,
                parent=self._parent,
            )
            self.visual.set_grid(grid)

        else:
            self.mode = "texture"
            self._grid = None
            self._grid_clim = None
            self.visual = scene.visuals.Image(
                data=np.zeros((1, 1, 4), dtype=np.float32),
                interpolation="bilinear" if ctx.smooth_texture else "nearest",
                parent=self._parent,
            )

        self.visual.update_gl_state(depth_test=False)
        self.visual.visible = layer.visible

    def remove(self):
        self.visual.parent = None

    # -------------------------------------------------
    # Hooks
    # -------------------------------------------------

    def on_view_changed(self):
        if self.mode == "gpu":
            # The quad just has to cover the view; the shader does the rest.
            self.visual.set_rect(*self.ctx.view_bounds())

    def on_view_settled(self):
        if self.layer.clim.mode == "view":
            self._set_clim(self._clim_from_view())

        if self.mode == "texture" and self._texture_outdated():
            self._rebuild_texture()

    def on_data_changed(self):
        """The source now describes a new state (e.g. next time step)."""
        if self.layer.clim.mode == "running":
            policy = self.layer.clim
            lo, hi = running_clim(
                self.clim,
                self._clim_from_view(allow_empty=True),
                policy.decay,
                self.layer.scale,
                policy.symmetric,
            )

            # Floor: once a pulse has left, the limits must not decay into
            # float32 noise (the screen would fill with "snow"). Stay within
            # PEAK_FLOOR of the strongest limit seen in this run.
            self._clim_peak = max(getattr(self, "_clim_peak", hi), hi)
            if self.layer.scale == "log":
                floor = self._clim_peak - self.PEAK_FLOOR_DECADES
            else:
                floor = self._clim_peak * 10.0 ** (-self.PEAK_FLOOR_DECADES)
            if hi < floor:
                width = hi - lo
                hi = floor
                lo = -hi if policy.symmetric else (hi - width if self.layer.scale == "log" else lo)

            self._set_clim((lo, hi))

        if self.mode == "grid":
            self.visual.set_grid(self.ctx.source.grid_array(self.layer.quantity))
        elif self.mode == "gpu":
            # Shader code bakes the source state in; recompile.
            self._build_visual()
        else:
            self._rebuild_texture()

    # -------------------------------------------------

    def _set_clim(self, clim):
        self.clim = clim

        if self.mode in ("gpu", "grid"):
            self.visual.set_clim(clim)

    def _sample_scaled(self, points):
        values = self.ctx.source.sample(self.layer.quantity, points)
        return apply_scale(self.layer.scale, values, self.layer.linthresh)

    def _clim_from_view(self, allow_empty=False):
        """Percentile limits of the current view; None if allow_empty and no data."""
        x_min, x_max, y_min, y_max = self.ctx.view_bounds()

        step = lattice_step(
            self.ctx.base_step,
            self.ctx.units_per_pixel()[1],
            self.CLIM_SAMPLE_PX,
        )

        grid = LatticeGrid.covering(x_min, x_max, y_min, y_max, step)
        values = self.ctx.source.sample(self.layer.quantity, grid.node_positions())

        # Values at round-off level carry no range information: exact zeros
        # (a wave that has not arrived; -30 after log) and the round-off
        # noise of quantities that are physically zero almost everywhere
        # (div E / div D away from charges) must not set the limits.
        # Display data is float32 (~7 digits): below 1e-6 of the peak is noise.
        values = values[np.isfinite(values)]
        magnitude = np.abs(values)
        noise_floor = 1e-6 * magnitude.max() if magnitude.size else 0.0
        values = values[magnitude > noise_floor]

        if values.size == 0 and allow_empty:
            return None

        policy = self.layer.clim

        return compute_clim(
            apply_scale(self.layer.scale, values, self.layer.linthresh),
            percentiles=policy.percentiles,
            symmetric=policy.symmetric,
        )

    def _texture_step(self):
        return lattice_step(
            self.ctx.base_step,
            self.ctx.units_per_pixel()[1],
            self.ctx.heatmap_sample_px,
        )

    def _texture_outdated(self):
        grid = self._grid
        return not (
            grid is not None
            and grid.step == self._texture_step()
            and grid.contains(*self.ctx.view_bounds())
            and self._grid_clim == self.clim
        )

    def _rebuild_texture(self):
        """Sample on a pixel-sized lattice covering view + margin."""
        x_min, x_max, y_min, y_max = self.ctx.view_bounds()

        margin_x = (x_max - x_min) * self.ctx.heatmap_margin_fraction
        margin_y = (y_max - y_min) * self.ctx.heatmap_margin_fraction
        step = self._texture_step()

        grid = LatticeGrid.covering(
            x_min - margin_x,
            x_max + margin_x,
            y_min - margin_y,
            y_max + margin_y,
            step,
        )

        rgba = map_to_rgba(
            self._sample_scaled(grid.node_positions()),
            self.clim,
            self._lut,
        )

        # Row 0 = y_min; PanZoomCamera has +Y up, so no flip is needed.
        self.visual.set_data(rgba)

        # Image pixel i covers [i, i+1]; its center must land on node i.
        self.visual.transform = STTransform(
            scale=(step, step, 1.0),
            translate=(grid.x_min - 0.5 * step, grid.y_min - 0.5 * step, 0.0),
        )

        self._grid = grid
        self._grid_clim = self.clim


class VectorLayerView:
    """
    Fixed-pixel-length arrows in three strength levels (see VectorLayer).

    The vector is sampled exactly at the arrow positions: a lattice with
    ~spacing_px on screen, so the arrow count does not depend on zoom.

    Optional source hints (3D slices provide them, 2D sources do not need to):
        "|name|" scalar        -> full magnitude used as the reference
        "name normal" scalar   -> component through the plane
        source.normal_toward_viewer (+1 / -1): sign of "normal" that points
                                 out of the screen
    """

    WEAK_ALPHA = 0.55
    RING_SIZE = 5
    NORMAL_SIZE = 13

    def __init__(self, layer, ctx, parent):
        self.layer = layer
        self.ctx = ctx

        quantity = ctx.source.quantity(layer.quantity)
        if quantity.kind != "vector":
            raise ValueError(f"{layer.quantity!r} is not a vector quantity")

        rgba = np.array(Color(layer.color).rgba, dtype=np.float32)
        weak_rgba = rgba.copy()
        weak_rgba[3] *= self.WEAK_ALPHA

        def arrows(color, width):
            return scene.visuals.Arrow(
                pos=np.empty((0, 2), dtype=np.float32),
                connect="segments",
                arrows=np.empty((0, 4), dtype=np.float32),
                color=color,
                arrow_color=color,
                width=width,
                arrow_size=layer.head_size,
                arrow_type=layer.head_type,
                parent=parent,
            )

        self.strong_visual = arrows(tuple(rgba), layer.line_width)
        self.weak_visual = arrows(tuple(weak_rgba), max(1.0, 0.75 * layer.line_width))

        self._rgba = rgba
        self._weak_rgba = weak_rgba
        # ~zero: small faded ring. Through the plane (textbook notation):
        # circle + dot = out of the screen, circle + cross = into it.
        self.ring_visual = scene.visuals.Markers(parent=parent)
        self.normal_circle_visual = scene.visuals.Markers(parent=parent)
        self.out_visual = scene.visuals.Markers(parent=parent)
        self.in_visual = scene.visuals.Markers(parent=parent)

        self.visuals = (
            self.strong_visual, self.weak_visual, self.ring_visual,
            self.normal_circle_visual, self.out_visual, self.in_visual,
        )
        for v in self.visuals:
            v.visible = layer.visible

        self.counts = {"strong": 0, "weak": 0, "zero": 0, "out": 0, "in": 0}
        self._peak_reference = 0.0

        names = {q.name for q in ctx.source.quantities()}
        self._full_name = f"|{layer.quantity}|" if f"|{layer.quantity}|" in names else None
        self._normal_name = (
            f"{layer.quantity} normal" if f"{layer.quantity} normal" in names else None
        )

    @property
    def arrow_count(self):
        return self.counts["strong"] + self.counts["weak"]

    def remove(self):
        for v in self.visuals:
            v.parent = None

    def on_view_settled(self):
        pass

    def on_data_changed(self):
        self.on_view_changed()

    # -------------------------------------------------

    def on_view_changed(self):
        width_px, height_px = self.ctx.view_size()
        if width_px <= 0 or height_px <= 0:
            return

        layer = self.layer
        source = self.ctx.source
        units_per_pixel = np.array(self.ctx.units_per_pixel(), dtype=np.float64)
        x_min, x_max, y_min, y_max = self.ctx.view_bounds()

        step = lattice_step(self.ctx.base_step, units_per_pixel[1], layer.spacing_px)
        grid = LatticeGrid.covering(x_min, x_max, y_min, y_max, step)
        positions = grid.node_positions().reshape(-1, 2)

        vectors = source.sample(layer.quantity, positions)
        in_plane = np.linalg.norm(vectors, axis=1)
        defined = np.isfinite(in_plane)

        full = (
            source.sample(self._full_name, positions)
            if self._full_name is not None
            else in_plane
        )

        # Reference: strong part of the FULL field among the visible arrows
        # (percentile, not max: near-singular peaks would hide everything).
        ok = defined & np.isfinite(full) & (full > 0.0)
        reference = np.percentile(full[ok], 98.0) if np.any(ok) else 0.0

        # Same floor as the heatmap limits: when the field has died out,
        # leftover round-off noise reads as "zero", not as strong arrows.
        self._peak_reference = max(self._peak_reference, reference)
        reference = max(reference, 1e-3 * self._peak_reference)

        if reference <= 0.0:
            self._draw(np.empty((0, 2)), np.empty((0, 2)), np.empty((0, 2)),
                       np.empty((0, 2)), np.empty((0, 2)), np.empty((0, 2)), np.empty((0, 2)),
                       units_per_pixel)
            return

        nonzero = defined & (in_plane > 0.0)
        strong = nonzero & (in_plane >= layer.weak_fraction * reference)
        weak = nonzero & ~strong & (in_plane >= layer.zero_fraction * reference)
        zero = defined & ~strong & ~weak

        # Through-plane markers where the in-plane part is ~zero but the
        # field itself is not.
        out_pts = in_pts = np.empty((0, 2))
        ring = zero

        if self._normal_name is not None and layer.show_normal_markers:
            normal = source.sample(self._normal_name, positions)
            toward = getattr(source, "normal_toward_viewer", 1.0) * normal
            through = zero & np.isfinite(normal) & (np.abs(normal) >= layer.weak_fraction * reference)
            out_pts = positions[through & (toward > 0.0)]
            in_pts = positions[through & (toward < 0.0)]
            ring = zero & ~through

        ring_pts = positions[ring] if layer.show_zero_markers else np.empty((0, 2))

        self._draw(
            positions[strong], vectors[strong],
            positions[weak], vectors[weak],
            ring_pts, out_pts, in_pts,
            units_per_pixel,
        )

    def _screen_arrows(self, positions, vectors, length_px, units_per_pixel):
        if len(positions) == 0:
            return np.empty((0, 2), np.float32), np.empty((0, 4), np.float32)

        directions = vectors / np.linalg.norm(vectors, axis=1)[:, np.newaxis]

        # Direction in screen space -> unit length -> back to world.
        directions_px = directions / units_per_pixel
        directions_px /= np.linalg.norm(directions_px, axis=1)[:, np.newaxis]
        world = directions_px * length_px * units_per_pixel

        return build_arrow_geometry(
            positions.astype(np.float32),
            world.astype(np.float32),
        )

    def _draw(self, s_pos, s_vec, w_pos, w_vec, ring_pts, out_pts, in_pts, units_per_pixel):
        layer = self.layer

        lines, heads = self._screen_arrows(s_pos, s_vec, layer.length_px, units_per_pixel)
        self.strong_visual.set_data(pos=lines, arrows=heads)

        lines, heads = self._screen_arrows(
            w_pos, w_vec, layer.length_px * layer.weak_length_scale, units_per_pixel,
        )
        self.weak_visual.set_data(pos=lines, arrows=heads)

        def markers(visual, pts, symbol, size, face, edge, edge_width=1.5):
            if len(pts) == 0:
                visual.visible = False
                return
            visual.visible = layer.visible
            visual.set_data(
                pos=np.asarray(pts, dtype=np.float32),
                symbol=symbol, size=size,
                face_color=face, edge_color=edge, edge_width=edge_width,
            )

        rgba, weak = tuple(self._rgba), tuple(self._weak_rgba)
        clear = (0.0, 0.0, 0.0, 0.0)
        through = np.concatenate([np.reshape(out_pts, (-1, 2)), np.reshape(in_pts, (-1, 2))])

        markers(self.ring_visual, ring_pts, "disc", self.RING_SIZE, weak, weak)
        # Hollow circle (VisPy's "ring" symbol is filled): outline only.
        markers(self.normal_circle_visual, through, "disc", self.NORMAL_SIZE, clear, rgba, 1.8)
        # Out of the screen: dot (the tip of an arrow coming at you).
        markers(self.out_visual, out_pts, "disc", 0.38 * self.NORMAL_SIZE, rgba, rgba)
        # Into the screen: cross (the tail feathers of an arrow going away).
        markers(self.in_visual, in_pts, "x", 0.62 * self.NORMAL_SIZE, rgba, rgba, 0.5)

        self.counts = {
            "strong": len(s_pos), "weak": len(w_pos), "zero": len(ring_pts),
            "out": len(out_pts), "in": len(in_pts),
        }


def build_arrow_geometry(positions, vectors):
    end_positions = positions + vectors

    arrow_lines = np.empty((len(positions) * 2, 2), dtype=np.float32)
    arrow_lines[0::2] = positions
    arrow_lines[1::2] = end_positions

    arrow_heads = np.column_stack((
        positions[:, 0],
        positions[:, 1],
        end_positions[:, 0],
        end_positions[:, 1],
    )).astype(np.float32)

    return arrow_lines, arrow_heads


def create_layer_view(layer, ctx, parent):
    if isinstance(layer, ScalarLayer):
        return ScalarLayerView(layer, ctx, parent)
    if isinstance(layer, VectorLayer):
        return VectorLayerView(layer, ctx, parent)

    raise TypeError(f"Unsupported layer type: {type(layer).__name__}")
