"""Teste rápido de perfil: envia só a mensagem de handshake do WireGuard.

O servidor só responde (tipo 2, ou tipo 3 = cookie sob carga) se a chave do
perfil for aceita. Chave revogada ou servidor fora do ar = silêncio. Leva
~0,2 s por perfil e todos podem ser testados ao mesmo tempo, sem criar túnel
nem mexer na rede.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import socket
import struct
import time

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

CONSTRUCTION = b"Noise_IKpsk2_25519_ChaChaPoly_BLAKE2s"
IDENTIFIER = b"WireGuard v1 zx2c4 Jason@zx2c4.com"
LABEL_MAC1 = b"mac1----"


def _hash(*parts: bytes) -> bytes:
    h = hashlib.blake2s()
    for p in parts:
        h.update(p)
    return h.digest()


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.blake2s).digest()


def _kdf(key: bytes, data: bytes, n: int) -> list[bytes]:
    t0 = _hmac(key, data)
    out, prev = [], b""
    for i in range(1, n + 1):
        prev = _hmac(t0, prev + bytes([i]))
        out.append(prev)
    return out


def _pub(priv: X25519PrivateKey) -> bytes:
    return priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _aead(key: bytes, plain: bytes, ad: bytes) -> bytes:
    return ChaCha20Poly1305(key).encrypt(b"\x00" * 12, plain, ad)


def _tai64n() -> bytes:
    t = time.time()
    return struct.pack(">QI", 0x400000000000000A + int(t), int((t % 1) * 1e9))


def build_initiation(private_key_b64: str, server_pub_b64: str, sender: int) -> bytes:
    s_priv = X25519PrivateKey.from_private_bytes(base64.b64decode(private_key_b64))
    s_pub_r = base64.b64decode(server_pub_b64)
    r_pub = X25519PublicKey.from_public_bytes(s_pub_r)

    c = _hash(CONSTRUCTION)
    h = _hash(_hash(c, IDENTIFIER), s_pub_r)
    e_priv = X25519PrivateKey.generate()
    e_pub = _pub(e_priv)
    c = _kdf(c, e_pub, 1)[0]
    h = _hash(h, e_pub)
    c, k = _kdf(c, e_priv.exchange(r_pub), 2)
    enc_static = _aead(k, _pub(s_priv), h)
    h = _hash(h, enc_static)
    c, k = _kdf(c, s_priv.exchange(r_pub), 2)
    enc_ts = _aead(k, _tai64n(), h)

    msg = struct.pack("<I", 1) + struct.pack("<I", sender) + e_pub + enc_static + enc_ts
    mac1 = hashlib.blake2s(msg, key=_hash(LABEL_MAC1, s_pub_r), digest_size=16).digest()
    return msg + mac1 + b"\x00" * 16


def probe(host: str, port: int, private_key_b64: str, server_pub_b64: str,
          timeout: float = 2.5, tries: int = 2) -> bool:
    """True se o servidor respondeu ao handshake com esta chave."""
    fam = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(fam, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        for _ in range(tries):
            sender = int.from_bytes(os.urandom(4), "little")
            try:
                s.sendto(build_initiation(private_key_b64, server_pub_b64, sender), (host, port))
                end = time.monotonic() + timeout
                while time.monotonic() < end:
                    data, _ = s.recvfrom(2048)
                    if len(data) >= 8 and data[0] in (2, 3):
                        recv_idx = struct.unpack("<I", data[4:8] if data[0] == 3 else data[8:12])[0]
                        if recv_idx == sender:
                            return True
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                return False
    return False
