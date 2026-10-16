# plot.py
#
# A small multi-series line plot drawn with QPainter (no plotting library):
# grid, tick labels, legend, NaN gaps. Used for S-parameters and Z(f).

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

COLORS = ["#ffb000", "#00c8ff", "#ff4fd8", "#7dff5a", "#ff6a3d", "#b18cff", "#ffffff", "#ffd27f"]


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
        self._x = None
        self._series = []          # [(label, y)]
        self.x_label = ""
        self.y_label = ""
        self.y_range = None
        self.marker_x = None
        self.colors = None         # per-series color indices (default: order)

    def set_data(self, x, series, x_label="", y_label="", y_range=None, marker_x=None, colors=None):
        self._x = np.asarray(x, dtype=float)
        self._series = [(label, np.asarray(y, dtype=float)) for label, y in series]
        self.x_label, self.y_label, self.y_range = x_label, y_label, y_range
        self.marker_x, self.colors = marker_x, colors
        self.update()

    def clear(self):
        self._x, self._series = None, []
        self.update()

    def _color(self, n):
        index = self.colors[n] if self.colors is not None else n
        return COLORS[index % len(COLORS)]

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

        if self._x is None or not self._series:
            p.setPen(QColor("#777"))
            p.drawText(plot, Qt.AlignCenter, "no results yet: press Simulate")
            p.end()
            return

        x0, x1 = float(self._x.min()), float(self._x.max())
        values = np.concatenate([y[np.isfinite(y)] for _, y in self._series] or [np.zeros(1)])
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
        for n, (label, y) in enumerate(self._series):
            path = QPainterPath()
            pen_down = False
            for xv, yv in zip(self._x, y):
                if not np.isfinite(yv):
                    pen_down = False
                    continue
                point = px(xv, yv)
                if pen_down:
                    path.lineTo(point)
                else:
                    path.moveTo(point)
                    pen_down = True
            p.setPen(QPen(QColor(self._color(n)), 1.8))
            p.drawPath(path)

        if self.marker_x is not None:
            a = px(self.marker_x, y0)
            p.setPen(QPen(QColor("#e8e8e8"), 1, Qt.DashLine))
            p.drawLine(QPointF(a.x(), plot.top()), QPointF(a.x(), plot.bottom()))
            for n, (_, y) in enumerate(self._series):
                yv = float(np.interp(self.marker_x, self._x, y))
                if np.isfinite(yv):
                    p.setBrush(QColor(self._color(n)))
                    p.setPen(Qt.NoPen)
                    p.drawEllipse(px(self.marker_x, yv), 3.5, 3.5)
            p.setBrush(Qt.NoBrush)
        p.setClipping(False)

        # Legend.
        x, y = plot.right() - 8, plot.top() + 6
        for n, (label, _) in enumerate(self._series):
            width = p.fontMetrics().horizontalAdvance(label) + 26
            p.setPen(QPen(QColor(self._color(n)), 2.5))
            p.drawLine(QPointF(x - width, y + 7), QPointF(x - width + 16, y + 7))
            p.setPen(QColor("#d8dce2"))
            p.drawText(QRectF(x - width + 20, y, width - 20, 14), Qt.AlignLeft | Qt.AlignVCenter, label)
            y += 15
        p.end()
