"""Widgets próprios: chave liga/desliga e imagens dos recursos."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QCheckBox

from anticanja.core import paths
from anticanja.ui import theme


def asset(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", paths.app_root()))
    for c in (base / "assets" / name, paths.app_root() / "assets" / name):
        if c.exists():
            return c
    return base / "assets" / name


def asset_pixmap(name: str, w: int | None = None, h: int | None = None) -> QPixmap:
    pm = QPixmap(str(asset(name)))
    if pm.isNull():
        return pm
    if w and h:
        return pm.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    if w:
        return pm.scaledToWidth(w, Qt.TransformationMode.SmoothTransformation)
    return pm


class ToggleSwitch(QCheckBox):
    """Chave estilo celular (vermelha quando ligada)."""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(28)

    def sizeHint(self) -> QSize:  # noqa: N802
        s = super().sizeHint()
        return QSize(s.width() + 40, max(28, s.height()))

    def hitButton(self, pos) -> bool:  # noqa: N802
        return self.rect().contains(pos)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = 44, 24
        x = self.width() - w - 2
        y = (self.height() - h) / 2
        on = self.isChecked()
        track = QColor(theme.RED if on else "#2a1c1f")
        if not self.isEnabled():
            track = QColor("#3a2a2d")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(x, y, w, h), h / 2, h / 2)
        p.setBrush(QColor("#ffffff"))
        d = h - 6
        kx = x + w - d - 3 if on else x + 3
        p.drawEllipse(QRectF(kx, y + 3, d, d))
        p.setPen(QColor(theme.TEXT if self.isEnabled() else theme.MUTED))
        p.drawText(QRectF(0, 0, x - 10, self.height()), Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap,
                   self.text())
        p.end()
