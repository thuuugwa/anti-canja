"""Verifica se cada perfil .conf está funcionando (Online/Offline).

Para cada perfil, sobe um sing-box TEMPORÁRIO, sem adaptador TUN e sem mexer
na rede do Windows: só um proxy local (127.0.0.1, porta aleatória, com senha)
ligado ao endpoint WireGuard do perfil. Em seguida faz uma requisição pequena
por esse proxy. Se responder, o handshake WireGuard funcionou → Online.
Chave revogada/expirada ou servidor fora do ar → Offline.

O perfil que está conectado no momento NÃO é testado (usar a mesma chave em
duas sessões derrubaria a conexão atual).
"""
from __future__ import annotations

import json
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from anticanja.core import logbus, paths, winproc
from anticanja.core.profiles import Profile
from anticanja.core.wgconf import WGConfig

log = logbus.get("status")
_job = winproc.KillOnCloseJob()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def build_probe_config(wg: WGConfig, port: int, user: str, pw: str) -> dict:
    peer = {
        "address": wg.peer.endpoint_host,
        "port": wg.peer.endpoint_port,
        "public_key": wg.peer.public_key,
        "allowed_ips": ["0.0.0.0/0", "::/0"],
        "persistent_keepalive_interval": 25,
    }
    if wg.peer.preshared_key:
        peer["pre_shared_key"] = wg.peer.preshared_key
    ep = {
        "type": "wireguard", "tag": "vpn", "system": False, "mtu": wg.mtu or 1408,
        "address": wg.addresses, "private_key": wg.private_key, "peers": [peer],
    }
    if ":" not in wg.peer.endpoint_host:
        ep["inet4_bind_address"] = "0.0.0.0"
    return {
        "log": {"level": "error"},
        "endpoints": [ep],
        "inbounds": [{"type": "mixed", "tag": "in", "listen": "127.0.0.1", "listen_port": port,
                      "users": [{"username": user, "password": pw}]}],
        "outbounds": [{"type": "direct", "tag": "direct"}],
        "route": {"final": "vpn", "auto_detect_interface": True},
    }


def check_profile(p: Profile, timeout: float = 15.0) -> bool | None:
    """True = Online, False = Offline, None = não foi possível testar."""
    exe = paths.singbox_exe()
    if not p.valid or not exe.exists():
        return None if p.valid else False
    port, user, pw = _free_port(), "hc" + secrets.token_hex(3), secrets.token_urlsafe(12)
    logbus.register_secret(pw)
    cfg = build_probe_config(p.config, port, user, pw)
    fd, cfg_path = tempfile.mkstemp(prefix="ac-hc-", suffix=".json", dir=paths.runtime_dir())
    proc = None
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        proc = subprocess.Popen(
            [str(exe), "run", "-c", cfg_path, "--disable-color"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            creationflags=winproc.CREATE_NO_WINDOW if winproc.IS_WINDOWS else 0,
        )
        _job.assign(proc.pid)
        proxy = f"http://{user}:{pw}@127.0.0.1:{port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        deadline = time.monotonic() + timeout
        time.sleep(0.8)
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return None
            try:
                with opener.open("http://cp.cloudflare.com/generate_204", timeout=4) as r:
                    if r.status in (200, 204):
                        return True
            except (urllib.error.URLError, OSError, ValueError):
                time.sleep(0.7)
        return False
    finally:
        if proc and proc.poll() is None:
            proc.kill()
            try:
                proc.wait(3)
            except subprocess.TimeoutExpired:
                pass
        Path(cfg_path).unlink(missing_ok=True)


def check_many(profiles: list[Profile], skip: set[str] | None = None, workers: int = 1) -> dict[str, bool | None]:
    """Testa um perfil por vez (ProtonVPN Free só aceita 1 conexão simultânea)."""
    skip = skip or set()
    todo = [p for p in profiles if p.name not in skip]
    if not todo:
        return {}
    with ThreadPoolExecutor(max_workers=min(workers, len(todo))) as ex:
        res = dict(zip((p.name for p in todo), ex.map(check_profile, todo), strict=True))
    for p in todo:  # segunda chance antes de marcar Offline
        if res[p.name] is False:
            time.sleep(2)
            res[p.name] = check_profile(p)
    if todo and all(v is False for v in res.values()) and len(todo) > 1:
        log.warning("Nenhum perfil respondeu: provável problema de rede/firewall, não dos perfis.")
        return {k: None for k in res}
    on = sum(1 for v in res.values() if v)
    off = sum(1 for v in res.values() if v is False)
    log.info("Status dos perfis: %d online, %d offline.", on, off)
    return res
