"""The desktop app's yt-dlp updater, with a fake PyPI (plus one real network test)."""

import hashlib
import io
import sys
import zipfile

import pytest

from chordchart.desktop import ytdlp_update
from chordchart.desktop.ytdlp_update import UpdateError, activate, update


def _wheel(package: str, version: str, broken: bool = False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{package}/__init__.py", "raise ImportError('broken')" if broken else "")
        archive.writestr(f"{package}/version.py", f"__version__ = '{version}'\n")
    return buffer.getvalue()


def _meta(name, version, data, requires=(), sha=None):
    return {
        "info": {"name": name, "version": version, "requires_dist": list(requires)},
        "urls": [
            {
                "filename": f"{name.replace('-', '_')}-{version}-py3-none-any.whl",
                "url": f"https://files.example/{name}-{version}.whl",
                "digests": {"sha256": sha or hashlib.sha256(data).hexdigest()},
            }
        ],
    }


class FakePyPI:
    def __init__(self, latest="2026.9.1", ejs="0.9.0", sha=None):
        self.ytdlp = _wheel("yt_dlp", latest)
        self.ejs = _wheel("yt_dlp_ejs", ejs)
        pin = [f'yt-dlp-ejs=={ejs}; extra == "default"', f'yt-dlp-ejs=={ejs}; extra == "pin"']
        self.json = {
            "https://pypi.org/pypi/yt-dlp/json": _meta("yt-dlp", latest, self.ytdlp, pin),
            f"https://pypi.org/pypi/yt-dlp/{latest}/json": _meta(
                "yt-dlp", latest, self.ytdlp, pin, sha
            ),
            f"https://pypi.org/pypi/yt-dlp-ejs/{ejs}/json": _meta("yt-dlp-ejs", ejs, self.ejs),
        }
        self.files = {
            f"https://files.example/yt-dlp-{latest}.whl": self.ytdlp,
            f"https://files.example/yt-dlp-ejs-{ejs}.whl": self.ejs,
        }
        self.downloads = []

    def get_json(self, url):
        return self.json[url]

    def get_bytes(self, url):
        self.downloads.append(url)
        return self.files[url]


def _run(fake, folder, current="2026.8.19", check=lambda staging: "2026.09.01"):
    return update(
        current, folder=folder, get_json=fake.get_json, get_bytes=fake.get_bytes, check_import=check
    )


def test_installs_newer_version_with_its_pinned_ejs(tmp_path):
    fake = FakePyPI()
    result = _run(fake, tmp_path)

    assert (result.status, result.version, result.restart_needed) == ("updated", "2026.9.1", True)
    current = tmp_path / "current"
    assert (current / "yt_dlp" / "version.py").read_text() == "__version__ = '2026.9.1'\n"
    assert (current / "yt_dlp_ejs" / "version.py").exists()
    assert len(fake.downloads) == 2
    assert [p.name for p in tmp_path.iterdir()] == ["current"]  # no staging left behind


def test_up_to_date_downloads_nothing(tmp_path):
    fake = FakePyPI(latest="2026.8.19")
    result = _run(fake, tmp_path, current="2026.08.19")  # zero-padded form compares equal
    assert (result.status, result.restart_needed) == ("up-to-date", False)
    assert fake.downloads == [] and not (tmp_path / "current").exists()


def test_bad_checksum_changes_nothing(tmp_path):
    (tmp_path / "current").mkdir()
    (tmp_path / "current" / "marker").write_text("old working copy")
    fake = FakePyPI(sha="0" * 64)
    with pytest.raises(UpdateError, match="SHA-256"):
        _run(fake, tmp_path)
    assert (tmp_path / "current" / "marker").read_text() == "old working copy"
    assert [p.name for p in tmp_path.iterdir()] == ["current"]


def test_failed_import_check_changes_nothing(tmp_path):
    def broken(staging):
        raise UpdateError("the new yt-dlp doesn't load (ImportError: x); nothing was changed")

    with pytest.raises(UpdateError, match="doesn't load"):
        _run(FakePyPI(), tmp_path, check=broken)
    assert list(tmp_path.iterdir()) == []


def test_wrong_version_from_import_check_is_refused(tmp_path):
    with pytest.raises(UpdateError, match="expected 2026.9.1"):
        _run(FakePyPI(), tmp_path, check=lambda staging: "2026.08.19")


def test_no_network_is_one_clear_error(tmp_path):
    def offline(url):
        raise OSError("getaddrinfo failed")

    with pytest.raises(UpdateError, match="couldn't reach PyPI"):
        update("2026.8.19", folder=tmp_path, get_json=offline)


def test_import_check_in_a_real_child_process(tmp_path):
    good = tmp_path / "good"
    zipfile.ZipFile(io.BytesIO(_wheel("yt_dlp", "2026.9.1"))).extractall(good)
    zipfile.ZipFile(io.BytesIO(_wheel("yt_dlp_ejs", "0.9.0"))).extractall(good)
    # The fake yt_dlp has no YoutubeDL, so the check must fail cleanly, not crash.
    with pytest.raises(UpdateError, match="doesn't load"):
        ytdlp_update._check_import(good)


# activate(): uses a stand-in package name, since the test process already imported
# the real yt_dlp (in the app, activate() runs before anything imports it).


def _install(folder, package, version, broken=False):
    zipfile.ZipFile(io.BytesIO(_wheel(package, version, broken))).extractall(folder / "current")


def test_activate_puts_a_good_update_first(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    _install(tmp_path, "fake_ytdlp_a", "2026.9.1")
    assert activate(tmp_path, module="fake_ytdlp_a") == "2026.9.1"
    assert sys.path[0] == str(tmp_path / "current")


def test_activate_sets_a_broken_update_aside(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    _install(tmp_path, "fake_ytdlp_b", "2026.9.1", broken=True)
    assert activate(tmp_path, module="fake_ytdlp_b") is None
    assert str(tmp_path / "current") not in sys.path
    assert "fake_ytdlp_b" not in sys.modules
    assert not (tmp_path / "current").exists()
    assert [p.name.startswith("broken-") for p in tmp_path.iterdir()] == [True]


def test_activate_without_an_update(tmp_path):
    assert activate(tmp_path) is None


@pytest.mark.network
def test_real_update_from_pypi(tmp_path):
    # Pretend we're on an ancient yt-dlp, so the real latest release gets installed
    # and import-checked in a child process.
    result = update("2000.1.1", folder=tmp_path)
    assert result.status == "updated"
    assert (tmp_path / "current" / "yt_dlp" / "version.py").exists()
    assert (tmp_path / "current" / "yt_dlp_ejs").is_dir()
