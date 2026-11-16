"""Configurações persistentes (JSON em %APPDATA%\\AntiCanja\\settings.json)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields

from anticanja.core import logbus, paths

log = logbus.get("settings")


@dataclass
class Settings:
    last_profile: str | None = None
    start_with_windows: bool = False
    auto_connect: bool = False
    auto_connect_best_ping: bool = False  # conexão automática escolhe o servidor de menor ping
    launch_discord_after_connect: bool = False
    minimize_to_tray: bool = True
    start_minimized: bool = False
    kill_switch: bool = True           # bloquear Discord se o túnel cair / estiver desligado
    kill_switch_when_disconnected: bool = False  # bloquear Discord mesmo com VPN desligada
    protect_dns: bool = False  # captura DNS do sistema: pode quebrar apps (opcional)
    strict_route: bool = False  # bloqueia DNS fora do túnel: pode quebrar apps (opcional)
    preferred_discord: str = "stable"
    extra_discord_dirs: list[str] = field(default_factory=list)
    verify_exit_ip: bool = True
    # Conexões abertas ANTES do túnel continuam fora dele. Se o Discord já estiver
    # aberto ao conectar: "ask" (perguntar), "always" (reiniciar) ou "never".
    restart_discord_on_connect: str = "ask"
    tray_hint_shown: bool = False
    restart_discord_on_disconnect: bool = True
    settings_version: int = 2
    check_updates: bool = True        # verificar atualizações ao abrir
    auto_install_updates: bool = False  # instalar sem perguntar
    skipped_version: str | None = None  # ao desconectar/sair, reabre o Discord fora da VPN

    @classmethod
    def load(cls) -> "Settings":
        f = paths.settings_file()
        if not f.exists():
            return cls()
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
            known = {fl.name for fl in fields(cls)}
            st = cls(**{k: v for k, v in raw.items() if k in known})
            if raw.get("settings_version", 1) < 2:
                # v1.3: DNS hijack e rota estrita quebravam outros apps (ChatGPT, Jira…).
                st.protect_dns = False
                st.strict_route = False
                st.settings_version = 2
                st.save()
            return st
        except (OSError, ValueError, TypeError) as e:
            log.warning("settings.json inválido (%s); usando padrões.", e)
            return cls()

    def save(self) -> None:
        f = paths.settings_file()
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)
