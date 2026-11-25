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


def _bundle_dir() -> Path | None:
    """Pasta interna do executável único (PyInstaller --onefile)."""
    b = getattr(sys, "_MEIPASS", None)
    return Path(b) if b else None


def ensure_layout() -> None:
    """Cria as pastas que o programa usa (ao lado do .exe e em %APPDATA%).

    Versão em .exe único: na primeira execução extrai o sing-box para a pasta
    bin\ ao lado do .exe e cria a pasta confs\ vazia.
    """
    for f in (data_dir, profiles_dir, runtime_dir, logs_dir):
        f()
    if not getattr(sys, "frozen", False):
        return
    root = app_root()
    for sub in ("bin", "confs"):
        try:
            (root / sub).mkdir(exist_ok=True)
        except OSError:
            pass
    bundle = _bundle_dir()
    if bundle is None:
        return
    name = "sing-box.exe" if sys.platform == "win32" else "sing-box"
    src = bundle / "bin" / name
    dst = root / "bin" / name
    if src.exists():
        try:
            if not dst.exists() or dst.stat().st_size != src.stat().st_size:
                import shutil
                shutil.copy2(src, dst)
        except OSError:
            pass  # pasta sem permissão de escrita: usa a cópia interna


def singbox_exe() -> Path:
    override = os.environ.get("ANTICANJA_SINGBOX")
    if override:
        return Path(override)
    name = "sing-box.exe" if sys.platform == "win32" else "sing-box"
    cands = [app_root() / "bin" / name, app_root() / name]
    if _bundle_dir():
        cands.append(_bundle_dir() / "bin" / name)
    for candidate in cands:
        if candidate.exists():
            return candidate
    return app_root() / "bin" / name
