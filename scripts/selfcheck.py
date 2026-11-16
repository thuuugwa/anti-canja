"""Valida as configurações geradas contra o sing-box.exe REAL (bin/).

Gera um perfil fictício (chaves aleatórias), monta as configs de VPN e de
bloqueio e roda `sing-box check` em cada uma. Se o formato mudar numa versão
futura do sing-box, o build falha aqui — antes de chegar ao usuário.

Uso:  python scripts/selfcheck.py
"""
import base64
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from anticanja.core import paths  # noqa: E402
from anticanja.core.singbox_config import TunnelOptions, build_block_config, build_vpn_config, write_config  # noqa: E402
from anticanja.core.wgconf import parse_text  # noqa: E402


def key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


CONF = f"""[Interface]
PrivateKey = {key()}
Address = 10.2.0.2/32
DNS = 10.2.0.1
[Peer]
# TEST#1
PublicKey = {key()}
AllowedIPs = 0.0.0.0/0
Endpoint = 203.0.113.10:51820
"""


def main() -> int:
    exe = paths.singbox_exe()
    if not exe.exists():
        print(f"sing-box não encontrado: {exe}")
        return 2
    ver = subprocess.run([str(exe), "version"], capture_output=True, text=True).stdout.splitlines()[0]
    print("Validando com", ver)
    wg = parse_text(CONF)
    wg6 = parse_text(CONF.replace("Address = 10.2.0.2/32", "Address = 10.2.0.2/32, 2a07:b944::2:2/128"))
    cases = {
        "vpn": build_vpn_config(wg, TunnelOptions(probe_port=45678, probe_user="u", probe_pass="p" * 16)),
        "vpn-ipv6": build_vpn_config(wg6, TunnelOptions()),
        "vpn-sem-dns": build_vpn_config(wg, TunnelOptions(protect_dns=False, strict_route=False)),
        "vpn-pasta-extra": build_vpn_config(wg, TunnelOptions(extra_discord_dirs=[r"D:\Apps (x86)\Discord"])),
        "bloqueio": build_block_config(TunnelOptions()),
        "vpn-gvisor": build_vpn_config(wg, TunnelOptions(stack="gvisor", exclude_ips=["203.0.113.10"])),
    }
    failed = 0
    with tempfile.TemporaryDirectory() as td:
        for name, cfg in cases.items():
            f = write_config(cfg, Path(td) / f"{name}.json")
            r = subprocess.run([str(exe), "check", "-c", str(f), "--disable-color"], capture_output=True, text=True)
            status = "OK" if r.returncode == 0 else "FALHOU"
            print(f"  [{status}] {name}")
            if r.returncode != 0:
                failed += 1
                print("     ", (r.stderr or r.stdout).strip())
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
