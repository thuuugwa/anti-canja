import hashlib
import io
import zipfile

import pytest

from anticanja.core import updater


def test_version_compare():
    assert updater.is_newer("v1.4.0", "1.3.0")
    assert updater.is_newer("1.3.1", "1.3")
    assert not updater.is_newer("v1.3.0", "1.3.0")
    assert not updater.is_newer("1.2.9", "1.3.0")
    assert updater.is_newer("2.0", "1.99.99")


def _release_json(tag, names):
    return {"tag_name": tag, "body": "Notas", "html_url": "https://x",
            "assets": [{"name": n, "browser_download_url": f"https://dl/{n}", "size": 10} for n in names]}


def test_check_finds_newer(monkeypatch):
    monkeypatch.setattr(updater, "_get_json", lambda url, timeout=15: _release_json(
        "v9.0.0", ["AntiCanja-9.0.0-win64.zip", "AntiCanja-9.0.0-win64.zip.sha256"]))
    rel = updater.check("dono/repo")
    assert rel.version == "9.0.0" and rel.sha256_url.endswith(".sha256")


def test_check_up_to_date(monkeypatch):
    monkeypatch.setattr(updater, "_get_json", lambda url, timeout=15: _release_json(
        "v0.0.1", ["AntiCanja-0.0.1-win64.zip"]))
    assert updater.check("dono/repo") is None


def test_check_missing_asset(monkeypatch):
    monkeypatch.setattr(updater, "_get_json", lambda url, timeout=15: _release_json("v9.0.0", ["outro.zip"]))
    with pytest.raises(updater.UpdateError):
        updater.check("dono/repo")


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, d in entries.items():
            z.writestr(n, d)
    return buf.getvalue()


class _Resp(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_download_verifies_sha(monkeypatch, tmp_path):
    payload = _zip({"AntiCanja.exe": b"x"})
    good = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    rel = updater.Release("9.0.0", "v9.0.0", "", "https://dl/a.zip", "AntiCanja-9.0.0-win64.zip", 0,
                          "https://dl/a.zip.sha256", "")

    def fake_open(req, timeout=0):
        url = req.full_url
        return _Resp((good + "  a.zip\n").encode() if url.endswith(".sha256") else payload)

    monkeypatch.setattr(updater.urllib.request, "urlopen", fake_open)
    seen = []
    path = updater.download(rel, lambda d, t: seen.append((d, t)) or True)
    assert path.exists() and seen[-1][0] == len(payload)
    assert (updater._extract(path) / "AntiCanja.exe").exists()

    bad = "0" * 64
    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda req, timeout=0: _Resp(bad.encode() if req.full_url.endswith(".sha256") else payload))
    with pytest.raises(updater.UpdateError, match="SHA-256"):
        updater.download(rel)


def test_extract_rejects_zip_slip(tmp_path):
    z = tmp_path / "x.zip"
    z.write_bytes(_zip({"../../evil.exe": b"x", "AntiCanja.exe": b"x"}))
    with pytest.raises(updater.UpdateError):
        updater._extract(z)
