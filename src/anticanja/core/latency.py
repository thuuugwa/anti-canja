"""Medição de latência (ping) até os servidores VPN dos perfis.

- Windows: ICMP via IcmpSendEcho (iphlpapi) — sem abrir janelas de console e
  sem depender do "ping.exe" (cuja saída é traduzida conforme o idioma).
- Se o servidor não responder ICMP, tenta o tempo de conexão TCP na porta 443
  (servidores do ProtonVPN costumam aceitar) como estimativa.
- Os IPs dos servidores ficam fora do adaptador do túnel (route_exclude), então
  o ping mede o caminho real mesmo com a VPN conectada.
"""
from __future__ import annotations

import ctypes
import socket
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from anticanja.core import logbus

log = logbus.get("ping")


@dataclass
class PingResult:
    host: str
    ms: float | None      # mediana; None = sem resposta
    loss: int             # pacotes perdidos
    sent: int
    method: str           # "icmp" | "tcp" | "-"

    @property
    def label(self) -> str:
        if self.ms is None:
            return "sem resposta"
        return f"{self.ms:.0f} ms" + (" (tcp)" if self.method == "tcp" else "")


def _resolve(host: str) -> str | None:
    try:
        return socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0]
    except OSError:
        return None


if sys.platform == "win32":
    from ctypes import wintypes

    class _IP_OPTION_INFORMATION(ctypes.Structure):
        _fields_ = [("Ttl", ctypes.c_ubyte), ("Tos", ctypes.c_ubyte), ("Flags", ctypes.c_ubyte),
                    ("OptionsSize", ctypes.c_ubyte), ("OptionsData", ctypes.c_void_p)]

    class _ICMP_ECHO_REPLY(ctypes.Structure):
        _fields_ = [("Address", ctypes.c_ulong), ("Status", ctypes.c_ulong), ("RoundTripTime", ctypes.c_ulong),
                    ("DataSize", ctypes.c_ushort), ("Reserved", ctypes.c_ushort), ("Data", ctypes.c_void_p),
                    ("Options", _IP_OPTION_INFORMATION)]

    _iphlp = ctypes.WinDLL("iphlpapi", use_last_error=True)
    _iphlp.IcmpCreateFile.restype = wintypes.HANDLE
    _iphlp.IcmpCloseHandle.argtypes = [wintypes.HANDLE]
    _iphlp.IcmpSendEcho.argtypes = [wintypes.HANDLE, ctypes.c_ulong, ctypes.c_void_p, wintypes.WORD,
                                    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
    _iphlp.IcmpSendEcho.restype = wintypes.DWORD

    def _icmp_once(ip: str, timeout_ms: int) -> float | None:
        h = _iphlp.IcmpCreateFile()
        if not h or h == wintypes.HANDLE(-1).value:
            return None
        try:
            payload = b"anticanja-ping-32bytes-xxxxxxxxx"
            buf = ctypes.create_string_buffer(ctypes.sizeof(_ICMP_ECHO_REPLY) + len(payload) + 8)
            addr = int.from_bytes(socket.inet_aton(ip), "little")
            n = _iphlp.IcmpSendEcho(h, addr, payload, len(payload), None, buf, ctypes.sizeof(buf), timeout_ms)
            if n == 0:
                return None
            reply = _ICMP_ECHO_REPLY.from_buffer_copy(buf)
            if reply.Status != 0:  # IP_SUCCESS
                return None
            return float(max(reply.RoundTripTime, 1))
        finally:
            _iphlp.IcmpCloseHandle(h)
else:
    def _icmp_once(ip: str, timeout_ms: int) -> float | None:  # pragma: no cover - só Windows
        return None


def _tcp_once(ip: str, port: int, timeout: float) -> float | None:
    t0 = time.perf_counter()
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return (time.perf_counter() - t0) * 1000
    except OSError:
        return None


def ping(host: str, count: int = 3, timeout_ms: int = 1000) -> PingResult:
    ip = _resolve(host)
    if not ip:
        return PingResult(host, None, count, count, "-")
    samples = [s for s in (_icmp_once(ip, timeout_ms) for _ in range(count)) if s is not None]
    method = "icmp"
    if not samples:
        samples = [s for s in (_tcp_once(ip, 443, timeout_ms / 1000) for _ in range(count)) if s is not None]
        method = "tcp"
    if not samples:
        return PingResult(host, None, count, count, "-")
    return PingResult(host, round(statistics.median(samples), 1), count - len(samples), count, method)


def ping_many(hosts: list[str], count: int = 3) -> dict[str, PingResult]:
    uniq = list(dict.fromkeys(h for h in hosts if h))
    if not uniq:
        return {}
    with ThreadPoolExecutor(max_workers=min(16, len(uniq))) as ex:
        results = list(ex.map(lambda h: ping(h, count), uniq))
    return {r.host: r for r in results}


def best(results: dict[str, PingResult]) -> PingResult | None:
    ok = [r for r in results.values() if r.ms is not None]
    if not ok:
        return None
    # menor latência; empate → menor perda
    return min(ok, key=lambda r: (r.ms + r.loss * 50, r.loss))
