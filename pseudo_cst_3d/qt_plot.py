# qt_plot.py
#
# Minimal line plot drawn with QPainter (no plotting library needed).

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


class CurvePlot(QWidget):
    """
    Plots one signed curve y(x) and an optional envelope +-env(x).

    Used for the current along a wire: orange = instantaneous current,
    gray = amplitude envelope (the standing-wave shape).
    """

    MARGIN_LEFT, MARGIN_RIGHT, MARGIN_TOP, MARGIN_BOTTOM = 44, 10, 10, 26

    def __init__(self, x_label="s", y_label="I", parent=None):
        super().__init__(parent)
        self.x_label = x_label
        self.y_label = y_label
        self._x = self._y = self._env = None
        self.setMinimumHeight(170)

    def set_data(self, x, y, envelope=None, x_label=None):
        self._x, self._y, self._env = x, y, envelope
        if x_label is not None:
            self.x_label = x_label
        self.update()

    # -------------------------------------------------

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#111111"))

        plot = QRectF(
            self.MARGIN_LEFT, self.MARGIN_TOP,
            self.width() - self.MARGIN_LEFT - self.MARGIN_RIGHT,
            self.height() - self.MARGIN_TOP - self.MARGIN_BOTTOM,
        )
        p.setPen(QPen(QColor("#444444"), 1))
        p.drawRect(plot)

        if self._x is None or len(self._x) == 0:
            p.end()
            return

        x0, x1 = float(min(self._x)), float(max(self._x))
        pad = 0.05 * max(x1 - x0, 1e-9)
        x0, x1 = x0 - pad, x1 + pad

        peak = max(abs(float(v)) for v in self._y)
        if self._env is not None:
            peak = max(peak, max(float(v) for v in self._env))
        y_max = 1.1 * peak if peak > 0 else 1.0

        def to_px(x, y):
            return QPointF(
                plot.left() + (x - x0) / (x1 - x0) * plot.width(),
                plot.center().y() - y / y_max * 0.5 * plot.height(),
            )

        # zero line
        p.setPen(QPen(QColor("#555555"), 1, Qt.PenStyle.DashLine))
        p.drawLine(to_px(x0, 0.0), to_px(x1, 0.0))

        def polyline(ys, color, width):
            path = QPainterPath(to_px(self._x[0], ys[0]))
            for x, y in zip(self._x[1:], ys[1:]):
                path.lineTo(to_px(x, y))
            p.setPen(QPen(color, width))
            p.drawPath(path)

        if self._env is not None:
            gray = QColor("#9a9a9a")
            polyline(list(self._env), gray, 1.5)
            polyline([-v for v in self._env], gray, 1.5)

        orange = QColor(255, 158, 26)
        polyline(list(self._y), orange, 2.0)
        p.setBrush(orange)
        for x, y in zip(self._x, self._y):
            p.drawEllipse(to_px(x, y), 2.5, 2.5)

        # labels
        p.setPen(QColor("#bbbbbb"))
        p.drawText(QRectF(0, plot.top() - 4, self.MARGIN_LEFT - 4, 16),
                   Qt.AlignmentFlag.AlignRight, f"{y_max:.3g}")
        p.drawText(QRectF(0, plot.bottom() - 12, self.MARGIN_LEFT - 4, 16),
                   Qt.AlignmentFlag.AlignRight, f"{-y_max:.3g}")
        lo, hi = float(min(self._x)), float(max(self._x))
        p.drawText(QRectF(plot.left(), plot.bottom() + 4, plot.width(), 18),
                   Qt.AlignmentFlag.AlignCenter,
                   f"{self.x_label}: {lo:+.3f} ... {hi:+.3f}")
        p.end()


