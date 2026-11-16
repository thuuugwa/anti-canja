"""Sistema de logs com redação automática de segredos.

Tudo que vai para o painel "Atividade" e para o arquivo de log passa por
`redact()`, que remove chaves WireGuard (base64 de 32 bytes), PrivateKey=…,
senhas de proxy e tokens. Assim nenhuma chave privada aparece em logs,
mesmo que venha de uma mensagem de erro do sing-box.
"""
from __future__ import annotations

import logging
import re
import threading
from logging.handlers import RotatingFileHandler
from typing import Callable

from anticanja.core import paths

# Base64 de 32 bytes = 43 chars + "="
_WG_KEY_RE = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=(?![A-Za-z0-9+/=])")
_KV_SECRET_RE = re.compile(
    r"(?i)(private[_ ]?key|pre[_ ]?shared[_ ]?key|presharedkey|password|secret|token)(\"?\s*[:=]\s*\"?)([^\s\",}]+)"
)
_URL_CRED_RE = re.compile(r"(?i)(\w+://)([^:/\s@]+):([^@\s]+)@")

_secrets_lock = threading.Lock()
_extra_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    """Registra um valor que nunca deve aparecer em logs (ex.: senha do proxy local)."""
    if value and len(value) >= 6:
        with _secrets_lock:
            _extra_secrets.add(value)


def redact(text: str) -> str:
    if not text:
        return text
    text = _KV_SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
    text = _URL_CRED_RE.sub(r"\1***:***@", text)
    text = _WG_KEY_RE.sub("<chave-oculta>", text)
    with _secrets_lock:
        for s in _extra_secrets:
            if s in text:
                text = text.replace(s, "***")
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class CallbackHandler(logging.Handler):
    """Encaminha registros para a interface (ou qualquer callback)."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self._callbacks: list[Callable[[str, str], None]] = []
        self.setFormatter(RedactingFormatter("[%(asctime)s] %(message)s", "%H:%M:%S"))

    def subscribe(self, cb: Callable[[str, str], None]) -> None:
        self._callbacks.append(cb)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:  # noqa: BLE001
            return
        for cb in list(self._callbacks):
            try:
                cb(record.levelname, msg)
            except Exception:  # noqa: BLE001
                pass


_ui_handler: CallbackHandler | None = None


def setup(level: int = logging.INFO) -> CallbackHandler:
    global _ui_handler
    root = logging.getLogger("anticanja")
    if _ui_handler is not None:
        return _ui_handler
    root.setLevel(logging.DEBUG)
    fh = RotatingFileHandler(paths.logs_dir() / "anticanja.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(RedactingFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(fh)
    _ui_handler = CallbackHandler()
    _ui_handler.setLevel(level)
    root.addHandler(_ui_handler)
    return _ui_handler


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"anticanja.{name}")
