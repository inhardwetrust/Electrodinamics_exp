# renderer.py
#
# Heatmap of an array with a cell grid, cell indices and (for small arrays)
# the value written in every cell. Works for 1D now and 2D arrays later:
# cell (row r, column c) is the world rectangle [c, c+1] x [r h, (r+1) h],
# h = cell height (1 for square cells; taller for long 1D arrays so the
# strip stays visible).

import numpy as np

from vispy import app, scene

# "heat": black (low) -> green -> yellow -> orange -> red (high).
_HEAT_STOPS = np.array([0.0, 0.15, 0.35, 0.6, 0.8, 1.0])
_HEAT_RGB = np.array([
    [0.00, 0.00, 0.00],
    [0.00, 0.25, 0.12],
    [0.05, 0.75, 0.10],
    [0.95, 0.95, 0.10],
    [1.00, 0.55, 0.05],
    [1.00, 0.05, 0.00],
])


# "signed": purple (negative) - black (zero) - yellow (positive).
_SIGNED_STOPS = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
_SIGNED_RGB = np.array([
    [0.80, 0.40, 1.00],
    [0.38, 0.08, 0.55],
    [0.00, 0.00, 0.00],
    [0.55, 0.45, 0.02],
    [1.00, 0.92, 0.20],
])

PALETTES = {"heat": (_HEAT_STOPS, _HEAT_RGB), "signed": (_SIGNED_STOPS, _SIGNED_RGB)}


def palette_colors(t, name="heat"):
    """t in [0, 1] (any shape) -> RGBA (..., 4) with the named palette."""
    stops, rgb_table = PALETTES[name]
    t = np.clip(np.nan_to_num(t), 0.0, 1.0)
    rgb = np.stack([np.interp(t, stops, rgb_table[:, c]) for c in range(3)], axis=-1)
    return np.concatenate([rgb, np.ones(t.shape + (1,))], axis=-1).astype(np.float32)


def heat_colors(t):
    return palette_colors(t, "heat")


def round_values(values, digits, fmt):
    """
    Round like the cell labels:
        "fixed"        digits after the decimal point   (0.000496 -> 0.000)
        "significant"  digits significant figures       (0.000496 -> 0.000496)
    """
    values = np.asarray(values, dtype=np.float64)
    if fmt == "fixed":
        return np.round(values, digits)

    out = np.zeros_like(values)
    nonzero = values != 0.0
    v = values[nonzero]
    scale = 10.0 ** (digits - 1 - np.floor(np.log10(np.abs(v))))
    out[nonzero] = np.round(v * scale) / scale
    return out


def format_value(value, digits, fmt):
    return f"{value:.{digits}f}" if fmt == "fixed" else f"{value:.{digits}g}"


def _skip_redundant_qt_swap(canvas):
    """
    Qt6 QOpenGLWidget composites the frame itself; VisPy's extra
    swapBuffers() blocks for ~2 frames on some drivers (seen on AMD).
    """
    try:
        from PySide6.QtOpenGLWidgets import QOpenGLWidget
    except ImportError:
        return
    if isinstance(canvas._backend, QOpenGLWidget):
        canvas._backend._vispy_swap_buffers = lambda: None


