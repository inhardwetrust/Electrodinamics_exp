# plot.py
#
# A small multi-series line plot drawn with QPainter (no plotting library):
# grid, tick labels, legend, NaN gaps. Used for S-parameters and Z(f).
# Each curve has its own x (runs may cover different bands) and may be
# dimmed (an older run shown for comparison: thinner, faded).

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

COLORS = ["#ffb000", "#00c8ff", "#ff4fd8", "#7dff5a", "#ff6a3d", "#b18cff", "#ffffff", "#ffd27f"]
DIM_ALPHA = 90          # 0..255: curves of older runs


def color_of(index, dim=False):
    color = QColor(COLORS[index % len(COLORS)])
    if dim:
        color.setAlpha(DIM_ALPHA)
    return color


def nice_ticks(lo, hi, count=6):
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / count
    mag = 10 ** math.floor(math.log10(raw))
    step = min((s * mag for s in (1, 2, 2.5, 5, 10)), key=lambda s: abs(s - raw))
    first = math.ceil(lo / step) * step
    return [first + n * step for n in range(int((hi - first) / step + 1e-9) + 1)]


class LinePlot(QWidget):
    MARGIN_LEFT, MARGIN_RIGHT, MARGIN_TOP, MARGIN_BOTTOM = 56, 14, 12, 34

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self._curves = []          # [(label, x, y, color index, dim)]
        self.x_label = ""
        self.y_label = ""
        self.y_range = None
        self.marker_x = None

    def set_data(self, curves, x_label="", y_label="", y_range=None, marker_x=None):
        """curves: [(label, x, y, color index, dim)]; dim = an older run (faded)."""
        self._curves = [(label, np.asarray(x, dtype=float), np.asarray(y, dtype=float), color, dim)
                        for label, x, y, color, dim in curves]
        self.x_label, self.y_label, self.y_range = x_label, y_label, y_range
        self.marker_x = marker_x
        self.update()

    def clear(self):
        self._curves = []
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#16181d"))
        font = QFont(self.font())
        font.setPointSizeF(8.5)
        p.setFont(font)

        plot = QRectF(self.MARGIN_LEFT, self.MARGIN_TOP,
                      self.width() - self.MARGIN_LEFT - self.MARGIN_RIGHT,
                      self.height() - self.MARGIN_TOP - self.MARGIN_BOTTOM)

        if not self._curves:
            p.setPen(QColor("#777"))
            p.drawText(plot, Qt.AlignCenter, "no results shown: press Simulate, or tick a run in Project > Results")
            p.end()
            return

        x0 = min(float(x.min()) for _, x, _, _, _ in self._curves)
        x1 = max(float(x.max()) for _, x, _, _, _ in self._curves)
        if x1 <= x0:
            x1 = x0 + 1.0
        values = np.concatenate([y[np.isfinite(y)] for _, _, y, _, _ in self._curves] or [np.zeros(1)])
        if self.y_range is not None:
            y0, y1 = self.y_range
        else:
            y0, y1 = (float(values.min()), float(values.max())) if values.size else (0.0, 1.0)
            pad = 0.08 * max(y1 - y0, 1e-9)
            y0, y1 = y0 - pad, y1 + pad

        def px(x, y):
            return QPointF(plot.left() + (x - x0) / (x1 - x0) * plot.width(),
                           plot.bottom() - (y - y0) / (y1 - y0) * plot.height())

        # Grid and ticks.
        p.setPen(QPen(QColor("#2c3038"), 1))
        for t in nice_ticks(x0, x1):
            a = px(t, y0)
            p.drawLine(QPointF(a.x(), plot.top()), QPointF(a.x(), plot.bottom()))
        for t in nice_ticks(y0, y1):
            a = px(x0, t)
            p.drawLine(QPointF(plot.left(), a.y()), QPointF(plot.right(), a.y()))
        p.setPen(QColor("#9aa0aa"))
        for t in nice_ticks(x0, x1):
            a = px(t, y0)
            p.drawText(QRectF(a.x() - 30, plot.bottom() + 2, 60, 14), Qt.AlignCenter, f"{t:g}")
        for t in nice_ticks(y0, y1):
            a = px(x0, t)
            p.drawText(QRectF(0, a.y() - 7, self.MARGIN_LEFT - 6, 14), Qt.AlignRight | Qt.AlignVCenter, f"{t:g}")
        p.drawText(QRectF(plot.left(), self.height() - 16, plot.width(), 14), Qt.AlignCenter, self.x_label)
        p.save()
        p.translate(12, plot.center().y())
        p.rotate(-90)
        p.drawText(QRectF(-plot.height() / 2, -8, plot.height(), 14), Qt.AlignCenter, self.y_label)
        p.restore()
        p.setPen(QPen(QColor("#4a505a"), 1))
        p.drawRect(plot)

        # Curves (NaN = gap), clipped to the plot.
        p.setClipRect(plot)
        # Dimmed (older) curves first, so the newest run is drawn on top.
        for label, xs, y, color, dim in sorted(self._curves, key=lambda c: not c[4]):
            path = QPainterPath()
            pen_down = False
            for xv, yv in zip(xs, y):
                if not np.isfinite(yv):
                    pen_down = False
                    continue
                point = px(xv, yv)
                if pen_down:
                    path.lineTo(point)
                else:
                    path.moveTo(point)
                    pen_down = True
            p.setPen(QPen(color_of(color, dim), 1.2 if dim else 1.8))
            p.drawPath(path)

        if self.marker_x is not None:
            a = px(self.marker_x, y0)
            p.setPen(QPen(QColor("#e8e8e8"), 1, Qt.DashLine))
            p.drawLine(QPointF(a.x(), plot.top()), QPointF(a.x(), plot.bottom()))
            for _, xs, y, color, dim in self._curves:
                if not xs[0] <= self.marker_x <= xs[-1]:
                    continue
                yv = float(np.interp(self.marker_x, xs, y))
                if np.isfinite(yv):
                    p.setBrush(color_of(color, dim))
                    p.setPen(Qt.NoPen)
                    p.drawEllipse(px(self.marker_x, yv), 2.5 if dim else 3.5, 2.5 if dim else 3.5)
            p.setBrush(Qt.NoBrush)
        p.setClipping(False)

        # Legend.
        x, y = plot.right() - 8, plot.top() + 6
        for label, _, _, color, dim in self._curves:
            width = p.fontMetrics().horizontalAdvance(label) + 26
            p.setPen(QPen(color_of(color, dim), 2.5))
            p.drawLine(QPointF(x - width, y + 7), QPointF(x - width + 16, y + 7))
            p.setPen(QColor("#8a8f98") if dim else QColor("#d8dce2"))
            p.drawText(QRectF(x - width + 20, y, width - 20, 14), Qt.AlignLeft | Qt.AlignVCenter, label)
            y += 15
        p.end()
