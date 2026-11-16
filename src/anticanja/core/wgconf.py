"""Parser de arquivos WireGuard (.conf) — formato exportado pelo ProtonVPN.

Exemplo típico do ProtonVPN:

    [Interface]
    # Key for anticanja
    # Bouncing = 7
    # NetShield = 1
    # Moderate NAT = off
    # NAT-PMP (Port Forwarding) = off
    # VPN Accelerator = on
    PrivateKey = xxxxx=
    Address = 10.2.0.2/32
    DNS = 10.2.0.1

    [Peer]
    # BR#12
    PublicKey = yyyyy=
    AllowedIPs = 0.0.0.0/0
    Endpoint = 149.102.251.97:51820

O parser é tolerante (comentários, maiúsculas, espaços, BOM, IPv6 entre
colchetes) e valida o mínimo necessário para o túnel funcionar.
"""
from __future__ import annotations

import base64
import binascii
import ipaddress
import re
from dataclasses import dataclass, field
from pathlib import Path


class WireGuardConfigError(ValueError):
    """Arquivo .conf inválido ou incompleto."""


@dataclass
class WGPeer:
    public_key: str
    endpoint_host: str
    endpoint_port: int
    allowed_ips: list[str] = field(default_factory=list)
    preshared_key: str | None = None
    persistent_keepalive: int | None = None
    comment: str | None = None  # ex.: "BR#12" (nome do servidor no ProtonVPN)

    @property
    def endpoint(self) -> str:
        host = self.endpoint_host
        if ":" in host:  # IPv6
            host = f"[{host}]"
        return f"{host}:{self.endpoint_port}"


@dataclass
class WGConfig:
    private_key: str
    addresses: list[str]
    dns: list[str]
    mtu: int | None
    peer: WGPeer
    interface_comments: list[str] = field(default_factory=list)

    @property
    def has_ipv6(self) -> bool:
        return any(":" in a for a in self.addresses)

    @property
    def server_name(self) -> str | None:
        """Nome do servidor, quando o ProtonVPN o grava como comentário no [Peer]."""
        return self.peer.comment

    @property
    def dns_ipv4(self) -> list[str]:
        out = []
        for d in self.dns:
            try:
                if ipaddress.ip_address(d).version == 4:
                    out.append(d)
            except ValueError:
                continue
        return out

    def redacted_summary(self) -> dict:
        """Resumo seguro para exibir/logar (sem chaves)."""
        return {
            "servidor": self.server_name or "-",
            "endpoint": self.peer.endpoint,
            "enderecos": ", ".join(self.addresses),
            "dns": ", ".join(self.dns) or "-",
            "mtu": self.mtu or "padrão",
            "allowed_ips": ", ".join(self.peer.allowed_ips),
            "ipv6": "sim" if self.has_ipv6 else "não",
            "chave_publica_servidor": _short_key(self.peer.public_key),
            "chave_pre_compartilhada": "sim" if self.peer.preshared_key else "não",
        }


def _short_key(k: str) -> str:
    return f"{k[:6]}…{k[-4:]}" if len(k) > 12 else "…"


def _valid_wg_key(value: str) -> bool:
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return False
    return len(raw) == 32


_SECTION_RE = re.compile(r"^\s*\[(?P<name>[A-Za-z]+)\]\s*$")
_KV_RE = re.compile(r"^\s*(?P<key>[A-Za-z]+)\s*=\s*(?P<value>.*?)\s*$")


