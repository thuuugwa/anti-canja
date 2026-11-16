"""Logotipo próprio do ANTI CANJA, desenhado em código (sem arquivos externos).

Escudo roxo com um "corte" diagonal e as iniciais AC — remete a proteção
("anti") sem usar nenhuma marca do Discord.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

from anticanja.ui import theme


def _shield_path(r: QRectF) -> QPainterPath:
    w, h, x, y = r.width(), r.height(), r.x(), r.y()
    p = QPainterPath()
    p.moveTo(x + w * 0.5, y)
    p.cubicTo(x + w * 0.70, y + h * 0.08, x + w * 0.86, y + h * 0.10, x + w, y + h * 0.12)
    p.cubicTo(x + w * 1.0, y + h * 0.55, x + w * 0.82, y + h * 0.82, x + w * 0.5, y + h)
    p.cubicTo(x + w * 0.18, y + h * 0.82, x, y + h * 0.55, x, y + h * 0.12)
    p.cubicTo(x + w * 0.14, y + h * 0.10, x + w * 0.30, y + h * 0.08, x + w * 0.5, y)
    p.closeSubpath()
    return p


def logo_pixmap(size: int = 64, state_color: str | None = None) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # fundo arredondado
    bg = QRectF(0, 0, size, size)
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0, QColor("#1f1630"))
    grad.setColorAt(1, QColor("#0d0a14"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(grad))
    p.drawRoundedRect(bg, size * 0.24, size * 0.24)

    # escudo
    m = size * 0.16
    sr = QRectF(m, m * 0.9, size - 2 * m, size - 1.8 * m)
    sg = QLinearGradient(sr.topLeft(), sr.bottomRight())
    sg.setColorAt(0, QColor(theme.PURPLE_SOFT))
    sg.setColorAt(1, QColor(theme.PURPLE))
    p.setBrush(QBrush(sg))
    p.drawPath(_shield_path(sr))

    # corte diagonal (o "anti")
    pen = QPen(QColor("#0d0a14"), max(1.5, size * 0.06), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(sr.left() + sr.width() * 0.18, sr.bottom() - sr.height() * 0.22),
               QPointF(sr.right() - sr.width() * 0.18, sr.top() + sr.height() * 0.26))

    # iniciais
    if size >= 32:
        f = QFont("Segoe UI", max(6, int(size * 0.17)))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        p.drawText(sr.adjusted(0, sr.height() * 0.05, 0, 0), Qt.AlignmentFlag.AlignCenter, "AC")

    # indicador de estado (bandeja)
    if state_color:
        d = size * 0.34
        p.setPen(QPen(QColor("#0d0a14"), max(1.0, size * 0.04)))
        p.setBrush(QColor(state_color))
        p.drawEllipse(QRectF(size - d - 1, size - d - 1, d, d))
    p.end()
    return pm


def app_icon(state_color: str | None = None) -> QIcon:
    """Ícone do app: avatar do ANTI CANJA (com bolinha de estado na bandeja)."""
    from anticanja.ui.widgets import asset

    base = QPixmap(str(asset("avatar.png")))
    icon = QIcon()
    for s in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        if base.isNull():
            icon.addPixmap(logo_pixmap(s, state_color))
            continue
        pm = base.scaled(s, s, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        if state_color:
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            d = s * 0.38
            p.setPen(QPen(QColor("#0a0607"), max(1.0, s * 0.05)))
            p.setBrush(QColor(state_color))
            p.drawEllipse(QRectF(s - d - 1, s - d - 1, d, d))
            p.end()
        icon.addPixmap(pm)
    return icon


def dot_pixmap(color: str, size: int = 12) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(0, 0, size, size)
    p.end()
    return pm
