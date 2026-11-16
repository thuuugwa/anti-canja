import socket
import threading

from anticanja.core import latency
from anticanja.core.latency import PingResult


def test_best_prefers_low_latency_and_low_loss():
    r = {
        "a": PingResult("a", 120.0, 0, 4, "icmp"),
        "b": PingResult("b", 40.0, 0, 4, "icmp"),
        "c": PingResult("c", None, 4, 4, "-"),
        "d": PingResult("d", 35.0, 2, 4, "icmp"),  # mais rápido, mas perdeu pacotes
    }
    assert latency.best(r).host == "b"
    assert latency.best({"c": r["c"]}) is None


def test_tcp_fallback_measures_local_listener(monkeypatch):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port = srv.getsockname()[1]
    threading.Thread(target=lambda: [srv.accept() for _ in range(3)], daemon=True).start()
    monkeypatch.setattr(latency, "_icmp_once", lambda ip, t: None)
    real = latency._tcp_once
    monkeypatch.setattr(latency, "_tcp_once", lambda ip, p, t: real(ip, port, t))
    res = latency.ping("127.0.0.1", count=3)
    assert res.ms is not None and res.method == "tcp" and res.loss == 0
    assert "ms" in res.label
    srv.close()


def test_unresolvable_host():
    res = latency.ping("host.invalido.anticanja.test", count=1)
    assert res.ms is None and res.label == "sem resposta"
