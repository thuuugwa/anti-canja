"""Gerenciador do túnel (ciclo de vida do sing-box).

Estados:
    DESCONECTADO → CONECTANDO → CONECTADO
                            ↘ ERRO
    BLOQUEADO  (kill-switch com VPN desligada: Discord sem internet, resto normal)

Garantias:
- Nunca há dois sing-box ao mesmo tempo (lock + limpeza de instâncias órfãs).
- A config gerada (contém a chave privada) só existe em disco enquanto o
  sing-box inicia; é apagada assim que ele sobe ou falha.
- Se o sing-box morrer inesperadamente com o kill-switch ligado, o Discord é
  encerrado na hora (sem TUN, ele sairia pela conexão normal) e o túnel é
  reiniciado automaticamente.
- Health-check periódico através do próprio túnel detecta servidor sem
  resposta (handshake WireGuard falhou / chave revogada) e reconecta.
"""
from __future__ import annotations

import enum
import json
import re
import secrets
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore

from anticanja.core import discord, firewall, logbus, paths, winproc
from anticanja.core.profiles import Profile
from anticanja.core.settings import Settings
from anticanja.core.singbox_config import (
    TUN_NAME,
    TunnelOptions,
    build_block_config,
    build_vpn_config,
    write_config,
)

log = logbus.get("tunel")
sblog = logbus.get("sing-box")


class State(enum.Enum):
    DISCONNECTED = "Desconectado"
    CONNECTING = "Conectando…"
    CONNECTED = "Túnel ativo"
    BLOCKING = "Discord bloqueado (kill-switch)"
    ERROR = "Erro"


@dataclass
class TunnelStatus:
    state: State = State.DISCONNECTED
    profile: str | None = None
    endpoint: str | None = None
    server_ip: str | None = None
    exit_ip: str | None = None
    exit_location: str | None = None
    pid: int | None = None
    healthy: bool | None = None
    detail: str = ""
    since: float | None = None
    extra: dict = field(default_factory=dict)


class TunnelError(RuntimeError):
    pass


# Mensagens do sing-box → explicação amigável.
_ERROR_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)udp6|address family not supported|invalid argument was supplied"),
     "IPv6 desativado neste PC — use a versão mais recente do ANTI CANJA (WireGuard em IPv4)."),
    (re.compile(r"(?i)access is denied|operation not permitted|requires? (admin|elevat)"),
     "Permissão negada: execute o ANTI CANJA como administrador."),
    (re.compile(r"(?i)wintun|configure tun interface|create tun"),
     "Falha ao criar o adaptador virtual (Wintun). Feche outras VPNs com TUN e tente de novo."),
    (re.compile(r"(?i)handshake"),
     "Handshake WireGuard falhou: chave revogada/expirada ou servidor indisponível."),
    (re.compile(r"(?i)no route to host|network is unreachable|i/o timeout"),
     "Problema de rede: sem rota até o servidor VPN."),
    (re.compile(r"(?i)decode config|unknown field|json"),
     "Configuração rejeitada pelo sing-box (versão incompatível?)."),
    (re.compile(r"(?i)address already in use|only one usage of each socket"),
     "Porta/adaptador já em uso — outra instância ou VPN ativa."),
]

_LEVEL_RE = re.compile(r"^(?:\x1b\[[0-9;]*m)*\s*(?P<lvl>TRACE|DEBUG|INFO|WARN|ERROR|FATAL|PANIC)\b\s*(?P<msg>.*)$")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def has_native_ipv6() -> bool:
    """True se o PC tem rota IPv6 pública própria (antes do túnel subir)."""
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as s:
            s.connect(("2001:4860:4860::8888", 53))  # UDP: não envia nada
            addr = s.getsockname()[0].split("%")[0]
    except OSError:
        return False
    import ipaddress

    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return ip.is_global and not ip.is_private


def _resolve_server_ip(host: str) -> str | None:
    try:
        return socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0]
    except OSError:
        return None


