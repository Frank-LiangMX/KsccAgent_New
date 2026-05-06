"""Small vector toolbar / tree icons (theme-aware default stroke)."""

from typing import Optional

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from .palette import ICON_FG_DEFAULT


def quark_icon(kind: str, size: int = 18, fg: Optional[str] = None) -> QIcon:
    """Draw monochrome icons with optional stroke color."""
    fg = fg or ICON_FG_DEFAULT
    c = QColor(fg)
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    w = float(size)
    pen = QPen(c)
    pen.setWidthF(max(1.15, w * 0.07))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)

    if kind == "folder":
        path = QPainterPath()
        path.moveTo(w * 0.16, w * 0.36)
        path.lineTo(w * 0.40, w * 0.36)
        path.lineTo(w * 0.48, w * 0.26)
        path.lineTo(w * 0.84, w * 0.26)
        path.lineTo(w * 0.84, w * 0.80)
        path.lineTo(w * 0.16, w * 0.80)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "file":
        m = w * 0.24
        p.drawRect(int(m), int(w * 0.14), int(w - 2 * m), int(w * 0.72))
        p.drawLine(int(w * 0.62), int(w * 0.14), int(w * 0.62), int(w * 0.34))
        p.drawLine(int(w * 0.62), int(w * 0.34), int(w * 0.78), int(w * 0.34))
    elif kind == "settings":
        for i in range(3):
            y = int(w * (0.30 + i * 0.20))
            p.drawLine(int(w * 0.22), y, int(w * 0.78), y)
    elif kind == "stop":
        p.setBrush(QBrush(c))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(w * 0.28, w * 0.28, w * 0.44, w * 0.44), 2.5, 2.5)
    elif kind == "chevron_right":
        p.drawLine(int(w * 0.34), int(w * 0.22), int(w * 0.62), int(w * 0.50))
        p.drawLine(int(w * 0.34), int(w * 0.78), int(w * 0.62), int(w * 0.50))
    elif kind == "chevron_down":
        p.drawLine(int(w * 0.22), int(w * 0.38), int(w * 0.50), int(w * 0.66))
        p.drawLine(int(w * 0.78), int(w * 0.38), int(w * 0.50), int(w * 0.66))
    elif kind == "plus":
        p.drawLine(int(w * 0.22), int(w * 0.50), int(w * 0.78), int(w * 0.50))
        p.drawLine(int(w * 0.50), int(w * 0.22), int(w * 0.50), int(w * 0.78))
    elif kind == "arrow_up":
        p.drawLine(int(w * 0.50), int(w * 0.72), int(w * 0.50), int(w * 0.28))
        p.drawLine(int(w * 0.32), int(w * 0.46), int(w * 0.50), int(w * 0.28))
        p.drawLine(int(w * 0.68), int(w * 0.46), int(w * 0.50), int(w * 0.28))
    elif kind == "arrow_left":
        p.drawLine(int(w * 0.72), int(w * 0.50), int(w * 0.30), int(w * 0.50))
        p.drawLine(int(w * 0.46), int(w * 0.34), int(w * 0.30), int(w * 0.50))
        p.drawLine(int(w * 0.46), int(w * 0.66), int(w * 0.30), int(w * 0.50))
    elif kind == "mic":
        p.drawRoundedRect(QRectF(w * 0.38, w * 0.30, w * 0.24, w * 0.32), 3, 3)
        p.drawLine(int(w * 0.50), int(w * 0.62), int(w * 0.50), int(w * 0.76))
        p.drawLine(int(w * 0.32), int(w * 0.80), int(w * 0.68), int(w * 0.80))
    elif kind == "shield":
        path = QPainterPath()
        path.moveTo(w * 0.50, w * 0.20)
        path.lineTo(w * 0.78, w * 0.32)
        path.lineTo(w * 0.75, w * 0.58)
        path.lineTo(w * 0.50, w * 0.86)
        path.lineTo(w * 0.25, w * 0.58)
        path.lineTo(w * 0.22, w * 0.32)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "panels":
        p.drawRoundedRect(QRectF(w * 0.12, w * 0.20, w * 0.30, w * 0.60), 2.0, 2.0)
        p.drawRoundedRect(QRectF(w * 0.50, w * 0.20, w * 0.38, w * 0.60), 2.0, 2.0)
    elif kind == "bullet_list":
        for i in range(3):
            cy = int(w * (0.30 + i * 0.20))
            p.drawEllipse(QRectF(w * 0.18, cy - w * 0.05, w * 0.10, w * 0.10))
            p.drawLine(int(w * 0.36), cy, int(w * 0.82), cy)

    p.end()
    return QIcon(pm)