class SpectrumPlot(QWidget):
    """
    Several curves over a common x axis (e.g. R(f) and X(f)), with NaN gaps,
    a zero line, min / max tick labels and an optional vertical marker.
    """

    MARGIN_LEFT, MARGIN_RIGHT, MARGIN_TOP, MARGIN_BOTTOM = 50, 10, 18, 26

    def __init__(self, x_label="f", parent=None):
        super().__init__(parent)
        self.x_label = x_label
        self._x = None
        self._series = []
        self._marker = None
        self._y_range = None
        self.setMinimumHeight(160)

    def set_data(self, x, series, marker=None, y_range=None):
        """series: [(values, QColor-compatible color, name)]."""
        self._x = np.asarray(x, dtype=float)
        self._series = [(np.asarray(v, dtype=float), QColor(c), name) for v, c, name in series]
        self._marker = marker
        self._y_range = y_range
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#111111"))

        plot = QRectF(
            self.MARGIN_LEFT, self.MARGIN_TOP,
            self.width() - self.MARGIN_LEFT - self.MARGIN_RIGHT,
            self.height() - self.MARGIN_TOP - self.MARGIN_BOTTOM,
        )
        p.setPen(QPen(QColor("#444444"), 1))
        p.drawRect(plot)

        values = [v[np.isfinite(v)] for v, _, _ in self._series]
        values = np.concatenate(values) if values else np.array([])
        if self._x is None or values.size == 0:
            p.setPen(QColor("#888888"))
            p.drawText(plot, Qt.AlignmentFlag.AlignCenter, "no data yet - run the simulation")
            p.end()
            return

        x0, x1 = float(self._x[0]), float(self._x[-1])
        if self._y_range is not None:
            y0, y1 = self._y_range
        else:
            y0, y1 = float(values.min()), float(values.max())
            pad = 0.08 * max(y1 - y0, 1e-12)
            y0, y1 = y0 - pad, y1 + pad

        def to_px(x, y):
            return QPointF(
                plot.left() + (x - x0) / (x1 - x0) * plot.width(),
                plot.bottom() - (y - y0) / (y1 - y0) * plot.height(),
            )

        p.setClipRect(plot)

        if y0 < 0.0 < y1:
            p.setPen(QPen(QColor("#555555"), 1, Qt.PenStyle.DashLine))
            p.drawLine(to_px(x0, 0.0), to_px(x1, 0.0))

        if self._marker is not None and x0 <= self._marker <= x1:
            p.setPen(QPen(QColor("#6a8cff"), 1, Qt.PenStyle.DotLine))
            p.drawLine(to_px(self._marker, y0), to_px(self._marker, y1))

        for v, color, _ in self._series:
            p.setPen(QPen(color, 2.0))
            path, drawing = QPainterPath(), False
            for x, y in zip(self._x, v):
                if not np.isfinite(y):
                    drawing = False
                    continue
                pt = to_px(x, min(max(y, y0), y1))
                if drawing:
                    path.lineTo(pt)
                else:
                    path.moveTo(pt)
                    drawing = True
            p.drawPath(path)

        p.setClipping(False)

        # labels: y range, x range, legend
        p.setPen(QColor("#bbbbbb"))
        p.drawText(QRectF(0, plot.top() - 6, self.MARGIN_LEFT - 4, 16), Qt.AlignmentFlag.AlignRight, f"{y1:.3g}")
        p.drawText(QRectF(0, plot.bottom() - 10, self.MARGIN_LEFT - 4, 16), Qt.AlignmentFlag.AlignRight, f"{y0:.3g}")
        p.drawText(QRectF(plot.left(), plot.bottom() + 4, plot.width(), 18), Qt.AlignmentFlag.AlignLeft, f"{x0:.3g}")
        p.drawText(QRectF(plot.left(), plot.bottom() + 4, plot.width(), 18), Qt.AlignmentFlag.AlignRight, f"{x1:.3g}")
        p.drawText(QRectF(plot.left(), plot.bottom() + 4, plot.width(), 18), Qt.AlignmentFlag.AlignCenter, self.x_label)

        x_text = plot.left() + 4
        for _, color, name in self._series:
            p.setPen(color)
            p.drawText(QPointF(x_text, plot.top() - 5), name)
            x_text += 12 + p.fontMetrics().horizontalAdvance(name)
        p.end()
