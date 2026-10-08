# qt_plot.py
#
# Minimal line plot drawn with QPainter (no plotting library needed).

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
