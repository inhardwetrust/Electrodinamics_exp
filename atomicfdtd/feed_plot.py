# feed_plot.py
#
# Small step-by-step plot of the feed waveform for the Feed box: one stem per
# step n, the current step marked. Plain QPainter - no plotting library.

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

# Curve colors in the order the model returns its curves.
COLORS = (QColor(0, 200, 255), QColor(255, 150, 0))
FREE = QColor(120, 120, 120)
NOW = QColor(255, 60, 60)


class FeedPlot(QWidget):
    """
    set_data(steps, curves, now): steps = int array, curves = [(label, values)]
    with NaN where the cell is free (not forced), now = the current step.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(170)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._steps = np.arange(0)
        self._curves = []
        self._now = 0

    def set_data(self, steps, curves, now):
        self._steps, self._curves, self._now = np.asarray(steps), list(curves), int(now)
        self.update()

    # -------------------------------------------------

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(20, 20, 20))
        font = QFont(self.font()); font.setPointSizeF(8.0); p.setFont(font)

        steps = self._steps
        if steps.size == 0 or not self._curves:
            return

        # Value range over all curves (always including 0).
        finite = np.concatenate([v[np.isfinite(v)] for _, v in self._curves] + [np.zeros(1)])
        lo, hi = float(finite.min()), float(finite.max())
        if hi - lo < 1e-12:
            lo, hi = lo - 1.0, hi + 1.0
        pad = 0.1 * (hi - lo)
        lo, hi = lo - pad, hi + pad

        legend_h = 16 * len(self._curves) + 4
        plot = QRectF(40, 6 + legend_h, self.width() - 48, self.height() - 26 - legend_h)
        n0, n1 = int(steps[0]), int(steps[-1])

        def x_of(n):
            return plot.left() + (n - n0 + 0.5) / (n1 - n0 + 1) * plot.width()

        def y_of(v):
            return plot.bottom() - (v - lo) / (hi - lo) * plot.height()

        # Axes: frame, zero line, value ticks, step ticks.
        p.setPen(QPen(QColor(70, 70, 70)))
        p.drawRect(plot)
        p.setPen(QPen(QColor(110, 110, 110), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(plot.left(), y_of(0.0)), QPointF(plot.right(), y_of(0.0)))
        p.setPen(QColor(170, 170, 170))
        for v in (lo + pad, 0.0, hi - pad):
            p.drawText(QRectF(0, y_of(v) - 7, 36, 14),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{v:.3g}")
        tick = _nice_tick(n1 - n0 + 1)
        for n in range(int(math.ceil(n0 / tick) * tick), n1 + 1, tick):
            p.drawText(QRectF(x_of(n) - 20, plot.bottom() + 2, 40, 14),
                       Qt.AlignmentFlag.AlignHCenter, str(n))

        # Stems, one per step. Free steps (NaN) get a small gray tick on zero.
        dot = max(1.5, min(3.5, 0.3 * plot.width() / (n1 - n0 + 1)))
        for i, (label, values) in enumerate(self._curves):
            color = COLORS[i % len(COLORS)]
            offset = (i - (len(self._curves) - 1) / 2) * dot * 1.2   # side by side
            for n, v in zip(steps, values):
                x = x_of(n) + offset
                if not np.isfinite(v):
                    if i == 0:
                        p.setPen(QPen(FREE, 1))
                        p.drawLine(QPointF(x, y_of(0) - 2), QPointF(x, y_of(0) + 2))
                    continue
                p.setPen(QPen(color, 1))
                p.drawLine(QPointF(x, y_of(0)), QPointF(x, y_of(v)))
                p.setBrush(color)
                p.drawEllipse(QPointF(x, y_of(v)), dot, dot)
            p.setBrush(Qt.BrushStyle.NoBrush)

        # Current step: vertical line + the values at it.
        if n0 <= self._now <= n1:
            p.setPen(QPen(NOW, 1.5))
            p.drawLine(QPointF(x_of(self._now), plot.top()), QPointF(x_of(self._now), plot.bottom()))

        for i, (label, values) in enumerate(self._curves):
            k = self._now - n0
            v = values[k] if 0 <= k < len(values) else np.nan
            text = f"{label}:  n={self._now}  " + (f"{v:+.4f}" if np.isfinite(v) else "free")
            p.setPen(COLORS[i % len(COLORS)])
            p.drawText(QRectF(6, 4 + 16 * i, self.width() - 12, 16), Qt.AlignmentFlag.AlignLeft, text)
        p.end()


def _nice_tick(span):
    """A round tick step giving ~6 labels."""
    raw = span / 6.0
    for m in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000):
        if m >= raw:
            return m
    return 10 ** int(math.ceil(math.log10(raw)))
