import pytest

from anticanja.core.wgconf import WireGuardConfigError, parse_text
from conftest import PRIV, PROTON_CONF, PSK, PUB


def test_parse_proton_export():
    cfg = parse_text(PROTON_CONF)
    assert cfg.private_key == PRIV
    assert cfg.addresses == ["10.2.0.2/32"]
    assert cfg.dns == ["10.2.0.1"]
    assert cfg.peer.public_key == PUB
    assert cfg.peer.endpoint_host == "195.181.163.1"
    assert cfg.peer.endpoint_port == 51820
    assert cfg.peer.endpoint == "195.181.163.1:51820"
    assert cfg.server_name == "MX#1"
    assert not cfg.has_ipv6


def test_bom_crlf_case_and_ipv6():
    text = "﻿" + (
        "[interface]\r\nprivatekey = {p}\r\naddress = 10.2.0.2/32, 2a07:b944::2:2/128\r\n"
        "dns = 10.2.0.1, 2a07:b944::2:1\r\nMTU = 1380\r\n\r\n[PEER]\r\npublickey={u}\r\n"
        "presharedkey = {k}\r\nallowedips = 0.0.0.0/0, ::/0\r\nendpoint = [2001:db8::1]:51820\r\n"
        "PersistentKeepalive = 15\r\n"
    ).format(p=PRIV, u=PUB, k=PSK)
    cfg = parse_text(text)
    assert cfg.has_ipv6
    assert cfg.mtu == 1380
    assert cfg.dns_ipv4 == ["10.2.0.1"]
    assert cfg.peer.endpoint == "[2001:db8::1]:51820"
    assert cfg.peer.preshared_key == PSK
    assert cfg.peer.persistent_keepalive == 15


def test_hostname_endpoint():
    cfg = parse_text(PROTON_CONF.replace("195.181.163.1:51820", "node-br-01.protonvpn.net:51820"))
    assert cfg.peer.endpoint_host == "node-br-01.protonvpn.net"


@pytest.mark.parametrize(
    "mutate,msg",
    [
        (lambda t: t.replace(f"PrivateKey = {PRIV}\n", ""), "PrivateKey"),
        (lambda t: t.replace(PRIV, "abc"), "PrivateKey inválida"),
        (lambda t: t.replace("Endpoint = 195.181.163.1:51820\n", ""), "Endpoint"),
        (lambda t: t.replace(":51820", ":99999"), "Porta"),
        (lambda t: t.split("[Peer]")[0], "[Peer]"),
        (lambda t: t.replace("Address = 10.2.0.2/32", "Address = banana"), "Address"),
    ],
)
def test_invalid(mutate, msg):
    with pytest.raises(WireGuardConfigError, match=msg.replace("[", r"\[").replace("]", r"\]")):
        parse_text(mutate(PROTON_CONF))


def test_redacted_summary_has_no_private_key():
    cfg = parse_text(PROTON_CONF)
    s = str(cfg.redacted_summary())
    assert PRIV not in s and PUB not in s
