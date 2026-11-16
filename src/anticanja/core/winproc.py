"""Utilitários Win32: privilégios, Job Object e encerramento gracioso.

- Job Object com KILL_ON_JOB_CLOSE: se o ANTI CANJA travar ou for morto pelo
  Gerenciador de Tarefas, o Windows encerra o sing-box junto. Nunca sobra
  túnel "órfão".
- Encerramento gracioso: o sing-box é iniciado com um console oculto próprio;
  para pará-lo, anexamos a esse console e enviamos CTRL+BREAK. Ele então remove
  rotas, filtros WFP e o adaptador Wintun de forma limpa. Se não responder em
  alguns segundos, usamos TerminateProcess (o Wintun e os filtros WFP são
  liberados pelo sistema quando o processo dono termina).
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time

IS_WINDOWS = sys.platform == "win32"

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010
CREATE_NEW_PROCESS_GROUP = 0x00000200


def is_admin() -> bool:
    if not IS_WINDOWS:
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def relaunch_as_admin(argv: list[str] | None = None) -> bool:
    """Reabre o programa pedindo elevação (UAC). Retorna True se o pedido foi aceito."""
    if not IS_WINDOWS:
        return False
    argv = argv if argv is not None else sys.argv
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, subprocess.list2cmdline(argv[1:])
    else:
        from pathlib import Path

        launcher = Path(__file__).resolve().parents[3] / "run_anticanja.py"
        exe, params = sys.executable, subprocess.list2cmdline([str(launcher), *argv[1:]])
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    return rc > 32


def hidden_console_startupinfo():
    if not IS_WINDOWS:
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return si


if IS_WINDOWS:
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _BASIC(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _EXT(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BASIC),
            ("IoInfo", _IO),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.AttachConsole.argtypes = [wintypes.DWORD]
    _k32.GenerateConsoleCtrlEvent.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _k32.SetConsoleCtrlHandler.argtypes = [wintypes.LPVOID, wintypes.BOOL]
    _k32.GetConsoleWindow.restype = wintypes.HWND


class KillOnCloseJob:
    """Job Object: todos os processos atribuídos morrem quando o ANTI CANJA fecha."""

    def __init__(self) -> None:
        self.handle = None
        if not IS_WINDOWS:
            return
        h = _k32.CreateJobObjectW(None, None)
        if not h:
            return
        info = _EXT()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if _k32.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.handle = h
        else:
            _k32.CloseHandle(h)

    def assign(self, pid: int) -> bool:
        if not self.handle:
            return False
        ph = _k32.OpenProcess(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
        if not ph:
            return False
        try:
            return bool(_k32.AssignProcessToJobObject(self.handle, ph))
        finally:
            _k32.CloseHandle(ph)


def graceful_stop(proc: subprocess.Popen, timeout: float = 6.0) -> bool:
    """Envia CTRL+BREAK ao console oculto do processo; retorna True se saiu sozinho."""
    if proc.poll() is not None:
        return True
    if IS_WINDOWS and not _k32.GetConsoleWindow():
        # Só é seguro quando nós mesmos não temos console (app empacotado, janela).
        if _k32.AttachConsole(proc.pid):
            try:
                _k32.SetConsoleCtrlHandler(None, True)
                # CTRL_BREAK (e não CTRL_C): CTRL_C fica desativado em processos criados com
                # CREATE_NEW_PROCESS_GROUP. O runtime do Go trata os dois como SIGINT.
                _k32.GenerateConsoleCtrlEvent(1, 0)  # CTRL_BREAK_EVENT
            finally:
                _k32.FreeConsole()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
            # Reativa o tratamento normal de Ctrl+C só depois do evento ter sido entregue.
            _k32.SetConsoleCtrlHandler(None, False)
            if proc.poll() is not None:
                return True
    elif not IS_WINDOWS:
        proc.terminate()
        try:
            proc.wait(timeout)
            return True
        except subprocess.TimeoutExpired:
            pass
    return False


def force_stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            pass


def remove_stale_adapters(prefix: str = "AntiCanja") -> int:
    """Remove adaptadores Wintun "fantasmas" com o nome do ANTI CANJA.

    Se o sing-box for encerrado à força (ex.: o app foi fechado pelo Gerenciador de
    Tarefas), o Windows pode manter o dispositivo do adaptador. Na próxima conexão o
    Wintun falha com "não é possível criar um arquivo já existente". Só mexemos em
    dispositivos cujo nome começa com o prefixo do ANTI CANJA.
    """
    if not IS_WINDOWS:
        return 0
    script = (
        "$n=0; Get-PnpDevice -Class Net -ErrorAction SilentlyContinue | "
        f"Where-Object {{ $_.FriendlyName -like '{prefix}*' }} | "
        "ForEach-Object { pnputil /remove-device $_.InstanceId | Out-Null; $n++ }; $n"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, creationflags=CREATE_NO_WINDOW,
        )
        return int((r.stdout or "0").strip().splitlines()[-1] or 0)
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        return 0
