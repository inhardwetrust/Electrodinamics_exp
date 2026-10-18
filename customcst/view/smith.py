# smith.py
#
# Smith chart (QPainter): the unit circle of Gamma, constant-r circles and
# constant-x arcs (normalized to the reference R), reflection traces and a
# marker dot per trace.

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from view.plot import color_of

R_CIRCLES = (0.0, 0.2, 0.5, 1.0, 2.0, 5.0)
X_ARCS = (0.2, 0.5, 1.0, 2.0, 5.0)


class SmithChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(240)
        self._traces = []          # [(label, gamma array, color index, dim, marker index or None)]

    def set_data(self, traces):
        """traces: [(label, gamma, color index, dim, marker index or None)]."""
        self._traces = list(traces)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#16181d"))
        font = QFont(self.font())
        font.setPointSizeF(8)
        p.setFont(font)

        size = min(self.width(), self.height()) - 30
        cx, cy = self.width() / 2, self.height() / 2
        rad = size / 2

        def px(g):
            return QPointF(cx + g.real * rad, cy - g.imag * rad)

        p.setClipRect(QRectF(cx - rad - 1, cy - rad - 1, 2 * rad + 2, 2 * rad + 2))
        grid = QPen(QColor("#2f343d"), 1)
        p.setPen(grid)

        # Constant r: center (r/(1+r), 0), radius 1/(1+r).
        for r in R_CIRCLES:
            c, rr = r / (1 + r), 1 / (1 + r)
            p.drawEllipse(px(complex(c, 0)), rr * rad, rr * rad)

        # Constant x: center (1, 1/x), radius 1/x, clipped to the unit disk.
        disk = QPainterPath()
        disk.addEllipse(QPointF(cx, cy), rad, rad)
        p.setClipPath(disk)
        for x in X_ARCS:
            for sign in (1, -1):
                c = complex(1, sign / x)
                p.drawEllipse(px(c), rad / x, rad / x)
        p.drawLine(QPointF(cx - rad, cy), QPointF(cx + rad, cy))      # x = 0
        p.setClipping(False)

        p.setPen(QPen(QColor("#5a606b"), 1.5))
        p.drawEllipse(QPointF(cx, cy), rad, rad)

        p.setPen(QColor("#8a909a"))
        for r in R_CIRCLES[1:]:
            g = (r - 1) / (r + 1)
            p.drawText(QRectF(px(complex(g, 0)).x() + 2, cy + 1, 30, 12), Qt.AlignLeft, f"{r:g}")

        # Traces and marker dots.
        for label, gamma, color, dim, marker in sorted(self._traces, key=lambda t: not t[3]):
            path = QPainterPath()
            for n, g in enumerate(gamma):
                (path.lineTo if n else path.moveTo)(px(g))
            p.setPen(QPen(color_of(color, dim), 1.2 if dim else 1.8))
            p.drawPath(path)
            if marker is not None and 0 <= marker < len(gamma):
                p.setBrush(color_of(color, dim))
                p.drawEllipse(px(gamma[marker]), 3 if dim else 4, 3 if dim else 4)
                p.setBrush(Qt.NoBrush)

        # Legend (top left).
        y = 6
        for label, _, color, dim, _ in self._traces:
            p.setPen(QPen(color_of(color, dim), 2.5))
            p.drawLine(QPointF(8, y + 7), QPointF(24, y + 7))
            p.setPen(QColor("#8a8f98") if dim else QColor("#d8dce2"))
            p.drawText(QRectF(28, y, 260, 14), Qt.AlignLeft | Qt.AlignVCenter, label)
            y += 15
        p.end()
