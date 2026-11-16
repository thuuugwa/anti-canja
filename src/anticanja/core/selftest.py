"""Teste de isolamento de tráfego (executado com a VPN ligada).

Ideia: o roteamento casa pelo CAMINHO do executável. Então copiamos o
curl.exe do Windows para dentro da pasta do Discord e fazemos a mesma
requisição duas vezes:

    C:\\Windows\\System32\\curl.exe          → deve mostrar o SEU IP (direto)
    %LOCALAPPDATA%\\Discord\\…\\curl.exe       → deve mostrar o IP da VPN

Se os dois IPs forem diferentes e o segundo for o IP de saída da VPN, o
split-tunnel está funcionando: só "o Discord" sai pela VPN. A cópia é
apagada no final.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from anticanja.core import discord, logbus

log = logbus.get("teste")

URL = "https://api.ipify.org"


@dataclass
class IsolationResult:
    direct_ip: str | None
    discord_ip: str | None
    ok: bool
    message: str


def _curl(exe: Path, timeout: int = 15) -> str | None:
    try:
        r = subprocess.run(
            [str(exe), "-s", "-4", "--max-time", str(timeout), URL],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout + 5, creationflags=0x08000000,
        )
        out = r.stdout.strip()
        return out if r.returncode == 0 and out and len(out) < 64 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def run(expect_vpn: bool = True) -> IsolationResult:
    sys_curl = Path(r"C:\Windows\System32\curl.exe")
    if not sys_curl.exists():
        return IsolationResult(None, None, False, "curl.exe do Windows não encontrado (Windows 10 1803+).")
    base = discord.local_appdata() / "Discord"
    created_base = not base.exists()  # sem Discord instalado: pasta criada só para o teste
    base.mkdir(parents=True, exist_ok=True)
    probe_dir = Path(tempfile.mkdtemp(prefix="anticanja-selftest-", dir=base))
    fake = probe_dir / "curl.exe"
    try:
        shutil.copy2(sys_curl, fake)
        log.info("Teste de isolamento: consultando IP público direto e 'como Discord'…")
        direct = _curl(sys_curl)
        via = _curl(fake)
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)
        if created_base:
            shutil.rmtree(base, ignore_errors=True)

    if expect_vpn:
        if not direct:
            res = IsolationResult(direct, via, False, "Sem internet na conexão normal — teste inconclusivo.")
        elif not via:
            res = IsolationResult(direct, via, False, "O processo 'Discord' não conseguiu sair pela VPN (servidor sem resposta?). "
                                                      "Não houve vazamento, mas a VPN não está funcional.")
        elif via == direct:
            res = IsolationResult(direct, via, False, f"FALHA: o tráfego do Discord saiu pelo seu IP real ({direct}).")
        else:
            res = IsolationResult(direct, via, True, f"OK: apps comuns saem por {direct}; o Discord sai por {via} (VPN).")
    else:  # kill-switch com VPN desligada
        if via:
            res = IsolationResult(direct, via, False, f"FALHA: o Discord acessou a internet sem VPN ({via}).")
        else:
            res = IsolationResult(direct, via, True, "OK: Discord bloqueado sem VPN; outros apps normais.")
    (log.info if res.ok else log.error)("Teste de isolamento — %s", res.message)
    return res
