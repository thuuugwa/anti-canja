"""Caminhos usados pelo aplicativo.

Dados do usuário ficam em %APPDATA%\\AntiCanja (perfis, settings, logs).
O sing-box.exe fica junto do executável (pasta bin/) — nunca em local gravável
por usuários comuns quando instalado em Program Files.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from anticanja import APP_ID


def _base_data_dir() -> Path:
    override = os.environ.get("ANTICANJA_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_ID
    return Path.home() / ".config" / APP_ID


def app_root() -> Path:
    """Pasta do programa (do .exe congelado ou da raiz do projeto)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def data_dir() -> Path:
    d = _base_data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def profiles_dir() -> Path:
    d = data_dir() / "profiles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def runtime_dir() -> Path:
    """Configs geradas do sing-box (contêm chave privada → apagadas ao desconectar)."""
    d = data_dir() / "runtime"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    d = data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings_file() -> Path:
    return data_dir() / "settings.json"


def singbox_exe() -> Path:
    override = os.environ.get("ANTICANJA_SINGBOX")
    if override:
        return Path(override)
    name = "sing-box.exe" if sys.platform == "win32" else "sing-box"
    for candidate in (app_root() / "bin" / name, app_root() / name):
        if candidate.exists():
            return candidate
    return app_root() / "bin" / name
