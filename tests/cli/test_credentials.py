"""`flock.cli.credentials`: the saved sign-in, and the order credentials are picked in."""

import ast
import json
import pickle
import stat
import sys
from pathlib import Path

import pytest

from flock.cli import credentials
from flock.cli.credentials import DEFAULT_HOST, Credentials, resolve


def test_the_file_is_private_and_written_whole(isolated):
    path = credentials.save("http://flock.test/", "flock_cli_secret", {"email": "a@b.c"})
    assert path == isolated / "credentials.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(isolated.stat().st_mode) == 0o700
    saved = json.loads(path.read_text())
    assert saved.keys() == {"host", "token", "user", "created_at"}
    assert (saved["host"], saved["token"]) == ("http://flock.test", "flock_cli_secret")

    # A write that fails leaves the previous sign-in, and no temporary file.
    def fail(*args, **kwargs):
        raise OSError("disk full")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(credentials.json, "dump", fail)
        with pytest.raises(OSError):
            credentials.save("http://other.test", "flock_cli_other", {})
    assert [p.name for p in isolated.iterdir()] == ["credentials.json"]
    assert json.loads(path.read_text())["token"] == "flock_cli_secret"

    assert credentials.delete() is True
    assert credentials.delete() is False
    assert credentials.load() is None


def test_an_unreadable_file_counts_as_no_sign_in(isolated):
    isolated.mkdir()
    (isolated / "credentials.json").write_text("{not json")
    assert credentials.load() is None
    (isolated / "credentials.json").write_text('{"host": "http://x.test"}')
    assert credentials.load() is None
    assert resolve() is None


def test_the_config_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("FLOCK_CONFIG_DIR")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert credentials.config_dir() == tmp_path / "home" / ".config" / "flock"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert credentials.config_dir() == tmp_path / "xdg" / "flock"
    monkeypatch.setenv("FLOCK_CONFIG_DIR", str(tmp_path / "flock"))
    assert credentials.credentials_path() == tmp_path / "flock" / "credentials.json"


def test_resolve_order(monkeypatch):
    assert resolve() is None
    credentials.save("http://saved.test", "flock_cli_saved", {})
    assert resolve() == Credentials("http://saved.test", "flock_cli_saved", "file")
    assert resolve("http://saved.test/") == Credentials(
        "http://saved.test", "flock_cli_saved", "file"
    )
    assert resolve("http://other.test") is None  # never sent to another server

    monkeypatch.setenv("FLOCK_HOST", "http://other.test")
    assert resolve() is None
    monkeypatch.delenv("FLOCK_HOST")

    monkeypatch.setenv("FLOCK_TOKEN", "flock-env-key")
    assert resolve() == Credentials(DEFAULT_HOST, "flock-env-key", "env")
    monkeypatch.setenv("FLOCK_HOST", "http://env.test")
    assert resolve() == Credentials("http://env.test", "flock-env-key", "env")
    assert resolve("http://arg.test") == Credentials("http://arg.test", "flock-env-key", "env")

    assert resolve(token="flock-arg") == Credentials("http://env.test", "flock-arg", "argument")
    monkeypatch.delenv("FLOCK_HOST")
    assert resolve(token="flock-arg") == Credentials(DEFAULT_HOST, "flock-arg", "argument")
    assert credentials.auth_headers(token="flock-arg") == {"Authorization": "Bearer flock-arg"}

    monkeypatch.delenv("FLOCK_TOKEN")
    credentials.delete()
    assert credentials.auth_headers() == {}


def test_credentials_hide_the_token_and_pickle():
    found = Credentials("http://flock.test", "flock_cli_secret", "file")
    assert "secret" not in repr(found)
    assert pickle.loads(pickle.dumps(found)) == found  # e.g. to DataLoader workers


def test_credentials_uses_only_the_standard_library():
    tree = ast.parse(Path(credentials.__file__).read_text())
    imported = {
        alias.name.partition(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.partition(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imported <= sys.stdlib_module_names, imported - sys.stdlib_module_names
