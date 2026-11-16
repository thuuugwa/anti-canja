"""Inicialização automática com o Windows.

Usa uma Tarefa Agendada ("ANTI CANJA") com gatilho de logon e "executar com
privilégios mais altos". Assim o programa abre já como administrador, sem a
janela do UAC a cada logon — o que um atalho na pasta Inicializar ou a chave
Run do registro não conseguem fazer.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from anticanja import APP_NAME
from anticanja.core import logbus

log = logbus.get("autostart")
TASK_NAME = APP_NAME


def _command() -> tuple[str, str]:
    if getattr(sys, "frozen", False):
        return sys.executable, "--autostart"
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pyw if pyw.exists() else sys.executable)
    return exe, "-m anticanja --autostart"


def _task_xml(exe: str, args: str) -> str:
    user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".strip("\\")
    workdir = str(Path(exe).parent)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Inicia o {escape(APP_NAME)} ao entrar no Windows.</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{escape(user)}</UserId><Delay>PT10S</Delay></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <Enabled>true</Enabled>
    <Priority>6</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(exe)}</Command>
      <Arguments>{escape(args)}</Arguments>
      <WorkingDirectory>{escape(workdir)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["schtasks.exe", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        creationflags=0x08000000,
    )


def is_enabled() -> bool:
    if sys.platform != "win32":
        return False
    try:
        return _schtasks("/Query", "/TN", TASK_NAME).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def enable() -> bool:
    if sys.platform != "win32":
        log.error("Inicialização automática só existe no Windows.")
        return False
    exe, args = _command()
    fd, xml_path = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    try:
        Path(xml_path).write_text(_task_xml(exe, args), encoding="utf-16")
        r = _schtasks("/Create", "/F", "/TN", TASK_NAME, "/XML", xml_path)
    finally:
        Path(xml_path).unlink(missing_ok=True)
    if r.returncode == 0:
        log.info("Inicialização com o Windows ativada.")
        return True
    log.error("Falha ao criar tarefa de inicialização: %s", (r.stderr or r.stdout).strip())
    return False


def disable() -> bool:
    if sys.platform != "win32":
        return False
    r = _schtasks("/Delete", "/F", "/TN", TASK_NAME)
    if r.returncode == 0 or not is_enabled():
        log.info("Inicialização com o Windows desativada.")
        return True
    log.error("Falha ao remover tarefa: %s", (r.stderr or r.stdout).strip())
    return False