def _split_list(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _parse_endpoint(value: str) -> tuple[str, int]:
    value = value.strip()
    m = re.match(r"^\[(?P<h>[^\]]+)\]:(?P<p>\d+)$", value)  # [ipv6]:porta
    if not m:
        m = re.match(r"^(?P<h>[^:\s]+):(?P<p>\d+)$", value)  # host:porta
    if not m:
        raise WireGuardConfigError(f"Endpoint inválido: {value!r}")
    port = int(m.group("p"))
    if not 0 < port < 65536:
        raise WireGuardConfigError(f"Porta inválida no endpoint: {port}")
    return m.group("h"), port


def parse_text(text: str) -> WGConfig:
    text = text.lstrip("﻿")
    section: str | None = None
    iface: dict[str, str] = {}
    peers: list[dict[str, str]] = []
    iface_comments: list[str] = []
    pending_comment: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(("#", ";")):
            comment = line.lstrip("#;").strip()
            if section == "interface":
                iface_comments.append(comment)
            elif section == "peer" and peers and "comment" not in peers[-1]:
                peers[-1]["comment"] = comment
            else:
                pending_comment = comment
            continue
        sm = _SECTION_RE.match(line)
        if sm:
            section = sm.group("name").lower()
            if section == "peer":
                peers.append({})
                if pending_comment:
                    peers[-1]["comment"] = pending_comment
            pending_comment = None
            continue
        # comentário no fim da linha
        line = re.split(r"\s+#", line, maxsplit=1)[0]
        kv = _KV_RE.match(line)
        if not kv or section is None:
            continue
        key = kv.group("key").lower()
        value = kv.group("value")
        target = iface if section == "interface" else peers[-1] if section == "peer" and peers else None
        if target is None:
            continue
        if key in target and key in ("address", "dns", "allowedips"):
            target[key] += "," + value
        else:
            target[key] = value

    if not iface:
        raise WireGuardConfigError("Seção [Interface] ausente.")
    if not peers:
        raise WireGuardConfigError("Seção [Peer] ausente.")
    if len(peers) > 1:
        # ProtonVPN sempre exporta 1 peer; usamos o primeiro e avisamos via exceção leve? Não:
        # simplesmente usamos o primeiro peer válido.
        pass

    priv = iface.get("privatekey", "").strip()
    if not priv:
        raise WireGuardConfigError("PrivateKey ausente em [Interface].")
    if not _valid_wg_key(priv):
        raise WireGuardConfigError("PrivateKey inválida (esperado base64 de 32 bytes).")

    addresses = _split_list(iface.get("address", ""))
    if not addresses:
        raise WireGuardConfigError("Address ausente em [Interface].")
    norm_addresses = []
    for a in addresses:
        try:
            net = ipaddress.ip_interface(a if "/" in a else (a + ("/128" if ":" in a else "/32")))
        except ValueError as e:
            raise WireGuardConfigError(f"Address inválido: {a!r}") from e
        norm_addresses.append(str(net))

    dns_entries = []
    for d in _split_list(iface.get("dns", "")):
        try:
            ipaddress.ip_address(d)
            dns_entries.append(d)
        except ValueError:
            # Domínios de busca (search domains) são ignorados.
            continue

    mtu = None
    if iface.get("mtu"):
        try:
            mtu = int(iface["mtu"])
        except ValueError as e:
            raise WireGuardConfigError("MTU inválido.") from e
        if not 576 <= mtu <= 9000:
            raise WireGuardConfigError(f"MTU fora do intervalo: {mtu}")

    p = peers[0]
    pub = p.get("publickey", "").strip()
    if not pub or not _valid_wg_key(pub):
        raise WireGuardConfigError("PublicKey ausente ou inválida em [Peer].")
    psk = p.get("presharedkey", "").strip() or None
    if psk and not _valid_wg_key(psk):
        raise WireGuardConfigError("PresharedKey inválida em [Peer].")
    if not p.get("endpoint"):
        raise WireGuardConfigError("Endpoint ausente em [Peer].")
    host, port = _parse_endpoint(p["endpoint"])
    allowed = _split_list(p.get("allowedips", "")) or ["0.0.0.0/0", "::/0"]
    keepalive = None
    if p.get("persistentkeepalive"):
        try:
            keepalive = int(p["persistentkeepalive"])
        except ValueError:
            keepalive = None

    return WGConfig(
        private_key=priv,
        addresses=norm_addresses,
        dns=dns_entries,
        mtu=mtu,
        peer=WGPeer(
            public_key=pub,
            endpoint_host=host,
            endpoint_port=port,
            allowed_ips=allowed,
            preshared_key=psk,
            persistent_keepalive=keepalive,
            comment=p.get("comment"),
        ),
        interface_comments=iface_comments,
    )


def parse_file(path: str | Path) -> WGConfig:
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError as e:
        raise WireGuardConfigError(f"Não foi possível ler {p.name}: {e.strerror}") from e
    if len(data) > 64 * 1024:
        raise WireGuardConfigError("Arquivo grande demais para ser um .conf do WireGuard.")
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return parse_text(data.decode(enc))
        except UnicodeDecodeError:
            continue
    raise WireGuardConfigError("Codificação de arquivo não reconhecida.")
