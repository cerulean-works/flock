"""Where `flock login` keeps its sign-in, and how every Flock client finds a credential.

This module uses only the standard library, so any Flock package can depend on it cheaply.
"""

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_HOST = "https://flock-api.cerulean.works"
"""The Flock API used when neither `--host`, `FLOCK_HOST` nor a saved sign-in names one."""


@dataclass(frozen=True)
class Credentials:
    """
    A Flock API and the Bearer token to send it.

    Args:
        host (`str`):
            The Flock API's URL, without a trailing slash.
        token (`str`):
            A CLI token, from `flock login` or `flock token`. It isn't shown in the `repr`.
        source (`str`):
            Where it came from: `"argument"`, `"env"` (`FLOCK_TOKEN`) or `"file"` (`flock login`).
    """

    host: str
    token: str = field(repr=False)
    source: str

    def auth_headers(self) -> dict[str, str]:
        """The `Authorization` header carrying the token."""
        return {"Authorization": f"Bearer {self.token}"}


def normalize_host(host: str) -> str:
    """`host` without a trailing slash, with `https://` added when it names no scheme."""
    host = host.strip().rstrip("/")
    return host if "://" in host else f"https://{host}"


def config_dir() -> Path:
    """`FLOCK_CONFIG_DIR`, else `$XDG_CONFIG_HOME/flock`, else `~/.config/flock`."""
    if configured := os.environ.get("FLOCK_CONFIG_DIR"):
        return Path(configured).expanduser()
    if xdg := os.environ.get("XDG_CONFIG_HOME"):
        return Path(xdg).expanduser() / "flock"
    return Path.home() / ".config" / "flock"


def credentials_path() -> Path:
    """The file `flock login` saves its sign-in to: `<config dir>/credentials.json`."""
    return config_dir() / "credentials.json"


def load() -> dict[str, Any] | None:
    """
    The saved sign-in, `{"host", "token", "user", "created_at"}`, or `None` when there's none.

    A file that can't be read or doesn't hold a host and token counts as none.
    """
    try:
        saved = json.loads(credentials_path().read_text())
    except OSError, ValueError:
        return None
    if not isinstance(saved, dict) or not saved.get("host") or not saved.get("token"):
        return None
    saved["host"] = normalize_host(saved["host"])
    return saved


def save(host: str, token: str, user: dict[str, Any], **extra: Any) -> Path:
    """
    Saves a sign-in, replacing any other. Returns the file's path.

    The file is written atomically (a temporary file, then a rename) and only its owner can
    read it (mode 0600).

    Args:
        host (`str`):
            The Flock API's URL.
        token (`str`):
            The CLI token.
        user (`dict`):
            Who signed in, as `GET /api/v1/me` describes them.
        extra:
            More fields to save alongside.
    """
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    body = {
        "host": normalize_host(host),
        "token": token,
        "user": user,
        "created_at": time.time(),
        **extra,
    }
    # mkstemp creates the file with mode 0600 before anything is written to it.
    fd, temporary = tempfile.mkstemp(prefix=".credentials-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(body, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def delete() -> bool:
    """Deletes the saved sign-in. Returns whether there was one."""
    try:
        credentials_path().unlink()
    except FileNotFoundError:
        return False
    return True


def resolve(host: str | None = None, token: str | None = None) -> Credentials | None:
    """
    The credential a Flock client should use, or `None` when there's none.

    Picks the first that exists:

    1. explicit arguments: `token`, sent to `host`;
    2. the `FLOCK_TOKEN` environment variable (a CLI token: `flock token`), sent to `host`;
    3. the sign-in `flock login` saved, unless it's for another server than `host`.

    `host` defaults to `FLOCK_HOST`. In the first two cases, without either, it is
    [`DEFAULT_HOST`].

    Args:
        host (`str`, *optional*):
            The Flock API to talk to.
        token (`str`, *optional*):
            A token to use as is.
    """
    wanted = host or os.environ.get("FLOCK_HOST")
    wanted = normalize_host(wanted) if wanted else None
    if token:
        return Credentials(wanted or DEFAULT_HOST, token, "argument")
    if env := os.environ.get("FLOCK_TOKEN"):
        return Credentials(wanted or DEFAULT_HOST, env, "env")
    saved = load()
    if saved is not None and wanted in (None, saved["host"]):
        return Credentials(saved["host"], saved["token"], "file")
    return None


def auth_headers(host: str | None = None, token: str | None = None) -> dict[str, str]:
    """The `Authorization` header of the credential [`resolve`] picks; empty when there's none."""
    credentials = resolve(host, token)
    return credentials.auth_headers() if credentials is not None else {}
