"""Regra do Firewall do Windows para o sing-box (somente enquanto conectado).

Por que é necessário: na pilha TCP "system"/"mixed" do sing-box (padrão no
Windows), as conexões TCP dos apps que NÃO vão para a VPN são entregues a um
ouvinte local do sing-box no adaptador do túnel. O Firewall do Windows trata
isso como conexão de entrada numa "rede não identificada (pública)" e bloqueia
em silêncio — os apps comuns ficam sem internet TCP (o UDP continua ok).

O ANTI CANJA cria uma regra de ENTRADA permitindo apenas o sing-box.exe dele,
ao conectar, e remove ao desconectar. Regras de BLOQUEIO antigas para esse mesmo
executável (criadas quando o aviso do firewall é fechado com "Cancelar") são
removidas, porque bloqueio tem prioridade sobre permissão.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from anticanja.core import logbus

log = logbus.get("firewall")
RULE_NAME = "ANTI CANJA (sing-box)"
_NO_WINDOW = 0x08000000


def _netsh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["netsh", "advfirewall", "firewall", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=20, creationflags=_NO_WINDOW)


def allow(exe: Path) -> bool:
    if sys.platform != "win32":
        return True
    exe_s = str(exe.resolve())
    try:
        _netsh("delete", "rule", "name=all", f"program={exe_s}")  # remove bloqueios antigos e duplicatas
        r1 = _netsh("add", "rule", f"name={RULE_NAME}", "dir=in", "action=allow", f"program={exe_s}",
                    "enable=yes", "profile=any", "edge=yes")
        r2 = _netsh("add", "rule", f"name={RULE_NAME}", "dir=out", "action=allow", f"program={exe_s}",
                    "enable=yes", "profile=any")
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("Não foi possível configurar o firewall: %s", e)
        return False
    ok = r1.returncode == 0 and r2.returncode == 0
    if not ok:
        log.warning("Falha ao criar regra de firewall: %s", (r1.stdout or r1.stderr).strip()[:200])
    return ok


def remove(exe: Path) -> None:
    if sys.platform != "win32":
        return
    try:
        _netsh("delete", "rule", f"name={RULE_NAME}", f"program={exe.resolve()}")
    except (OSError, subprocess.TimeoutExpired):
        pass
