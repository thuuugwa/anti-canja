
import pytest

from anticanja.core import logbus
from anticanja.core.profiles import ProfileManager, sanitize_name
from conftest import PRIV, PROTON_CONF, PSK


def test_import_preserves_original(tmp_path, proton_conf):
    before = proton_conf.read_bytes()
    pm = ProfileManager(tmp_path / "profiles")
    ok, failed = pm.import_files([proton_conf])
    assert not failed and ok[0].name == "wg-MX-FREE-1"
    assert proton_conf.read_bytes() == before and proton_conf.exists()
    assert ok[0].endpoint == "195.181.163.1:51820"


def test_duplicate_and_invalid(tmp_path, proton_conf):
    pm = ProfileManager(tmp_path / "profiles")
    pm.import_files([proton_conf])
    bad = tmp_path / "bad.conf"
    bad.write_text("lixo")
    ok, failed = pm.import_files([proton_conf, bad])
    assert not ok and len(failed) == 2
    assert "já importado" in failed[0][1]


def test_rename_delete_replace(tmp_path, proton_conf):
    pm = ProfileManager(tmp_path / "profiles")
    pm.import_files([proton_conf])
    p = pm.rename("wg-MX-FREE-1", "México 1")
    assert p.name == "México 1" and pm.get("wg-MX-FREE-1") is None
    newer = tmp_path / "new.conf"
    newer.write_text(PROTON_CONF.replace("195.181.163.1", "195.181.163.9"))
    assert pm.replace("México 1", newer).endpoint == "195.181.163.9:51820"
    pm.delete("México 1")
    assert pm.list() == []


def test_sanitize_name():
    assert sanitize_name('a<b>:c"?') == "a_b__c__"
    with pytest.raises(ValueError):
        sanitize_name("CON")


def test_redaction():
    s = f'PrivateKey = {PRIV} e "private_key": "{PRIV}" psk {PSK} http://u:senha@127.0.0.1:1'
    out = logbus.redact(s)
    assert PRIV not in out and PSK not in out and "senha" not in out
    logbus.register_secret("segredo-xyz-123")
    assert "segredo-xyz-123" not in logbus.redact("token segredo-xyz-123")


def test_logger_redacts(caplog):
    h = logbus.setup()
    got = []
    h.subscribe(lambda lvl, msg: got.append(msg))
    logbus.get("t").info("chave %s", PRIV)
    assert got and PRIV not in got[-1]
