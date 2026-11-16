"""Gerenciamento de perfis (.conf).

- Importar copia o arquivo para %APPDATA%\\AntiCanja\\profiles — o original do
  usuário nunca é alterado nem movido.
- Cada perfil é um arquivo <nome>.conf; renomear = renomear a cópia.
- "Atualizar perfil" = substituir a cópia por uma nova versão do .conf
  (ex.: o usuário baixou de novo do painel do ProtonVPN).
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from anticanja.core import logbus, paths
from anticanja.core.wgconf import WGConfig, WireGuardConfigError, parse_file

log = logbus.get("perfis")

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def sanitize_name(name: str) -> str:
    name = _INVALID_CHARS.sub("_", name).strip().strip(".")
    name = re.sub(r"\s+", " ", name)[:64]
    if not name or name.upper() in _RESERVED:
        raise ValueError("Nome de perfil inválido.")
    return name


@dataclass
class Profile:
    name: str
    path: Path
    config: WGConfig | None
    error: str | None
    modified: datetime

    @property
    def endpoint(self) -> str:
        return self.config.peer.endpoint if self.config else "—"

    @property
    def endpoint_host(self) -> str | None:
        return self.config.peer.endpoint_host if self.config else None

    @property
    def server_name(self) -> str:
        if self.config and self.config.server_name:
            return self.config.server_name
        return self.name

    @property
    def valid(self) -> bool:
        return self.config is not None


class ProfileManager:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or paths.profiles_dir()
        self.directory.mkdir(parents=True, exist_ok=True)

    # ---------- leitura ----------
    def list(self) -> list[Profile]:
        out: list[Profile] = []
        for f in sorted(self.directory.glob("*.conf"), key=lambda p: p.stem.lower()):
            out.append(self._load(f))
        return out

    def get(self, name: str) -> Profile | None:
        f = self.directory / f"{name}.conf"
        return self._load(f) if f.exists() else None

    def _load(self, f: Path) -> Profile:
        try:
            cfg, err = parse_file(f), None
        except WireGuardConfigError as e:
            cfg, err = None, str(e)
        return Profile(
            name=f.stem,
            path=f,
            config=cfg,
            error=err,
            modified=datetime.fromtimestamp(f.stat().st_mtime),
        )

    # ---------- escrita ----------
    def _unique_name(self, base: str) -> str:
        base = sanitize_name(base)
        name, i = base, 2
        while (self.directory / f"{name}.conf").exists():
            name = f"{base} ({i})"
            i += 1
        return name

    def import_files(self, files: list[str | Path]) -> tuple[list[Profile], list[tuple[str, str]]]:
        """Importa vários arquivos. Retorna (importados, [(arquivo, erro)])."""
        ok: list[Profile] = []
        failed: list[tuple[str, str]] = []
        for src in files:
            src = Path(src)
            try:
                cfg = parse_file(src)  # valida antes de copiar
                dup = self._find_duplicate(cfg)
                if dup:
                    raise WireGuardConfigError(f"já importado como '{dup}'")
                name = self._unique_name(src.stem)
                dest = self.directory / f"{name}.conf"
                shutil.copyfile(src, dest)  # cópia; original intacto
                _restrict_permissions(dest)
                prof = self._load(dest)
                ok.append(prof)
                log.info("Perfil importado: %s (endpoint %s)", name, cfg.peer.endpoint)
            except (WireGuardConfigError, OSError, ValueError) as e:
                failed.append((src.name, str(e)))
                log.warning("Falha ao importar %s: %s", src.name, e)
        return ok, failed

    def _find_duplicate(self, cfg: WGConfig) -> str | None:
        for p in self.list():
            if (
                p.config
                and p.config.peer.public_key == cfg.peer.public_key
                and p.config.peer.endpoint == cfg.peer.endpoint
                and p.config.private_key == cfg.private_key
            ):
                return p.name
        return None

    def rename(self, old: str, new: str) -> Profile:
        new = sanitize_name(new)
        src = self.directory / f"{old}.conf"
        dst = self.directory / f"{new}.conf"
        if not src.exists():
            raise FileNotFoundError(old)
        if dst.exists() and dst.resolve() != src.resolve():
            raise FileExistsError(f"Já existe um perfil chamado '{new}'.")
        src.rename(dst)
        log.info("Perfil renomeado: %s → %s", old, new)
        return self._load(dst)

    def delete(self, name: str) -> None:
        f = self.directory / f"{name}.conf"
        f.unlink(missing_ok=True)
        log.info("Perfil excluído: %s", name)

    def replace(self, name: str, new_file: str | Path) -> Profile:
        """Atualiza um perfil existente com uma nova versão do .conf."""
        cfg = parse_file(new_file)
        dest = self.directory / f"{name}.conf"
        if not dest.exists():
            raise FileNotFoundError(name)
        tmp = dest.with_suffix(".tmp")
        shutil.copyfile(new_file, tmp)
        os.replace(tmp, dest)
        _restrict_permissions(dest)
        log.info("Perfil atualizado: %s (endpoint %s)", name, cfg.peer.endpoint)
        return self._load(dest)


def _restrict_permissions(path: Path) -> None:
    """Limita a cópia (contém chave privada) ao usuário atual + Administradores + SYSTEM."""
    if sys.platform != "win32":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return
    try:
        import subprocess

        user = os.environ.get("USERNAME", "")
        if not user:
            return
        subprocess.run(
            [
                "icacls", str(path), "/inheritance:r",
                "/grant:r", f"{user}:(F)", "*S-1-5-32-544:(F)", "*S-1-5-18:(F)",
            ],
            capture_output=True,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            timeout=10,
            check=False,
        )
    except Exception:  # noqa: BLE001
        pass
