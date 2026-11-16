import json
import re

from anticanja.core.discord import DISCORD_PATH_REGEX, extra_path_regexes, go_regex_escape
from anticanja.core.singbox_config import TunnelOptions, build_block_config, build_vpn_config
from anticanja.core.wgconf import parse_text
from conftest import PROTON_CONF


def _rules(cfg):
    return cfg["route"]["rules"]


def test_vpn_config_routes_only_discord():
    cfg = build_vpn_config(parse_text(PROTON_CONF), TunnelOptions())
    assert cfg["route"]["final"] == "direct"
    assert cfg["route"]["auto_detect_interface"] is True
    routed = [r for r in _rules(cfg) if r.get("outbound") == "vpn"]
    # única regra que manda tráfego para a VPN = processos do Discord
    assert len(routed) == 1 and routed[0]["process_path_regex"][0] == DISCORD_PATH_REGEX
    assert routed[0]["action"] == "route"
    ep = cfg["endpoints"][0]
    assert ep["type"] == "wireguard" and ep["tag"] == "vpn" and ep["system"] is False
    assert ep["peers"][0]["address"] == "195.181.163.1" and ep["peers"][0]["port"] == 51820
    tun = cfg["inbounds"][0]
    assert tun["auto_route"] and tun["strict_route"]
    assert "192.168.0.0/16" in tun["route_exclude_address"]
    assert "stack" not in tun  # padrão do sing-box
    json.dumps(cfg)  # serializável


def test_ipv6_discord_rejected_when_profile_has_no_ipv6():
    cfg = build_vpn_config(parse_text(PROTON_CONF), TunnelOptions())
    rules = _rules(cfg)
    rej = [i for i, r in enumerate(rules) if r.get("action") == "reject" and r.get("ip_version") == 6]
    route = [i for i, r in enumerate(rules) if r.get("outbound") == "vpn" and "process_path_regex" in r]
    assert rej and rej[0] < route[0]


def test_dns_protection():
    cfg = build_vpn_config(parse_text(PROTON_CONF), TunnelOptions(protect_dns=True))
    servers = {s["tag"]: s for s in cfg["dns"]["servers"]}
    assert servers["dns-vpn"]["server"] == "10.2.0.1" and servers["dns-vpn"]["detour"] == "vpn"
    assert "discord.com" in cfg["dns"]["rules"][0]["domain_suffix"]
    assert any(r.get("action") == "hijack-dns" for r in _rules(cfg))
    cfg2 = build_vpn_config(parse_text(PROTON_CONF), TunnelOptions(protect_dns=False))
    assert not any(r.get("action") in ("hijack-dns", "sniff") for r in _rules(cfg2))


def test_probe_inbound_is_local_and_authenticated():
    cfg = build_vpn_config(parse_text(PROTON_CONF), TunnelOptions(probe_port=40000, probe_user="u", probe_pass="p" * 20))
    probe = [i for i in cfg["inbounds"] if i["tag"] == "probe-in"][0]
    assert probe["listen"] == "127.0.0.1" and probe["users"][0]["password"]
    assert _rules(cfg)[0] == {"inbound": "probe-in", "action": "route", "outbound": "vpn"}


def test_block_mode_has_no_vpn():
    cfg = build_block_config(TunnelOptions())
    assert "endpoints" not in cfg
    assert _rules(cfg) == [{"process_path_regex": [DISCORD_PATH_REGEX], "action": "reject"}]


def test_hostname_endpoint_gets_resolver():
    cfg = build_vpn_config(parse_text(PROTON_CONF.replace("195.181.163.1", "x.protonvpn.net")), TunnelOptions())
    assert cfg["endpoints"][0]["domain_resolver"]["server"] == "dns-local"


def _py(rx):  # converte (?i) do Go para flag do Python
    return re.compile(rx.replace("(?i)", ""), re.IGNORECASE)


def test_discord_regex_matches_only_discord():
    rx = _py(DISCORD_PATH_REGEX)
    yes = [
        r"C:\Users\Dahora\AppData\Local\Discord\app-1.0.9170\Discord.exe",
        r"C:\Users\Ana Maria\AppData\Local\DiscordPTB\app-1.0.1120\DiscordPTB.exe",
        r"D:\Users\x\AppData\Local\DiscordCanary\app-1.0.500\DiscordCanary.exe",
        r"C:\Users\x\AppData\Local\Discord\Update.exe",
        r"c:\users\x\appdata\local\discord\app-1.0.9170\modules\discord_hook-1\discord_hook\DiscordHookHelper64.exe",
    ]
    no = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Users\x\AppData\Local\Programs\Opera GX\opera.exe",
        r"C:\Users\x\AppData\Local\DiscordBotManager\bot.exe",
        r"C:\Users\x\AppData\Local\Vencord\Discord.exe",
        r"C:\Games\Discord.exe",
        r"C:\Windows\System32\svchost.exe",
    ]
    for p in yes:
        assert rx.search(p), p
    for p in no:
        assert not rx.search(p), p


def test_custom_dir_regex():
    rx = _py(extra_path_regexes([r"D:\Apps (x86)\Discord+"])[0])
    assert rx.search(r"D:\Apps (x86)\Discord+\app-1\Discord.exe")
    assert not rx.search(r"D:\Apps (x86)\Other\x.exe")
    assert go_regex_escape("a.b") == r"a\.b"


def test_exclude_server_ips_and_interface_name():
    opts = TunnelOptions(exclude_ips=["195.181.163.1", "149.40.62.21", "192.168.1.5", "lixo", "195.181.163.1"],
                         interface_name="AntiCanja 2")
    cfg = build_vpn_config(parse_text(PROTON_CONF), opts)
    tun = cfg["inbounds"][0]
    assert tun["interface_name"] == "AntiCanja 2"
    ex = tun["route_exclude_address"]
    assert ex.count("195.181.163.1/32") == 1 and "149.40.62.21/32" in ex
    assert "192.168.1.5/32" not in ex  # já coberto pela LAN


def test_stack_option():
    assert "stack" not in build_vpn_config(parse_text(PROTON_CONF), TunnelOptions())["inbounds"][0]
    assert build_vpn_config(parse_text(PROTON_CONF), TunnelOptions(stack="gvisor"))["inbounds"][0]["stack"] == "gvisor"


def test_wireguard_binds_ipv4():
    ep = build_vpn_config(parse_text(PROTON_CONF), TunnelOptions())["endpoints"][0]
    assert ep["inet4_bind_address"] == "0.0.0.0"
