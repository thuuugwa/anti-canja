"""Gera a configuração do sing-box que faz o split-tunnel por processo.

Como funciona (resumo — detalhes em docs/ARQUITETURA.md):

1. Um adaptador TUN (Wintun, embutido no sing-box) recebe as rotas padrão
   (auto_route). Assim o sing-box "vê" cada conexão nova e sabe qual
   processo a abriu (Windows: GetExtendedTcpTable/UdpTable → PID → caminho).
2. Regras de rota:
     • processo cujo caminho está nas pastas do Discord → endpoint WireGuard
     • qualquer outro processo                          → "direct" (sai pela
       placa de rede física, como se a VPN não existisse)
3. O próprio sing-box sai pela interface física (auto_detect_interface),
   então o túnel WireGuard não entra em loop.
4. DNS: no Windows quem resolve nomes é o serviço Dnscache (svchost), não o
   Discord. Para não vazar as consultas do Discord para o provedor, o DNS é
   capturado (hijack-dns) e apenas domínios do Discord são resolvidos pelo
   DNS do ProtonVPN através do túnel. O resto usa o resolvedor do sistema.
5. IPv6: se o perfil não tem endereço IPv6, conexões IPv6 do Discord são
   rejeitadas (o Discord cai para IPv4 pela VPN) — nunca saem "por fora".
6. Kill-switch: no modo "block", sem VPN, conexões do Discord são rejeitadas.

Nenhuma configuração permanente do Windows é alterada: rotas, adaptador e
filtros WFP do strict_route pertencem ao processo sing-box e somem com ele.
"""
from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass, field
from pathlib import Path

from anticanja.core.discord import DISCORD_PATH_REGEX, extra_path_regexes
from anticanja.core.wgconf import WGConfig

TUN_NAME = "AntiCanja"
TUN_ADDR_V4 = "172.19.77.1/30"
TUN_ADDR_V6 = "fdfe:dcba:7777::1/126"

# Tráfego local/LAN nunca entra no TUN → jogos em LAN, impressoras, roteador,
# Chromecast etc. não passam nem pelo sing-box.
LAN_EXCLUDES = [
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "224.0.0.0/4",
    "255.255.255.255/32",
    "fc00::/7",
    "fe80::/10",
    "ff00::/8",
]

# Domínios do Discord resolvidos pelo DNS da VPN (privacidade das consultas).
DISCORD_DOMAINS = [
    "discord.com",
    "discord.gg",
    "discord.media",
    "discord.dev",
    "discord.new",
    "discord.gift",
    "discord.co",
    "discord.app",
    "discordapp.com",
    "discordapp.net",
    "discordcdn.com",
    "discordstatus.com",
    "discord-attachments-uploads-prd.storage.googleapis.com",
    "dis.gd",
]


@dataclass
class TunnelOptions:
    protect_dns: bool = True
    strict_route: bool = True
    tun_mtu: int = 1500  # MTU padrão de rede: evita fragmentação/estouro em apps sensíveis
    keepalive: int = 25
    extra_discord_dirs: list[str] = field(default_factory=list)
    log_level: str = "info"
    # Proxy local só para teste de saída (verificar IP público da VPN).
    probe_port: int | None = None
    probe_user: str | None = None
    probe_pass: str | None = None
    # IPs que nunca entram no TUN (servidores VPN de todos os perfis): o ping mede o
    # caminho real e o tráfego do próprio WireGuard nunca corre risco de loop.
    exclude_ips: list[str] = field(default_factory=list)
    interface_name: str = TUN_NAME
    # None = padrão do sing-box (TCP pela pilha do sistema; precisa do firewall liberado).
    # "gvisor" = pilha userspace: não depende do firewall (fallback automático).
    stack: str | None = None
    # Só captura IPv6 se o PC tiver IPv6 nativo. Sem isso, os apps tentariam IPv6 pelo
    # túnel, que não tem saída IPv6 → travamentos/lentidão (ex.: ChatGPT, Jira).
    capture_ipv6: bool = False


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _host_prefixes(ips: list[str]) -> list[str]:
    out = []
    for ip in ips:
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            continue
        net = f"{a}/{32 if a.version == 4 else 128}"
        if net not in out and not any(a in ipaddress.ip_network(x) for x in LAN_EXCLUDES):
            out.append(net)
    return out


