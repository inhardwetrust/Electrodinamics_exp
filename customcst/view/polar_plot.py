# polar_plot.py
#
# A polar plot of patterns in dB drawn with QPainter, in the style of
# plot.py's LinePlot: 0 degrees at the top, angles clockwise, the radius
# from `top` (the outer circle) down to `top - span` (the center). Values
# below the center are drawn at the center. Curves of older runs are dimmed.
# A marker angle (the cut's maximum) is drawn as a thin radial line.

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from view.plot import color_of

RING_STEP = 10.0        # dB between the grid circles
SPOKE_STEP = 30         # degrees between the grid spokes


class PolarPlot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 320)
        self._curves = []           # [(label, angles deg, values dB, color index, dim)]
        self.top, self.span = 10.0, 40.0
        self.unit = "dBi"
        self.side_labels = ("", "")         # (right half, left half), e.g. ("φ = 0°", "φ = 180°")
        self.marker = None                  # degrees, or None
        self.empty_text = "no far field shown"

    def set_data(self, curves, top, span, unit="dBi", side_labels=("", ""), marker=None):
        """curves: [(label, angles deg, values dB, color index, dim)]; the closed circle is drawn."""
        self._curves = [(label, np.asarray(a, dtype=float), np.asarray(v, dtype=float), color, dim)
                        for label, a, v, color, dim in curves]
        self.top, self.span, self.unit = float(top), float(span), unit
        self.side_labels, self.marker = side_labels, marker
        self.update()

    def clear(self):
        self._curves = []
        self.update()

    def _point(self, center, radius, angle, value):
        r = radius * min(max((value - (self.top - self.span)) / self.span, 0.0), 1.0)
        a = math.radians(angle)
        return QPointF(center.x() + r * math.sin(a), center.y() - r * math.cos(a))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#16181d"))
        font = QFont(self.font())
        font.setPointSizeF(8.5)
        p.setFont(font)

        if not self._curves:
            p.setPen(QColor("#777"))
            p.drawText(QRectF(self.rect()), Qt.AlignCenter, self.empty_text)
            p.end()
            return

        legend_h = 16 * len(self._curves) + 6
        size = min(self.width() - 60, self.height() - 50 - legend_h)
        radius = max(size, 60) / 2.0
        center = QPointF(self.width() / 2.0, 24 + radius)

        # grid: circles every RING_STEP dB, spokes every SPOKE_STEP degrees
        grid = QPen(QColor("#3a3f4a"), 1)
        p.setPen(grid)
        rings = int(round(self.span / RING_STEP))
        for n in range(1, rings + 1):
            r = radius * n / rings
            p.drawEllipse(center, r, r)
        for angle in range(0, 360, SPOKE_STEP):
            p.drawLine(center, self._point(center, radius, angle, self.top))
        p.setPen(QColor("#8a8f99"))
        for n in range(1, rings + 1):
            value = self.top - self.span + n * RING_STEP
            r = radius * n / rings
            p.drawText(QPointF(center.x() + 3, center.y() - r + 11), f"{value:g}")
        for angle in range(0, 360, SPOKE_STEP):
            q = self._point(center, radius + 14, angle, self.top)
            p.drawText(QRectF(q.x() - 18, q.y() - 8, 36, 16), Qt.AlignCenter, f"{angle}°")
        right, left = self.side_labels
        p.drawText(QRectF(center.x() + radius - 80, center.y() + radius - 4, 100, 16), Qt.AlignRight, right)
        p.drawText(QRectF(center.x() - radius - 20, center.y() + radius - 4, 100, 16), Qt.AlignLeft, left)
        p.drawText(QRectF(0, 2, self.width() - 6, 16), Qt.AlignRight, self.unit)

        if self.marker is not None:
            p.setPen(QPen(QColor("#888"), 1, Qt.DashLine))
            p.drawLine(center, self._point(center, radius, self.marker, self.top))

        for label, angles, values, color, dim in self._curves:
            path = QPainterPath()
            for i, (a, v) in enumerate(zip(angles, values)):
                q = self._point(center, radius, a, v)
                if i == 0:
                    path.moveTo(q)
                else:
                    path.lineTo(q)
            path.closeSubpath()
            p.setPen(QPen(color_of(color, dim), 1.0 if dim else 2.0))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)

        y = center.y() + radius + 30
        for label, _, _, color, dim in self._curves:
            p.setPen(QPen(color_of(color, dim), 2))
            p.drawLine(QPointF(12, y - 4), QPointF(32, y - 4))
            p.setPen(QColor("#aaa" if dim else "#ddd"))
            p.drawText(QPointF(38, y), label)
            y += 16
        p.end()
