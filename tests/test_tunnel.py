"""Testa o ciclo de vida do túnel com um sing-box FALSO (sem rede, sem admin).

Valida: conexão, troca de perfil sem duplicar processos, remoção da config
com chave privada, desconexão, reinício automático após queda, modo bloqueio
e redação de segredos vindos da saída do sing-box.
"""
import json
import os
import stat
import sys
import textwrap
import time

import pytest

from anticanja.core import logbus, tunnel as tunnel_mod, winproc
from anticanja.core.profiles import ProfileManager
from anticanja.core.settings import Settings
from anticanja.core.tunnel import State, TunnelError, TunnelManager
from conftest import PRIV, PROTON_CONF

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="sing-box falso usa shebang POSIX")

FAKE = textwrap.dedent(
    """\
    #!{py}
    import json, os, sys, time, signal
    args = sys.argv[1:]
    cfg_path = args[args.index("-c") + 1]
    if args[0] == "check":
        cfg = json.load(open(cfg_path))
        sys.exit(1 if os.environ.get("FAKE_FAIL_CHECK") else 0)
    if args[0] == "run":
        cfg = json.load(open(cfg_path))
        with open(os.environ["FAKE_LOG"], "a") as f:
            f.write(json.dumps({{"pid": os.getpid(), "mode": "vpn" if "endpoints" in cfg else "block"}}) + "\\n")
        signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
        key = cfg.get("endpoints", [{{}}])[0].get("private_key", "none")
        print("WARN debug private_key=" + key, flush=True)
        print("INFO sing-box started (0.01s)", flush=True)
        crash = float(os.environ.get("FAKE_CRASH_AFTER", "0"))
        t0 = time.time()
        while True:
            time.sleep(0.05)
            if crash and time.time() - t0 > crash and not os.path.exists(os.environ["FAKE_LOG"] + ".crashed"):
                open(os.environ["FAKE_LOG"] + ".crashed", "w").close()
                print("FATAL simulated crash", flush=True)
                os._exit(3)
    """
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = tmp_path / "sing-box"
    fake.write_text(FAKE.format(py=sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("ANTICANJA_SINGBOX", str(fake))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "runs.log"))
    monkeypatch.setattr(winproc, "is_admin", lambda: True)
    monkeypatch.setattr(tunnel_mod.discord, "status", lambda: tunnel_mod.discord.DiscordStatus(False, [], []))
    conf = tmp_path / "wg-MX-FREE-1.conf"
    conf.write_text(PROTON_CONF)
    conf2 = tmp_path / "wg-BR-2.conf"
    conf2.write_text(PROTON_CONF.replace("195.181.163.1", "149.102.251.97").replace(PRIV, "2" * 43 + "="))
    pm = ProfileManager(tmp_path / "profiles")
    pm.import_files([conf, conf2])
    s = Settings(verify_exit_ip=False)
    tm = TunnelManager(s)
    lines = []
    logbus.setup().subscribe(lambda lvl, m: lines.append(m))
    yield tm, pm, s, tmp_path, lines
    tm.shutdown()


def runs(tmp_path):
    f = tmp_path / "runs.log"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def test_connect_disconnect_and_cleanup(env):
    tm, pm, s, tmp, lines = env
    tm.connect(pm.get("wg-MX-FREE-1"))
    assert tm.status.state == State.CONNECTED and tm.is_running
    assert tm.status.endpoint == "195.181.163.1:51820"
    assert not tm._config_path.exists(), "config com chave privada deve ser apagada após iniciar"
    pid = tm.status.pid
    tm.disconnect()
    assert tm.status.state == State.DISCONNECTED and not tm.is_running
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert all(PRIV not in m for m in lines), "chave privada vazou para o log"


def test_switch_profile_never_duplicates(env):
    tm, pm, s, tmp, _ = env
    tm.connect(pm.get("wg-MX-FREE-1"))
    first = tm.status.pid
    tm.connect(pm.get("wg-MX-FREE-1"))  # mesmo perfil: no-op
    assert tm.status.pid == first
    tm.connect(pm.get("wg-BR-2"))
    assert tm.status.pid != first and tm.status.endpoint == "149.102.251.97:51820"
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(first, 0)


def test_auto_restart_after_crash(env, monkeypatch):
    tm, pm, s, tmp, lines = env
    monkeypatch.setenv("FAKE_CRASH_AFTER", "0.5")
    tm.connect(pm.get("wg-MX-FREE-1"))
    first = tm.status.pid
    deadline = time.time() + 10
    while time.time() < deadline:
        if tm.status.state == State.CONNECTED and tm.status.pid not in (None, first):
            break
        time.sleep(0.1)
    assert tm.status.state == State.CONNECTED and tm.status.pid != first
    assert any("parou inesperadamente" in m for m in lines)


def test_block_mode_when_disconnected(env):
    tm, pm, s, tmp, _ = env
    s.kill_switch_when_disconnected = True
    tm.apply_idle_policy()
    assert tm.status.state == State.BLOCKING
    tm.connect(pm.get("wg-MX-FREE-1"))
    assert tm.status.state == State.CONNECTED
    tm.disconnect()
    assert tm.status.state == State.BLOCKING
    assert [r["mode"] for r in runs(tmp)] == ["block", "vpn", "block"]
    s.kill_switch_when_disconnected = False
    tm.apply_idle_policy()
    assert tm.status.state == State.DISCONNECTED and not tm.is_running


def test_check_failure_reports_error(env, monkeypatch):
    tm, pm, s, tmp, _ = env
    monkeypatch.setenv("FAKE_FAIL_CHECK", "1")
    with pytest.raises(TunnelError):
        tm.connect(pm.get("wg-MX-FREE-1"))
    assert not tm.is_running and not tm._config_path.exists()