def _process_regexes(opts: TunnelOptions) -> list[str]:
    return [DISCORD_PATH_REGEX, *extra_path_regexes(opts.extra_discord_dirs)]


def _base(opts: TunnelOptions) -> dict:
    tun = {
        "type": "tun",
        "tag": "tun-in",
        "interface_name": opts.interface_name,
        "address": [TUN_ADDR_V4, TUN_ADDR_V6] if opts.capture_ipv6 else [TUN_ADDR_V4],
        "mtu": opts.tun_mtu,
        "auto_route": True,
        "strict_route": opts.strict_route,
        "route_exclude_address": LAN_EXCLUDES + _host_prefixes(opts.exclude_ips),
    }
    if opts.stack:
        tun["stack"] = opts.stack
    return {
        "log": {"level": opts.log_level, "timestamp": False},
        "dns": {
            "servers": [{"type": "local", "tag": "dns-local"}],
            "rules": [],
            "final": "dns-local",
        },
        "inbounds": [tun],
        "outbounds": [{"type": "direct", "tag": "direct"}],
        "route": {
            "rules": [],
            "final": "direct",
            "auto_detect_interface": True,
            "default_domain_resolver": {"server": "dns-local"},
        },
    }


def build_vpn_config(wg: WGConfig, opts: TunnelOptions) -> dict:
    cfg = _base(opts)
    regexes = _process_regexes(opts)

    peer: dict = {
        "address": wg.peer.endpoint_host,
        "port": wg.peer.endpoint_port,
        "public_key": wg.peer.public_key,
        "allowed_ips": wg.peer.allowed_ips or ["0.0.0.0/0", "::/0"],
        "persistent_keepalive_interval": wg.peer.persistent_keepalive or opts.keepalive,
    }
    if wg.peer.preshared_key:
        peer["pre_shared_key"] = wg.peer.preshared_key

    endpoint: dict = {
        "type": "wireguard",
        "tag": "vpn",
        "system": False,  # pilha userspace: sem adaptador WireGuard extra no Windows
        "mtu": wg.mtu or 1408,
        "address": wg.addresses,
        "private_key": wg.private_key,
        "peers": [peer],
    }
    # Abre o socket UDP do WireGuard em IPv4. Sem isso o sing-box escuta em "[::]"
    # (IPv6) e falha em PCs com IPv6 desativado: "listen udp6 [::]: invalid argument".
    if ":" not in wg.peer.endpoint_host:
        endpoint["inet4_bind_address"] = "0.0.0.0"
    if not _is_ip(wg.peer.endpoint_host):
        endpoint["domain_resolver"] = {"server": "dns-local", "strategy": "ipv4_only"}
    cfg["endpoints"] = [endpoint]

    rules: list[dict] = []

    if opts.probe_port:
        cfg["inbounds"].append(
            {
                "type": "mixed",
                "tag": "probe-in",
                "listen": "127.0.0.1",
                "listen_port": opts.probe_port,
                "users": [{"username": opts.probe_user or "ac", "password": opts.probe_pass or ""}],
            }
        )
        rules.append({"inbound": "probe-in", "action": "route", "outbound": "vpn"})

    if opts.protect_dns:
        vpn_dns = wg.dns_ipv4
        if vpn_dns:
            cfg["dns"]["servers"].append(
                {"type": "udp", "tag": "dns-vpn", "server": vpn_dns[0], "detour": "vpn"}
            )
            cfg["dns"]["rules"].append(
                {"domain_suffix": DISCORD_DOMAINS, "action": "route", "server": "dns-vpn"}
            )
        rules.append({"port": 53, "action": "sniff"})
        rules.append({"protocol": "dns", "action": "hijack-dns"})

    if not wg.has_ipv6:
        rules.append({"process_path_regex": regexes, "ip_version": 6, "action": "reject"})

    rules.append({"process_path_regex": regexes, "action": "route", "outbound": "vpn"})
    cfg["route"]["rules"] = rules
    return cfg


def build_block_config(opts: TunnelOptions) -> dict:
    """Kill-switch: VPN desligada, Discord bloqueado, resto da máquina normal."""
    cfg = _base(opts)
    cfg["route"]["rules"] = [
        {"process_path_regex": _process_regexes(opts), "action": "reject"},
    ]
    return cfg


def write_config(cfg: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path
