"""Where the hub is and how to reach its storage: the API, or a bucket or folder used directly."""

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from flock.hub.client import HubClient

CONFIG_FILE = Path("~/.config/flock/hub.json")
"""Read when `FLOCK_API_URL` or `FLOCK_API_KEY` isn't set: `{"api_url": ..., "api_key": ...}`."""

REFRESH_MARGIN = 300.0
"""Seconds before temporary credentials expire at which they are replaced."""


@dataclass
class HubConfig:
    """
    How `flock.hub` reaches datasets: through the Flock API, or straight to a bucket or folder.

    With `api_url` and `api_key`, dataset metadata comes from the REST API and S3 access uses temporary credentials from `POST /api/v1/hub/credentials`, renewed a few minutes before they expire. With `root`, datasets are `<root>/<org>/<name>`, read and written with `storage_options` as given, and calls that only exist on the server are skipped (creating datasets, version messages) or raise.

    The config pickles without its open connections and cached credentials, so DataLoader workers can take it along and fetch their own.

    Args:
        api_url (`str`, *optional*):
            The Flock API, for example `https://flock.cerulean.works`.
        api_key (`str`, *optional*):
            An API key, sent as `Authorization: Bearer <key>`.
        root (`str`, *optional*):
            Direct mode: a URI root such as `s3://<bucket>/datasets` or a local folder.
        storage_options (`dict[str, str]`, *optional*):
            Direct mode: Lance `storage_options` for `root`, such as `aws_endpoint` and keys.
        transport (`httpx.BaseTransport`, *optional*):
            Replaces httpx's network transport, for tests. It isn't pickled.

    Raises:
        `ValueError`: if neither `root` nor both `api_url` and `api_key` are given, or if both modes are.
    """

    api_url: str | None = None
    api_key: str | None = None
    root: str | None = None
    storage_options: dict[str, str] | None = None
    transport: httpx.BaseTransport | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        server = self.api_url is not None or self.api_key is not None
        if self.root is not None and server:
            raise ValueError("give either root (direct mode) or api_url and api_key, not both")
        if self.root is None and (self.api_url is None or self.api_key is None):
            raise ValueError(
                "the hub needs FLOCK_API_URL and FLOCK_API_KEY (or ~/.config/flock/hub.json), "
                "or a HubConfig with root= for direct access"
            )
        if self.root is not None:
            self.root = self.root.rstrip("/")
        self._lock = threading.Lock()
        self._credentials: dict[str, Any] | None = None
        self._client: HubClient | None = None

    @classmethod
    def from_env(cls) -> HubConfig:
        """Reads `FLOCK_API_URL` and `FLOCK_API_KEY`, each falling back to [`CONFIG_FILE`]."""
        saved: dict[str, str] = {}
        path = CONFIG_FILE.expanduser()
        if path.exists():
            saved = json.loads(path.read_text())
        return cls(
            api_url=os.environ.get("FLOCK_API_URL") or saved.get("api_url"),
            api_key=os.environ.get("FLOCK_API_KEY") or saved.get("api_key"),
        )

    @property
    def direct(self) -> bool:
        """Whether datasets are reached straight under `root`, without the API."""
        return self.root is not None

    @property
    def client(self) -> HubClient:
        """The REST client. Raises `RuntimeError` in direct mode."""
        if self.direct:
            raise RuntimeError("this needs the Flock API, but the hub config is in direct mode")
        if self._client is None:
            assert self.api_url is not None and self.api_key is not None
            self._client = HubClient(self.api_url, self.api_key, transport=self.transport)
        return self._client

    def dataset_uri(self, org: str, name: str) -> str:
        """The Lance URI of `org/name`: under `root`, or the proxy's `s3://datasets/<org>/<name>`."""
        if self.root is not None:
            return f"{self.root}/{org}/{name}"
        return f"s3://datasets/{org}/{name}"

    def lance_storage_options(self) -> dict[str, str]:
        """
        Lance `storage_options` that are valid for at least the next few minutes.

        In direct mode, a copy of `storage_options`. Otherwise temporary credentials, fetched on first use and again once fewer than [`REFRESH_MARGIN`] seconds are left.
        """
        if self.direct:
            return dict(self.storage_options or {})
        with self._lock:
            credentials = self._credentials
            if credentials is None or credentials["expires_at"] - time.time() < REFRESH_MARGIN:
                credentials = self._credentials = self.client.credentials()
        return _lance_options(credentials)

    def __getstate__(self) -> dict[str, Any]:
        # Locks, connections and transports don't pickle, and a worker should fetch credentials
        # of its own rather than share ones that may expire before it starts.
        state = {k: v for k, v in self.__dict__.items() if not k.startswith("_")}
        state["transport"] = None
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)
        self._lock = threading.Lock()
        self._credentials = None
        self._client = None


def _lance_options(credentials: dict[str, Any]) -> dict[str, str]:
    """Lance `storage_options` from a `POST /hub/credentials` answer."""
    options = {
        "aws_endpoint": credentials["endpoint"],
        "aws_access_key_id": credentials["access_key_id"],
        "aws_secret_access_key": credentials["secret_access_key"],
        "aws_region": credentials["region"],
    }
    # Always set by the API; a stand-in server for tests may hand out plain keys.
    if credentials.get("session_token"):
        options["aws_session_token"] = credentials["session_token"]
    if credentials["endpoint"].startswith("http://"):
        options["allow_http"] = "true"
    return options


_default: HubConfig | None = None


def get_default() -> HubConfig:
    """The config used when a call isn't given one: the last [`set_default`], else [`HubConfig.from_env`]."""
    global _default
    if _default is None:
        _default = HubConfig.from_env()
    return _default


def set_default(config: HubConfig | None) -> None:
    """Makes `config` the default for calls that aren't given one. `None` goes back to the environment."""
    global _default
    _default = config


def parse_repo_id(repo_id: str) -> tuple[str, str]:
    """
    Splits `org/name`.

    Raises:
        `ValueError`: if `repo_id` isn't two non-empty parts separated by one `/`.
    """
    org, sep, name = repo_id.partition("/")
    if not sep or not org or not name or "/" in name:
        raise ValueError(f"repo_id must look like 'org/name', got {repo_id!r}")
    return org, name
