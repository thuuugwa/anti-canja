import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

PRIV = "1eVwz9MzzQkczpBZVFpfX3vfUkS8lMt0neQOv3elNos="
PUB = "1R9/GaGTaO3aDXjRSa605/T9i8yaPsEKRzH0f3ychhw="
PSK = "K8avZ1euTlRpWSGg4N6lYmStsF4qboeCkpWk9iMaLTg="

PROTON_CONF = f"""[Interface]
# Key for anticanja
# Bouncing = 7
# NetShield = 1
# Moderate NAT = off
# NAT-PMP (Port Forwarding) = off
# VPN Accelerator = on
PrivateKey = {PRIV}
Address = 10.2.0.2/32
DNS = 10.2.0.1

[Peer]
# MX#1
PublicKey = {PUB}
AllowedIPs = 0.0.0.0/0
Endpoint = 195.181.163.1:51820
"""


@pytest.fixture(autouse=True)
def _isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTICANJA_DATA_DIR", str(tmp_path / "data"))
    yield


@pytest.fixture
def proton_conf(tmp_path) -> Path:
    p = tmp_path / "wg-MX-FREE-1.conf"
    p.write_text(PROTON_CONF, encoding="utf-8")
    return p
