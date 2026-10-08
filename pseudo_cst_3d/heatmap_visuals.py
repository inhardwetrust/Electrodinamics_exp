# heatmap_visuals.py

import numpy as np

from vispy import gloo
from vispy.scene.visuals import create_visual_node
from vispy.visuals import Visual

from color_mapping import LUT_SIZE


VERTEX_SHADER = """
attribute vec2 a_position;
varying vec2 v_world;

void main() {
    v_world = a_position;
    gl_Position = $transform(vec4(a_position, 0.0, 1.0));
}
"""

FRAGMENT_SHADER = """
varying vec2 v_world;
uniform vec2 u_clim;        // limits of the SCALED value
uniform sampler2D u_lut;    // LUT_SIZE x 1 palette

__FIELD__

float scale_value(float v) {
    __SCALE__
}

void main() {
    float v = scale_value(field_scalar(v_world));
    float t = clamp((v - u_clim.x) / (u_clim.y - u_clim.x), 0.0, 1.0);

    // Hit texel centers: t = 0 -> first entry, t = 1 -> last entry.
    float u = t * (__N__ - 1.0) / __N__ + 0.5 / __N__;
    gl_FragColor = texture2D(u_lut, vec2(u, 0.5));
}
"""


class FieldShaderHeatmapVisual(Visual):
    """
    Scalar heatmap evaluated per pixel on the GPU.

    One quad covering the current view; the fragment shader evaluates
    `field_scalar(p)` (provided by the FieldSource), applies the scale and
    colors it through a LUT texture. Resolution is always one screen pixel.
    """

    def __init__(self, glsl_field, glsl_scale_body, lut, clim):
        fragment = (
            FRAGMENT_SHADER
            .replace("__FIELD__", glsl_field)
            .replace("__SCALE__", glsl_scale_body)
            .replace("__N__", "%.1f" % LUT_SIZE)
        )

        Visual.__init__(self, vcode=VERTEX_SHADER, fcode=fragment)

        self._vbo = gloo.VertexBuffer(np.zeros((4, 2), dtype=np.float32))
        self.shared_program["a_position"] = self._vbo

        self.shared_program["u_lut"] = _lut_texture(lut)

        self.set_clim(clim)

        self._draw_mode = "triangle_strip"
        self.set_gl_state("translucent", depth_test=False)

    def set_clim(self, clim):
        self.shared_program["u_clim"] = tuple(float(v) for v in clim)
        self.update()

    def set_rect(self, x_min, x_max, y_min, y_max):
        self._vbo.set_data(np.array([
            [x_min, y_min],
            [x_max, y_min],
            [x_min, y_max],
            [x_max, y_max],
        ], dtype=np.float32))
        self.update()

    def _prepare_transforms(self, view):
        view.view_program.vert["transform"] = view.get_transform()

    def _prepare_draw(self, view):
        return True

    def _compute_bounds(self, axis, view):
        return None


FieldShaderHeatmap = create_visual_node(FieldShaderHeatmapVisual)


GRID_VERTEX_SHADER = """
attribute vec2 a_position;
attribute vec2 a_texcoord;
varying vec2 v_texcoord;

void main() {
    v_texcoord = a_texcoord;
    gl_Position = $transform(vec4(a_position, 0.0, 1.0));
}
"""

GRID_FRAGMENT_SHADER = """
varying vec2 v_texcoord;
uniform sampler2D u_data;   // raw values, r32f, linear filtering
uniform vec2 u_clim;
uniform sampler2D u_lut;

float scale_value(float v) {
    __SCALE__
}

void main() {
    float v = scale_value(texture2D(u_data, v_texcoord).r);
    float t = clamp((v - u_clim.x) / (u_clim.y - u_clim.x), 0.0, 1.0);
    float u = t * (__N__ - 1.0) / __N__ + 0.5 / __N__;
    gl_FragColor = texture2D(u_lut, vec2(u, 0.5));
}
"""


def _lut_texture(lut):
    lut_rgba8 = (np.clip(lut, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    return gloo.Texture2D(
        lut_rgba8[np.newaxis],
        interpolation="linear",
        wrapping="clamp_to_edge",
    )


class GridScalarHeatmapVisual(Visual):
    """
    Scalar heatmap of a solver's own grid (GridArray), colored on the GPU.

    Raw values go to a float texture as they are; scale, color limits and
    palette are applied in the fragment shader. Per frame this costs one
    texture upload (500 x 500 -> 1 MB) and no CPU resampling, which is what
    time-stepping solvers need.

    The quad spans exactly the sample positions [x0, x0 + (nx-1) dx] and the
    texture coordinates hit the first/last texel centers, so the GPU's
    bilinear filtering matches GridArray.sample() (nothing drawn outside).
    """

    def __init__(self, glsl_scale_body, lut, clim):
        fragment = (
            GRID_FRAGMENT_SHADER
            .replace("__SCALE__", glsl_scale_body)
            .replace("__N__", "%.1f" % LUT_SIZE)
        )

        Visual.__init__(self, vcode=GRID_VERTEX_SHADER, fcode=fragment)

        self._pos_vbo = gloo.VertexBuffer(np.zeros((4, 2), dtype=np.float32))
        self._tex_vbo = gloo.VertexBuffer(np.zeros((4, 2), dtype=np.float32))
        self.shared_program["a_position"] = self._pos_vbo
        self.shared_program["a_texcoord"] = self._tex_vbo
        self.shared_program["u_lut"] = _lut_texture(lut)

        self._data_texture = None
        self._geometry = None

        self.set_clim(clim)

        self._draw_mode = "triangle_strip"
        self.set_gl_state("translucent", depth_test=False)

    def set_clim(self, clim):
        self.shared_program["u_clim"] = tuple(float(v) for v in clim)
        self.update()

    def set_grid(self, grid_array):
        values = np.ascontiguousarray(grid_array.values, dtype=np.float32)
        ny, nx = values.shape

        if self._data_texture is not None and self._data_texture.shape[:2] == (ny, nx):
            self._data_texture.set_data(values)
        else:
            self._data_texture = gloo.Texture2D(
                values,
                internalformat="r32f",
                interpolation="linear",
                wrapping="clamp_to_edge",
            )
            self.shared_program["u_data"] = self._data_texture

        geometry = (grid_array.x0, grid_array.y0, grid_array.dx, grid_array.dy, nx, ny)

        if geometry != self._geometry:
            x0, y0, dx, dy = grid_array.x0, grid_array.y0, grid_array.dx, grid_array.dy
            x1 = x0 + (nx - 1) * dx
            y1 = y0 + (ny - 1) * dy

            self._pos_vbo.set_data(np.array([
                [x0, y0], [x1, y0], [x0, y1], [x1, y1],
            ], dtype=np.float32))

            u0, u1 = 0.5 / nx, 1.0 - 0.5 / nx
            v0, v1 = 0.5 / ny, 1.0 - 0.5 / ny

            # Texture row 0 = values[0] = lowest y.
            self._tex_vbo.set_data(np.array([
                [u0, v0], [u1, v0], [u0, v1], [u1, v1],
            ], dtype=np.float32))

            self._geometry = geometry

        self.update()

    def _prepare_transforms(self, view):
        view.view_program.vert["transform"] = view.get_transform()

    def _prepare_draw(self, view):
        return self._data_texture is not None

    def _compute_bounds(self, axis, view):
        return None


GridScalarHeatmap = create_visual_node(GridScalarHeatmapVisual)
