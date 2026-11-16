from anticanja.core.healthcheck import build_probe_config
from anticanja.core.wgconf import parse_text
from conftest import PROTON_CONF


def test_probe_config_has_no_tun_and_local_auth_proxy():
    cfg = build_probe_config(parse_text(PROTON_CONF), 40001, "u", "p" * 12)
    assert all(i["type"] != "tun" for i in cfg["inbounds"])
    inb = cfg["inbounds"][0]
    assert inb["listen"] == "127.0.0.1" and inb["users"][0]["password"]
    assert cfg["route"]["final"] == "vpn"
    assert cfg["endpoints"][0]["inet4_bind_address"] == "0.0.0.0"
