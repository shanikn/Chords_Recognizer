"""packaging/secrets_check.py: the build refuses to ship a Spotify key or user files."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "secrets_check", ROOT / "packaging" / "secrets_check.py"
)
secrets_check = importlib.util.module_from_spec(spec)
sys.modules["secrets_check"] = secrets_check
spec.loader.exec_module(secrets_check)

SECRET = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def app(tmp_path):
    folder = tmp_path / "ChordChart"
    (folder / "_internal").mkdir(parents=True)
    (folder / "ChordChart.exe").write_bytes(b"MZ" + b"\0" * 100)
    (folder / "_internal" / "base_library.zip").write_bytes(b"PK" + b"x" * 5000)
    return folder


def test_a_clean_build_passes(app):
    assert secrets_check.problems(app, [SECRET]) == []


def test_the_key_anywhere_in_any_file_is_found(app, monkeypatch):
    blob = b"a" * (secrets_check.CHUNK - 10) + SECRET.encode() + b"b" * 100  # across a chunk edge
    (app / "_internal" / "data.bin").write_bytes(blob)
    (app / "_internal" / "wide.txt").write_bytes(("x" + SECRET).encode("utf-16-le"))
    found = secrets_check.problems(app, [SECRET])
    assert [f.split(":")[0] for f in found] == [
        str(Path("_internal/data.bin")),
        str(Path("_internal/wide.txt")),
    ]


@pytest.mark.parametrize("name", ["settings.json", "spotify.json", "Settings.JSON"])
def test_user_files_are_refused_even_without_a_key(app, name):
    (app / "_internal" / name).write_text("{}")
    assert secrets_check.problems(app, []) != []


def test_local_secrets_from_environment_and_settings(tmp_path):
    (tmp_path / "settings.json").write_text(
        json.dumps({"spotify_client_id": "id-1234567890", "spotify_client_secret": "short"})
    )
    env = {"SPOTIFY_CLIENT_SECRET": SECRET}
    assert secrets_check.local_secrets(env, tmp_path) == sorted(
        [SECRET, "id-1234567890"]
    )  # "short": too short
    assert secrets_check.local_secrets({}, tmp_path / "missing") == []


def test_check_stops_the_build(app, monkeypatch):
    monkeypatch.setattr(secrets_check, "local_secrets", lambda: [SECRET])
    (app / "leak.txt").write_text(SECRET)
    with pytest.raises(SystemExit, match="leak.txt: contains this PC's Spotify key"):
        secrets_check.check(app)