class ArrayRenderer:
    # Values are written into the cells up to this many cells.
    MAX_VALUE_LABELS = 60
    MIN_LABEL_CELL_PX = 42
    MAX_INDEX_LABELS = 60
    GRID_COLOR = (0.55, 0.55, 0.55, 1.0)
    MARK_COLOR = (0.3, 0.8, 1.0, 1.0)

    def __init__(self, canvas_size=(1000, 400), show=True):
        self.canvas = scene.SceneCanvas(keys="interactive", size=canvas_size,
                                        bgcolor="black", show=show)
        _skip_redundant_qt_swap(self.canvas)

        self.view = self.canvas.central_widget.add_view()
        self.view.camera = scene.PanZoomCamera(aspect=1.0)

        self.image = scene.visuals.Image(np.zeros((1, 1, 4), np.float32),
                                         interpolation="nearest", parent=self.view.scene)
        self.grid = scene.visuals.Line(connect="segments", color=self.GRID_COLOR,
                                       width=1, parent=self.view.scene)
        # Outlines of the cells forced by the set ("feed").
        self.marks = scene.visuals.Line(connect="segments", color=self.MARK_COLOR,
                                        width=3, parent=self.view.scene)
        self.marks.visible = False

        self.values = scene.visuals.Text("", anchor_x="center", anchor_y="center",
                                         parent=self.view.scene)
        self.indices = scene.visuals.Text("", color=(0.7, 0.7, 0.7, 1.0), anchor_x="center",
                                          anchor_y="top", parent=self.view.scene)

        # Flat 2D layers: draw order decides, no depth test.
        for visual in (self.image, self.grid, self.marks, self.values, self.indices):
            visual.set_gl_state("translucent", depth_test=False)

        self.shape = None
        self.cell_h = 1.0

        # Cell labels and how colors relate to them (see set_number_format).
        self.digits = 3
        self.number_format = "fixed"
        self.color_mode = "match numbers"
        self.palette = "heat"
        self.status_text = ""

    # -------------------------------------------------

    def set_shape(self, shape):
        """New array size: rebuild the grid and labels, fit the view."""
        rows, cols = (1, shape[0]) if len(shape) == 1 else shape[:2]
        self.shape = (rows, cols)

        # A long 1D strip of square cells would be a thin line on screen.
        self.cell_h = max(1.0, cols / 12.0) if rows == 1 else 1.0
        self.image.transform = scene.STTransform(scale=(1.0, self.cell_h))
        h = self.cell_h

        segments = []
        for c in range(cols + 1):
            segments += [[c, 0], [c, rows * h]]
        for r in range(rows + 1):
            segments += [[0, r * h], [cols, r * h]]
        # Many cells: a dimmer grid, so it does not drown the colors.
        alpha = 1.0 if max(rows, cols) <= 15 else 0.35
        self.grid.set_data(pos=np.array(segments, np.float32),
                           color=self.GRID_COLOR[:3] + (alpha,))
        # A huge array: the grid lines would be denser than the pixels.
        self.grid.visible = cols <= 100 and rows <= 100

        self.indices.visible = rows == 1 and cols <= self.MAX_INDEX_LABELS
        if self.indices.visible:
            self.indices.text = [str(c) for c in range(cols)]
            self.indices.pos = np.array([[c + 0.5, -0.3 * h] for c in range(cols)], np.float32)

        self.values.visible = rows * cols <= self.MAX_VALUE_LABELS
        self.reset_view()

    def reset_view(self):
        rows, cols = self.shape
        h = self.cell_h
        self.view.camera.rect = (-0.5, -0.9 * h, cols + 1.0, (rows + 1.8) * h)

        # Text sizes follow the cell size on screen at the fitted view.
        width_px, height_px = (max(v, 1) for v in self.canvas.size)
        cell_px = min(width_px / (cols + 1.0), height_px / ((rows + 1.8) * h))
        self.values.font_size = float(np.clip(cell_px * 0.16, 5, 18))

        # Numbers only where they fit: a cell must be wide enough on screen.
        self.values.visible = (rows * cols <= self.MAX_VALUE_LABELS
                               and cell_px >= self.MIN_LABEL_CELL_PX)
        self.indices.font_size = float(np.clip(cell_px * 0.11, 5, 12))

    COLOR_MODES = ("match numbers", "continuous")
    PALETTES = tuple(PALETTES)
    NUMBER_FORMATS = ("fixed", "significant")

    def set_number_format(self, digits=None, number_format=None, color_mode=None):
        """
        color_mode "match numbers": the color comes from the SAME rounded
        value that is written in the cell, so equal numbers always have equal
        colors (the heatmap is quantized to the label precision).
        """
        if digits is not None:
            self.digits = int(digits)
        if number_format is not None:
            self.number_format = number_format
        if color_mode is not None:
            self.color_mode = color_mode

    def set_state(self, state, clim):
        rows, cols = self.shape
        data = np.asarray(state, dtype=np.float64).reshape(rows, cols)
        # + 0.0 turns -0.0 into 0.0 (no "-0.000" labels).
        shown = round_values(data, self.digits, self.number_format) + 0.0
        colored = shown if self.color_mode == "match numbers" else data

        lo, hi = clim
        t = (colored - lo) / (hi - lo)
        rgba = palette_colors(t, self.palette)
        self.image.set_data(rgba)

        if self.values.visible:
            centers, texts, colors = [], [], []
            for r in range(rows):
                for c in range(cols):
                    centers.append([c + 0.5, (r + 0.5) * self.cell_h])
                    texts.append(format_value(shown[r, c], self.digits, self.number_format))
                    # Dark text on bright cells, light text on dark ones.
                    bright = float(rgba[r, c, :3] @ np.array([0.3, 0.6, 0.1])) > 0.45
                    colors.append((0, 0, 0, 1) if bright else (1, 1, 1, 1))
            self.values.text = texts
            self.values.pos = np.array(centers, np.float32)
            self.values.color = np.array(colors, np.float32)

        self.canvas.update()

    def set_marks(self, mask):
        """Outline every cell where mask is True (the set / feed cells)."""
        rows, cols = self.shape
        mask = np.asarray(mask).reshape(rows, cols)
        h, inset = self.cell_h, 0.06
        segments = []

        for r, c in zip(*np.nonzero(mask)):
            x0, x1 = c + inset, c + 1 - inset
            y0, y1 = r * h + inset * h, (r + 1) * h - inset * h
            segments += [[x0, y0], [x1, y0], [x1, y0], [x1, y1],
                         [x1, y1], [x0, y1], [x0, y1], [x0, y0]]

        self.marks.visible = bool(segments)
        if segments:
            # Many forced cells: thinner outlines, so they do not hide the colors.
            width = 3 if len(segments) <= 8 * 12 else 1.5
            self.marks.set_data(pos=np.array(segments, np.float32), width=width)
        self.canvas.update()

    def set_status(self, text):
        self.status_text = text
        self.canvas.title = "atomic FDTD - " + text

    def run(self):
        app.run()
