"""Atualização automática via GitHub Releases.

Fluxo:
1. `check()` consulta https://api.github.com/repos/<repo>/releases/latest.
2. Compara a tag (ex.: v1.4.0) com a versão instalada (anticanja.__version__).
3. `download()` baixa o pacote `AntiCanja-<versão>-win64.zip` (com barra de
   progresso) e confere o SHA-256 publicado junto (`….zip.sha256`).
4. `install_and_restart()` extrai numa pasta temporária e inicia um script que:
   espera o ANTI CANJA fechar → copia os arquivos novos por cima da instalação
   (sem apagar nada do usuário, ex.: a pasta confs) → abre o programa de novo.

Os perfis, configurações e logs ficam em %APPDATA%\\AntiCanja e nunca são tocados.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from anticanja import UPDATE_REPO, __version__
from anticanja.core import logbus, paths

log = logbus.get("update")
ASSET_RE = re.compile(r"^AntiCanja-(?P<ver>[\d.]+)-win64\.zip$", re.IGNORECASE)
UA = {"User-Agent": f"AntiCanja-Updater/{__version__}", "Accept": "application/vnd.github+json"}


class UpdateError(RuntimeError):
    pass


@dataclass
class Release:
    version: str
    tag: str
    notes: str
    asset_url: str
    asset_name: str
    asset_size: int
    sha256_url: str | None
    page_url: str


def parse_version(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(remote: str, local: str = __version__) -> bool:
    a, b = parse_version(remote), parse_version(local)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def enabled() -> bool:
    return bool(UPDATE_REPO and "/" in UPDATE_REPO) and getattr(sys, "frozen", False)


def _get_json(url: str, timeout: float = 15) -> dict:
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("Repositório ou release não encontrado no GitHub (o repositório é público?).") from e
        if e.code == 403:
            raise UpdateError("Limite de consultas do GitHub atingido. Tente mais tarde.") from e
        raise UpdateError(f"GitHub respondeu HTTP {e.code}.") from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise UpdateError(f"Sem conexão com o GitHub: {e}") from e


def check(repo: str = UPDATE_REPO) -> Release | None:
    """Retorna a release mais nova, ou None se já está atualizado."""
    if not repo:
        raise UpdateError("Repositório de atualizações não configurado.")
    data = _get_json(f"https://api.github.com/repos/{repo}/releases/latest")
    tag = data.get("tag_name") or ""
    assets = data.get("assets") or []
    zip_asset = next((a for a in assets if ASSET_RE.match(a.get("name", ""))), None)
    if not zip_asset:
        raise UpdateError(f"A release {tag} não tem o arquivo AntiCanja-<versão>-win64.zip.")
    version = ASSET_RE.match(zip_asset["name"]).group("ver")
    if parse_version(tag) != (0,):
        version = ".".join(str(x) for x in parse_version(tag))
    if not is_newer(version):
        return None
    sha = next((a for a in assets if a.get("name", "").lower() == zip_asset["name"].lower() + ".sha256"), None)
    return Release(
        version=version, tag=tag, notes=(data.get("body") or "").strip(),
        asset_url=zip_asset["browser_download_url"], asset_name=zip_asset["name"],
        asset_size=int(zip_asset.get("size") or 0),
        sha256_url=sha["browser_download_url"] if sha else None,
        page_url=data.get("html_url") or "",
    )


def download(rel: Release, progress: Callable[[int, int], bool] | None = None) -> Path:
    """Baixa o pacote. `progress(baixado, total)` → retornar False cancela."""
    work = Path(tempfile.gettempdir()) / "AntiCanja-update"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    dest = work / rel.asset_name
    req = urllib.request.Request(rel.asset_url, headers={"User-Agent": UA["User-Agent"]})
    h = hashlib.sha256()
    try:
        with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or rel.asset_size or 0)
            done = 0
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress and progress(done, total) is False:
                    raise UpdateError("Download cancelado.")
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError(f"Falha no download: {e}") from e
    if rel.sha256_url:
        req = urllib.request.Request(rel.sha256_url, headers={"User-Agent": UA["User-Agent"]})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                expected = r.read().decode("utf-8", "replace").split()[0].strip().lower()
        except (urllib.error.URLError, OSError, IndexError) as e:
            raise UpdateError(f"Não foi possível baixar o SHA-256: {e}") from e
        if h.hexdigest().lower() != expected:
            raise UpdateError("O arquivo baixado está corrompido (SHA-256 não confere).")
        log.info("SHA-256 da atualização conferido.")
    else:
        log.warning("Release sem arquivo .sha256 — integridade não verificada.")
    return dest


def _extract(zip_path: Path) -> Path:
    stage = zip_path.parent / "novo"
    shutil.rmtree(stage, ignore_errors=True)
    try:
        with zipfile.ZipFile(zip_path) as z:
            for m in z.namelist():  # proteção contra caminhos maliciosos (zip slip)
                target = (stage / m).resolve()
                if not str(target).startswith(str(stage.resolve())):
                    raise UpdateError("Pacote de atualização inválido.")
            z.extractall(stage)
    except zipfile.BadZipFile as e:
        raise UpdateError("Pacote de atualização corrompido.") from e
    # aceita zip com ou sem pasta raiz
    if not (stage / "AntiCanja.exe").exists():
        inner = next((p for p in stage.iterdir() if (p / "AntiCanja.exe").exists()), None)
        if inner is None:
            raise UpdateError("Pacote não contém AntiCanja.exe.")
        stage = inner
    return stage


def install_and_restart(zip_path: Path) -> None:
    """Prepara a troca de arquivos; o chamador deve encerrar o app em seguida."""
    if not getattr(sys, "frozen", False):
        raise UpdateError("Atualização só funciona na versão compilada (.exe).")
    stage = _extract(zip_path)
    install_dir = paths.app_root()
    exe = install_dir / "AntiCanja.exe"
    log_file = paths.logs_dir() / "update.log"
    script = zip_path.parent / "aplicar_update.cmd"
    script.write_text(
        "@echo off\r\n"
        f'set "SRC={stage}"\r\nset "DST={install_dir}"\r\nset "LOG={log_file}"\r\n'
        f"echo [%date% %time%] Aplicando atualizacao >> \"%LOG%\"\r\n"
        # espera o ANTI CANJA (e o sing-box) fecharem
        f":espera\r\ntasklist /FI \"PID eq {os.getpid()}\" | find \"{os.getpid()}\" >nul && (ping -n 2 127.0.0.1 >nul & goto espera)\r\n"
        "ping -n 3 127.0.0.1 >nul\r\n"
        # copia por cima, sem apagar arquivos extras do usuário (confs etc.)
        'robocopy "%SRC%" "%DST%" /E /R:5 /W:2 /NFL /NDL /NP >> "%LOG%"\r\n'
        "if %ERRORLEVEL% GEQ 8 (echo FALHOU robocopy %ERRORLEVEL% >> \"%LOG%\" & goto abre)\r\n"
        "echo OK >> \"%LOG%\"\r\n"
        f':abre\r\nstart "" "{exe}" --updated\r\n',
        encoding="utf-8",
    )
    subprocess.Popen(
        ["cmd.exe", "/c", str(script)],
        creationflags=0x08000000,  # sem janela
        close_fds=True,
    )
    log.info("Atualização preparada; o ANTI CANJA vai reiniciar.")
