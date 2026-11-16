"""Detecção, monitoramento e inicialização do Discord.

O Discord Desktop (Electron + Squirrel) fica em:
    %LOCALAPPDATA%\\Discord\\app-1.0.xxxx\\Discord.exe        (Stable)
    %LOCALAPPDATA%\\DiscordPTB\\app-…\\DiscordPTB.exe          (PTB)
    %LOCALAPPDATA%\\DiscordCanary\\app-…\\DiscordCanary.exe    (Canary)
    %LOCALAPPDATA%\\Discord\\Update.exe                         (atualizador)

Cada canal abre vários processos com o MESMO executável (processo principal,
GPU, utility/network service, renderer). Além disso há módulos nativos
(discord_voice, discord_utils...) carregados DENTRO desses processos, e o
Update.exe que baixa atualizações.

Por isso o roteamento não usa só o nome "Discord.exe" (outro programa poderia
ter esse nome): usamos uma expressão regular sobre o CAMINHO COMPLETO do
executável, restrita às pastas de instalação do Discord. Isso pega todos os
processos auxiliares e o atualizador — e nada mais. Navegadores abrindo
discord.com NÃO são afetados, porque o caminho deles é outro.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore

from anticanja.core import logbus

log = logbus.get("discord")


@dataclass(frozen=True)
class DiscordChannel:
    key: str
    label: str
    folder: str
    exe: str


CHANNELS: tuple[DiscordChannel, ...] = (
    DiscordChannel("stable", "Discord", "Discord", "Discord.exe"),
    DiscordChannel("ptb", "Discord PTB", "DiscordPTB", "DiscordPTB.exe"),
    DiscordChannel("canary", "Discord Canary", "DiscordCanary", "DiscordCanary.exe"),
    DiscordChannel("development", "Discord Development", "DiscordDevelopment", "DiscordDevelopment.exe"),
)

# Regex (sintaxe Go/RE2, usada pelo sing-box) que casa QUALQUER executável dentro
# das pastas de instalação do Discord em qualquer perfil de usuário do Windows.
# (?i) = sem diferenciar maiúsculas. Ex.: C:\Users\Ana\AppData\Local\Discord\app-1.0.9170\Discord.exe
DISCORD_PATH_REGEX = r"(?i)\\appdata\\local\\discord(ptb|canary|development)?\\[^:]+\.exe$"

# Nomes de processo exatos (usados só para a interface/monitoramento — não para rota).
DISCORD_EXE_NAMES = {c.exe.lower() for c in CHANNELS}


def local_appdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))


def installed_channels() -> list[DiscordChannel]:
    base = local_appdata()
    return [c for c in CHANNELS if (base / c.folder / "Update.exe").exists()]


def extra_path_regexes(custom_dirs: list[str]) -> list[str]:
    """Permite instalar o Discord em pasta personalizada (ex.: D:\\Apps\\Discord)."""
    out = []
    for d in custom_dirs:
        d = d.strip().replace("/", "\\").rstrip("\\")
        if d:
            out.append(r"(?i)^" + go_regex_escape(d) + r"\\.+\.exe$")
    return out


def go_regex_escape(s: str) -> str:
    """Escapa apenas metacaracteres (compatível com RE2/Go, usado pelo sing-box)."""
    special = set(r".^$*+?()[]{}|\\")
    return "".join("\\" + ch if ch in special else ch for ch in s)


def _is_discord_path(exe: str | None) -> bool:
    if not exe:
        return False
    import re

    return re.search(DISCORD_PATH_REGEX.replace("(?i)", ""), exe, re.IGNORECASE) is not None


@dataclass
class DiscordStatus:
    running: bool
    channels: list[str]
    pids: list[int]

    @property
    def label(self) -> str:
        if not self.running:
            return "fechado"
        return "aberto (" + ", ".join(self.channels) + ")"


def status() -> DiscordStatus:
    if psutil is None:
        return DiscordStatus(False, [], [])
    pids, chans = [], set()
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name not in DISCORD_EXE_NAMES:
                continue
            exe = proc.info.get("exe")
            # exe pode ser None sem permissão; com admin normalmente temos acesso.
            if exe and not _is_discord_path(exe):
                continue
            pids.append(proc.info["pid"])
            for c in CHANNELS:
                if c.exe.lower() == name:
                    chans.add(c.label)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    order = [c.label for c in CHANNELS]
    return DiscordStatus(bool(pids), sorted(chans, key=order.index), pids)


def launch(channel_key: str | None = None) -> bool:
    """Abre o Discord pelo Update.exe (forma oficial — respeita atualizações).

    Como o ANTI CANJA roda como administrador, abrir o Discord diretamente o
    deixaria elevado. Usamos o explorer.exe como intermediário para que o
    Discord rode com o token normal do usuário (sem privilégios de admin).
    """
    chans = installed_channels()
    if not chans:
        log.error("Discord não encontrado em %s", local_appdata())
        return False
    chan = next((c for c in chans if c.key == channel_key), chans[0])
    if sys.platform != "win32":
        log.error("Abrir o Discord só é suportado no Windows.")
        return False
    # explorer.exe não repassa argumentos, então não dá para chamar
    # "Update.exe --processStart Discord.exe" através dele. Preferimos o atalho
    # oficial do Menu Iniciar (que já contém esses argumentos); se não existir,
    # abrimos o Discord.exe da versão mais recente (pasta app-x.y.z).
    start_menu = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Discord Inc"
    shortcut = start_menu / f"{chan.label}.lnk"
    target: Path | None = shortcut if shortcut.exists() else None
    if target is None:
        app_dirs = sorted(
            (d for d in (local_appdata() / chan.folder).glob("app-*") if (d / chan.exe).exists()),
            key=lambda d: [int(x) if x.isdigit() else 0 for x in d.name[4:].split(".")],
        )
        if app_dirs:
            target = app_dirs[-1] / chan.exe
    if target is None:
        log.error("Executável do %s não encontrado.", chan.label)
        return False
    try:
        subprocess.Popen(["explorer.exe", str(target)], creationflags=0x08000000)
        log.info("Iniciando %s…", chan.label)
        return True
    except OSError as e:
        log.error("Falha ao iniciar o Discord: %s", e)
        return False


def kill_all() -> int:
    """Encerra todos os processos do Discord (usado pela proteção contra vazamento)."""
    if psutil is None:
        return 0
    killed = 0
    for pid in status().pids:
        try:
            psutil.Process(pid).kill()
            killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return killed


def restart(channel_key: str | None = None, reason: str = "entrar no túnel") -> bool:
    """Fecha e reabre o Discord (todas as versões que estavam abertas).

    Usado ao conectar (para as conexões entrarem no túnel) e ao desconectar/sair
    (para o Discord voltar imediatamente para a internet normal).
    """
    import time

    st = status()
    labels = set(st.channels)
    if st.running:
        kill_all()
        for _ in range(30):
            if not status().running:
                break
            time.sleep(0.2)
        log.info("Discord reiniciado para %s.", reason)
    keys = [c.key for c in CHANNELS if c.label in labels] or [channel_key]
    ok = True
    for k in keys:
        ok = launch(k) and ok
    return ok


class DiscordMonitor:
    """Observa a abertura/fechamento do Discord e avisa os interessados."""

    def __init__(self, interval: float = 2.0) -> None:
        import threading

        self.interval = interval
        self._stop = threading.Event()
        self._listeners: list = []
        self.current = DiscordStatus(False, [], [])
        self._thread = threading.Thread(target=self._run, name="discord-monitor", daemon=True)

    def subscribe(self, cb) -> None:
        self._listeners.append(cb)

    def start(self) -> None:
        self.current = status()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                new = status()
            except Exception:  # noqa: BLE001
                continue
            if new.running != self.current.running or new.channels != self.current.channels:
                if new.running and not self.current.running:
                    log.info("Discord iniciado: %s.", ", ".join(new.channels))
                elif not new.running and self.current.running:
                    log.info("Discord encerrado.")
                else:
                    log.info("Discord: %s.", new.label)
                self.current = new
                for cb in list(self._listeners):
                    try:
                        cb(new)
                    except Exception:  # noqa: BLE001
                        pass
            else:
                self.current = new