class TunnelManager:
    HEALTH_INTERVAL = 45.0
    HEALTH_FAILS_BEFORE_RECONNECT = 3
    MAX_AUTO_RESTARTS = 3
    START_TIMEOUT = 25.0

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.status = TunnelStatus()
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._mode: str | None = None  # "vpn" | "block"
        self._profile: Profile | None = None
        self._listeners: list[Callable[[TunnelStatus], None]] = []
        self._job = winproc.KillOnCloseJob()
        self._stop_evt = threading.Event()
        self._expected_exit = False
        self._started_evt = threading.Event()
        self._fatal_line: str | None = None
        self._probe: tuple[int, str, str] | None = None
        self._restart_count = 0
        self._desired: str | None = None  # modo que o usuário quer: "vpn" | "block" | None
        self._config_path = paths.runtime_dir() / "active.json"
        # Fornece os IPs de todos os servidores dos perfis (excluídos do TUN).
        self.exclude_ips_provider: Callable[[], list[str]] = lambda: []
        self._iface_name = TUN_NAME
        self._stack: str | None = None  # None = padrão do sing-box; "gvisor" = fallback sem firewall

    # ------------------------------------------------------------ eventos
    def subscribe(self, cb: Callable[[TunnelStatus], None]) -> None:
        self._listeners.append(cb)

    def _emit(self) -> None:
        for cb in list(self._listeners):
            try:
                cb(self.status)
            except Exception:  # noqa: BLE001
                log.exception("listener falhou")

    def _set(self, **kw) -> None:
        for k, v in kw.items():
            setattr(self.status, k, v)
        self._emit()

    # ------------------------------------------------------------ API
    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def connect(self, profile: Profile) -> None:
        """Conecta (ou troca de perfil). Bloqueante — chame em thread de trabalho."""
        with self._lock:
            if not profile.valid:
                raise TunnelError(f"Perfil inválido: {profile.error}")
            if self.status.state == State.CONNECTED and self._profile and self._profile.name == profile.name and self.is_running:
                log.info("Já conectado com %s.", profile.name)
                return
            self._stop_process(reason="troca de perfil" if self.is_running else None)
            self._profile = profile
            self._restart_count = 0
            self._desired = "vpn"
            try:
                self._start("vpn")
            except TunnelError:
                self._desired = None
                self._profile = None
                if self.settings.kill_switch_when_disconnected:
                    self._try_block_mode()
                raise

    def disconnect(self) -> None:
        with self._lock:
            was = self.status.state
            self._desired = None
            self._stop_process(reason="desconectar")
            self._profile = None
            self._set(state=State.DISCONNECTED, profile=None, endpoint=None, server_ip=None,
                      exit_ip=None, exit_location=None, pid=None, healthy=None, detail="", since=None)
            if was != State.DISCONNECTED:
                log.info("VPN desconectada.")
            if self.settings.kill_switch_when_disconnected:
                self._try_block_mode()

    def apply_idle_policy(self) -> None:
        """Chamado ao iniciar/alterar opções: liga ou desliga o bloqueio com VPN desligada."""
        with self._lock:
            if self._mode == "vpn":
                return
            if self.settings.kill_switch_when_disconnected and not self.is_running:
                self._try_block_mode()
            elif not self.settings.kill_switch_when_disconnected and self._mode == "block":
                self._desired = None
                self._stop_process(reason="kill-switch desativado")
                self._set(state=State.DISCONNECTED, pid=None, detail="")
                log.info("Bloqueio do Discord removido.")

    def shutdown(self) -> None:
        with self._lock:
            self._desired = None
            self._stop_process(reason="encerrando o ANTI CANJA")
            self._set(state=State.DISCONNECTED, pid=None)
            self._cleanup_runtime()

    # ------------------------------------------------------------ interno
    def _try_block_mode(self) -> None:
        self._desired = "block"
        try:
            self._start("block")
        except TunnelError as e:
            log.error("Não foi possível ativar o bloqueio do Discord: %s", e)

    def _options(self, with_probe: bool) -> TunnelOptions:
        s = self.settings
        opts = TunnelOptions(
            protect_dns=s.protect_dns,
            strict_route=s.strict_route,
            extra_discord_dirs=list(s.extra_discord_dirs),
            interface_name=self._iface_name,
            stack=self._stack,
            capture_ipv6=has_native_ipv6(),
        )
        try:
            opts.exclude_ips = list(self.exclude_ips_provider())
        except Exception:  # noqa: BLE001
            opts.exclude_ips = []
        if with_probe:
            port, user, pw = _free_port(), "ac" + secrets.token_hex(4), secrets.token_urlsafe(18)
            logbus.register_secret(pw)
            self._probe = (port, user, pw)
            opts.probe_port, opts.probe_user, opts.probe_pass = port, user, pw
        else:
            self._probe = None
        return opts

    def _preflight(self) -> Path:
        exe = paths.singbox_exe()
        if not exe.exists():
            raise TunnelError(f"sing-box não encontrado em {exe}. Rode o build.ps1 ou coloque sing-box.exe na pasta bin.")
        if not winproc.is_admin():
            raise TunnelError("É necessário executar como administrador para criar o adaptador do túnel.")
        self._kill_orphans(exe)
        firewall.allow(exe)
        return exe

    def _kill_orphans(self, exe: Path) -> None:
        """Encerra sing-box deixados por uma execução anterior que travou."""
        if psutil is None:
            return
        target = str(exe.resolve()).lower()
        for p in psutil.process_iter(["pid", "exe"]):
            try:
                if p.info["exe"] and p.info["exe"].lower() == target and (not self._proc or p.pid != self._proc.pid):
                    log.warning("Encerrando instância órfã do sing-box (PID %s).", p.pid)
                    p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    def _start(self, mode: str) -> None:
        try:
            self._start_once(mode)
        except TunnelError as e:
            if "Wintun" not in str(e):
                raise
            log.warning("Adaptador anterior ficou preso no Windows; limpando e tentando de novo…")
            n = winproc.remove_stale_adapters(TUN_NAME)
            if n:
                log.info("%d adaptador(es) antigo(s) removido(s).", n)
            # Se a remoção não bastar, usa um nome alternativo.
            self._iface_name = TUN_NAME if n else (TUN_NAME + " 2" if self._iface_name == TUN_NAME else TUN_NAME)
            self._start_once(mode)

    def _start_once(self, mode: str) -> None:
        exe = self._preflight()
        if mode == "vpn":
            assert self._profile and self._profile.config
            wg = self._profile.config
            opts = self._options(with_probe=True)
            cfg = build_vpn_config(wg, opts)
            server_ip = wg.peer.endpoint_host if re.match(r"^[\d.:a-fA-F]+$", wg.peer.endpoint_host) else _resolve_server_ip(wg.peer.endpoint_host)
            self._set(state=State.CONNECTING, profile=self._profile.name, endpoint=wg.peer.endpoint,
                      server_ip=server_ip, exit_ip=None, exit_location=None, healthy=None,
                      detail=f"Conectando a {self._profile.server_name}…")
            log.info("Conectando ao perfil %s (%s)…", self._profile.name, wg.peer.endpoint)
        else:
            opts = self._options(with_probe=False)
            cfg = build_block_config(opts)
            self._set(state=State.CONNECTING, detail="Ativando bloqueio do Discord…")

        write_config(cfg, self._config_path)
        try:
            self._check_config(exe)
            self._launch(exe, mode)
        finally:
            self._cleanup_runtime()  # chave privada não fica em disco

        if mode == "vpn":
            self._set(state=State.CONNECTED, pid=self._proc.pid if self._proc else None, since=time.time(),
                      detail=f"sing-box: em execução (PID {self._proc.pid})")
            log.info("Túnel ativo — apenas o Discord está passando pela VPN.")
            threading.Thread(target=self._verify_exit, name="verify-exit", daemon=True).start()
            threading.Thread(target=self._verify_direct, name="verify-direct", daemon=True).start()
        else:
            self._set(state=State.BLOCKING, pid=self._proc.pid if self._proc else None,
                      detail="Discord sem acesso à internet até a VPN conectar.")
            log.info("Kill-switch ativo: Discord bloqueado enquanto a VPN estiver desligada.")

    def _check_config(self, exe: Path) -> None:
        try:
            r = subprocess.run(
                [str(exe), "check", "-c", str(self._config_path), "--disable-color"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
                creationflags=winproc.CREATE_NO_WINDOW if winproc.IS_WINDOWS else 0,
            )
        except subprocess.TimeoutExpired as e:
            raise TunnelError("sing-box check não respondeu.") from e
        if r.returncode != 0:
            lines = (r.stderr or r.stdout or "").strip().splitlines()
            msg = logbus.redact(lines[-1] if lines else "erro desconhecido")
            self._set(state=State.ERROR, detail=msg)
            raise TunnelError(f"Configuração rejeitada: {msg}")

    def _launch(self, exe: Path, mode: str) -> None:
        self._started_evt.clear()
        self._fatal_line = None
        self._expected_exit = False
        self._stop_evt.clear()
        flags = (winproc.CREATE_NEW_CONSOLE | winproc.CREATE_NEW_PROCESS_GROUP) if winproc.IS_WINDOWS else 0
        try:
            proc = subprocess.Popen(
                [str(exe), "run", "-c", str(self._config_path), "-D", str(paths.runtime_dir()), "--disable-color"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                creationflags=flags, startupinfo=winproc.hidden_console_startupinfo(),
                cwd=str(paths.runtime_dir()),
            )
        except OSError as e:
            self._set(state=State.ERROR, detail=str(e))
            raise TunnelError(f"Falha ao iniciar o sing-box: {e}") from e
        self._proc = proc
        self._mode = mode
        self._job.assign(proc.pid)
        threading.Thread(target=self._reader, args=(proc,), name="sb-reader", daemon=True).start()

        deadline = time.monotonic() + self.START_TIMEOUT
        while time.monotonic() < deadline:
            if self._started_evt.wait(0.2):
                break
            if proc.poll() is not None:
                break
        if proc.poll() is not None or not self._started_evt.is_set():
            reason = self._fatal_line or "o sing-box encerrou durante a inicialização"
            self._expected_exit = True
            winproc.force_stop(proc)
            self._proc, self._mode = None, None
            hint = self._hint_for(reason)
            self._set(state=State.ERROR, pid=None, detail=hint or reason)
            raise TunnelError(hint or reason)
        threading.Thread(target=self._watchdog, args=(proc,), name="sb-watchdog", daemon=True).start()

    def _hint_for(self, line: str) -> str | None:
        for rx, hint in _ERROR_HINTS:
            if rx.search(line):
                return hint
        return None

    def _reader(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if not line:
                continue
            line = logbus.redact(line)
            m = _LEVEL_RE.match(line)
            lvl, msg = (m.group("lvl"), m.group("msg")) if m else ("INFO", line)
            if "sing-box started" in msg:
                self._started_evt.set()
            if lvl in ("ERROR", "FATAL", "PANIC"):
                self._fatal_line = msg
                hint = self._hint_for(msg)
                sblog.error("%s%s", msg, f" → {hint}" if hint else "")
            elif lvl == "WARN":
                sblog.warning(msg)
            else:
                sblog.debug(msg)  # só no arquivo de log (painel fica limpo)
        proc.stdout.close()

    def _watchdog(self, proc: subprocess.Popen) -> None:
        fails = 0
        last_check = time.monotonic()
        while not self._stop_evt.is_set() and proc is self._proc:
            if proc.poll() is not None:
                break
            if self._mode == "vpn" and self._probe and time.monotonic() - last_check > self.HEALTH_INTERVAL:
                last_check = time.monotonic()
                if self._probe_ok():
                    if fails:
                        log.info("Conexão com o servidor VPN restabelecida.")
                    fails = 0
                    self._set(healthy=True)
                else:
                    fails += 1
                    log.warning("Servidor VPN sem resposta (%d/%d).", fails, self.HEALTH_FAILS_BEFORE_RECONNECT)
                    self._set(healthy=False)
                    if fails >= self.HEALTH_FAILS_BEFORE_RECONNECT:
                        log.warning("Reconectando o túnel…")
                        threading.Thread(target=self._auto_restart, name="sb-restart", daemon=True).start()
                        return
            self._stop_evt.wait(1.0)

        if self._expected_exit or self._stop_evt.is_set() or proc is not self._proc:
            return
        # ---- saída inesperada ----
        code = proc.returncode
        log.error("O sing-box parou inesperadamente (código %s).", code)
        if self._mode == "vpn" and self.settings.kill_switch:
            st = discord.status()
            if st.running:
                n = discord.kill_all()
                log.warning("Kill-switch: Discord encerrado (%d processos) para evitar vazamento.", n)
        threading.Thread(target=self._auto_restart, name="sb-restart", daemon=True).start()

    def _auto_restart(self) -> None:
        with self._lock:
            mode = self._mode or self._desired
            self._stop_process(reason=None)
            if mode is None or self._desired != mode:
                return
            if self._restart_count >= self.MAX_AUTO_RESTARTS:
                self._set(state=State.ERROR, pid=None,
                          detail="Túnel caiu repetidamente. Verifique a conexão ou troque de servidor.")
                log.error("Desisti após %d tentativas de reconexão.", self.MAX_AUTO_RESTARTS)
                if self.settings.kill_switch:
                    self._try_block_mode()
                return
            self._restart_count += 1
            attempt = self._restart_count
            self._set(state=State.CONNECTING, pid=None, detail=f"Reconectando ({attempt}/{self.MAX_AUTO_RESTARTS})…")
        time.sleep(min(2 ** attempt, 10))  # fora do lock: a interface continua responsiva
        with self._lock:
            if self._desired != mode or self.is_running:
                return  # usuário desconectou/trocou de perfil enquanto esperávamos
            try:
                log.info("Reiniciando túnel (tentativa %d/%d)…", attempt, self.MAX_AUTO_RESTARTS)
                self._start(mode)
            except TunnelError as e:
                log.error("Reconexão falhou: %s", e)
                threading.Thread(target=self._auto_restart, name="sb-restart", daemon=True).start()

    def _stop_process(self, reason: str | None) -> None:
        proc = self._proc
        self._stop_evt.set()
        if proc is None:
            self._mode = None
            return
        self._expected_exit = True
        if proc.poll() is None:
            if reason:
                log.info("Parando sing-box (%s)…", reason)
            if not winproc.graceful_stop(proc):
                winproc.force_stop(proc)
        self._proc, self._mode = None, None
        self._cleanup_runtime()
        if self._desired is None:
            firewall.remove(paths.singbox_exe())

    def _cleanup_runtime(self) -> None:
        for f in (self._config_path, self._config_path.with_suffix(".tmp")):
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass

    # ------------------------------------------------------------ verificação
    def _opener(self) -> urllib.request.OpenerDirector | None:
        if not self._probe:
            return None
        port, user, pw = self._probe
        proxy = f"http://{user}:{pw}@127.0.0.1:{port}"
        return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))

    def _probe_ok(self) -> bool:
        op = self._opener()
        if not op:
            return False
        try:
            with op.open("http://cp.cloudflare.com/generate_204", timeout=8) as r:
                return r.status in (200, 204)
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def _verify_direct(self) -> None:
        """Confere se os apps comuns (não-Discord) continuam com internet TCP.

        Este próprio processo não é o Discord, então sua conexão segue a rota
        "direct". Se falhar, o firewall/antivírus está bloqueando a pilha TCP do
        sing-box: reconectamos com a pilha userspace (gvisor), que não depende dele.
        """
        if not winproc.IS_WINDOWS:
            return
        time.sleep(1.0)
        targets = [("1.1.1.1", 443), ("8.8.8.8", 443), ("208.67.222.222", 443)]
        for _ in range(2):
            for host, port in targets:
                if self._mode != "vpn":
                    return
                try:
                    with socket.create_connection((host, port), timeout=4):
                        log.info("Internet dos outros apps OK (saindo direto, sem VPN).")
                        return
                except OSError:
                    continue
        if self._mode != "vpn":
            return
        if self._stack == "gvisor":
            log.error("Os outros apps estão sem internet TCP mesmo com a pilha alternativa. "
                      "Verifique se um antivírus/firewall de terceiros bloqueia o sing-box.exe.")
            return
        log.warning("Outros apps sem internet TCP (firewall bloqueando o sing-box). "
                    "Reconectando com a pilha alternativa…")
        self._stack = "gvisor"
        with self._lock:
            if self._desired == "vpn" and self._profile:
                self._stop_process(reason="trocar pilha TCP")
                try:
                    self._start("vpn")
                except TunnelError as e:
                    log.error("Falha ao reconectar: %s", e)

    def _verify_exit(self) -> None:
        """Confirma o handshake e descobre o IP público de saída da VPN."""
        op = self._opener()
        if not op or not self.settings.verify_exit_ip:
            return
        for attempt in range(1, 4):
            if self._mode != "vpn":
                return
            try:
                with op.open("https://ipinfo.io/json", timeout=10) as r:
                    data = json.loads(r.read().decode("utf-8"))
                ip = data.get("ip")
                loc = ", ".join(x for x in (data.get("city"), data.get("country")) if x)
                self._restart_count = 0
                self._set(exit_ip=ip, exit_location=loc or None, healthy=True)
                log.info("Handshake OK. IP de saída do Discord: %s%s", ip, f" ({loc})" if loc else "")
                return
            except (urllib.error.URLError, OSError, ValueError) as e:
                log.debug("verificação %d falhou: %s", attempt, e)
                time.sleep(3)
        self._set(healthy=False)
        log.error("Sem resposta do servidor VPN. Handshake WireGuard provavelmente falhou "
                  "(chave revogada/expirada, servidor fora do ar ou porta UDP bloqueada). "
                  "O Discord permanece bloqueado — não há vazamento.")
