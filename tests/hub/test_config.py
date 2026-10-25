"""The hub config and REST client: environment, credentials and their refresh, errors."""

import json
import pickle
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

import flock.hub.config
from flock.hub import DatasetNotFound, HubConfig, HubError, create_dataset, load_dataset
from flock.hub.config import REFRESH_MARGIN, get_default, parse_repo_id, set_default


class FakeAPI:
    """A stand-in Flock API over httpx's `MockTransport`, recording its requests."""

    def __init__(self, ttl: float = 3600.0) -> None:
        self.ttl = ttl
        self.now = 1_000_000.0
        self.requests: list[httpx.Request] = []
        self.issued = 0
        self.routes: dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]] = {
            ("POST", "/api/v1/hub/credentials"): self.credentials,
        }

    def credentials(self, request: httpx.Request) -> httpx.Response:
        self.issued += 1
        return httpx.Response(
            200,
            json={
                "endpoint": "http://hub-s3.test:9000",
                "region": "us-east-1",
                "access_key_id": f"FLKT{self.issued}",
                "secret_access_key": "secret",
                "session_token": f"token-{self.issued}",
                "expires_at": self.now + self.ttl,
            },
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get((request.method, request.url.path))
        if route is None:
            return httpx.Response(404, json={"error": "dataset not found"})
        return route(request)

    def config(self) -> HubConfig:
        return HubConfig(
            api_url="https://flock.test",
            api_key="flk_key",
            transport=httpx.MockTransport(self.handle),
        )


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> FakeAPI:
    fake = FakeAPI()
    monkeypatch.setattr(flock.hub.config.time, "time", lambda: fake.now)
    return fake


def test_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FLOCK_API_URL", "https://flock.test")
    monkeypatch.setenv("FLOCK_API_KEY", "flk_env")
    config = HubConfig.from_env()
    assert (config.api_url, config.api_key, config.direct) == (
        "https://flock.test",
        "flk_env",
        False,
    )


def test_from_config_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FLOCK_API_URL", raising=False)
    monkeypatch.setenv("FLOCK_API_KEY", "flk_env")
    path = tmp_path / ".config" / "flock" / "hub.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"api_url": "https://saved.test", "api_key": "flk_saved"}))
    config = HubConfig.from_env()
    # Each value falls back on its own: the environment wins where it is set.
    assert (config.api_url, config.api_key) == ("https://saved.test", "flk_env")


def test_missing_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FLOCK_API_URL", raising=False)
    monkeypatch.delenv("FLOCK_API_KEY", raising=False)
    with pytest.raises(ValueError, match="FLOCK_API_URL and FLOCK_API_KEY"):
        HubConfig.from_env()
    with pytest.raises(ValueError, match="not both"):
        HubConfig(api_url="https://flock.test", api_key="k", root="/data")


def test_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = HubConfig(root=str(tmp_path))
    set_default(config)
    try:
        assert get_default() is config
        create_dataset("flock/x")  # uses it
    finally:
        set_default(None)
    monkeypatch.setenv("FLOCK_API_URL", "https://flock.test")
    monkeypatch.setenv("FLOCK_API_KEY", "flk_env")
    assert get_default().api_key == "flk_env"
    set_default(None)


def test_dataset_uris(tmp_path: Path, api: FakeAPI) -> None:
    assert HubConfig(root=str(tmp_path) + "/").dataset_uri("flock", "x") == f"{tmp_path}/flock/x"
    assert HubConfig(root="s3://bucket/datasets").dataset_uri("flock", "x") == (
        "s3://bucket/datasets/flock/x"
    )
    assert api.config().dataset_uri("flock", "x") == "s3://datasets/flock/x"


def test_parse_repo_id() -> None:
    assert parse_repo_id("flock/fineweb") == ("flock", "fineweb")
    for bad in ("fineweb", "/x", "flock/", "a/b/c", ""):
        with pytest.raises(ValueError, match="org/name"):
            parse_repo_id(bad)


def test_direct_storage_options_are_copies(tmp_path: Path) -> None:
    config = HubConfig(root="s3://b/d", storage_options={"aws_region": "eu"})
    options = config.lance_storage_options()
    options["aws_region"] = "changed"
    assert config.lance_storage_options() == {"aws_region": "eu"}
    with pytest.raises(RuntimeError, match="direct mode"):
        config.client  # noqa: B018


def test_credentials_become_storage_options(api: FakeAPI) -> None:
    options = api.config().lance_storage_options()
    assert options == {
        "aws_endpoint": "http://hub-s3.test:9000",
        "aws_access_key_id": "FLKT1",
        "aws_secret_access_key": "secret",
        "aws_session_token": "token-1",
        "aws_region": "us-east-1",
        "allow_http": "true",
    }
    request = api.requests[0]
    assert request.headers["authorization"] == "Bearer flk_key"
    assert json.loads(request.content) == {"read_only": False}


def test_https_endpoint_needs_no_allow_http(api: FakeAPI) -> None:
    original = api.credentials

    def https(request: httpx.Request) -> httpx.Response:
        response = original(request)
        body = response.json() | {"endpoint": "https://hub-s3.test"}
        return httpx.Response(200, json=body)

    api.routes[("POST", "/api/v1/hub/credentials")] = https
    assert "allow_http" not in api.config().lance_storage_options()


def test_credentials_refresh_before_expiry(api: FakeAPI) -> None:
    api.ttl = REFRESH_MARGIN + 60
    config = api.config()
    assert api.issued == 0  # fetched on first use, not at creation
    assert config.lance_storage_options()["aws_access_key_id"] == "FLKT1"
    api.now += 30
    assert config.lance_storage_options()["aws_access_key_id"] == "FLKT1"  # still cached
    api.now += 31  # now under REFRESH_MARGIN from expiry
    assert config.lance_storage_options()["aws_access_key_id"] == "FLKT2"
    assert api.issued == 2
    api.now += 59
    assert config.lance_storage_options()["aws_access_key_id"] == "FLKT2"
    api.now += 10_000  # long expired
    assert config.lance_storage_options()["aws_access_key_id"] == "FLKT3"


def test_pickling_drops_credentials_and_transport(api: FakeAPI) -> None:
    config = api.config()
    config.lance_storage_options()
    clone = pickle.loads(pickle.dumps(config))
    assert (clone.api_url, clone.api_key, clone.transport) == (
        "https://flock.test",
        "flk_key",
        None,
    )
    assert clone._credentials is None and clone._client is None


def test_errors(api: FakeAPI) -> None:
    config = api.config()
    with pytest.raises(DatasetNotFound, match="404 dataset not found"):
        config.client.get_dataset("flock", "missing")
    with pytest.raises(DatasetNotFound):
        load_dataset("flock/missing", hub_config=config)

    api.routes[("GET", "/api/v1/datasets/flock/broken")] = lambda r: httpx.Response(
        500, json={"error": "boom"}
    )
    with pytest.raises(HubError, match="500 boom") as caught:
        config.client.get_dataset("flock", "broken")
    assert caught.value.status == 500 and not isinstance(caught.value, DatasetNotFound)

    api.routes[("POST", "/api/v1/hub/credentials")] = lambda r: httpx.Response(401, text="nope")
    with pytest.raises(HubError, match="401 nope"):
        config.lance_storage_options()

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    down = HubConfig(
        api_url="https://x.test", api_key="k", transport=httpx.MockTransport(unreachable)
    )
    with pytest.raises(HubError, match="refused") as caught:
        down.client.credentials()
    assert caught.value.status is None


def test_create_dataset_on_the_server(api: FakeAPI) -> None:
    info = {"id": "uuid-1", "org": "flock", "name": "new", "uri": "s3://datasets/flock/new"}
    api.routes[("POST", "/api/v1/datasets/flock")] = lambda r: httpx.Response(201, json=info)
    assert create_dataset("flock/new", "About it", "internal", hub_config=api.config()) == info
    assert json.loads(api.requests[-1].content) == {
        "name": "new",
        "description": "About it",
        "visibility": "internal",
    }


def test_set_version_message(api: FakeAPI) -> None:
    seen: list[Any] = []

    def patch(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"version": 3, "message": "m"})

    api.routes[("PATCH", "/api/v1/datasets/flock/x/versions/3")] = patch
    api.config().client.set_version_message("flock", "x", 3, "m")
    assert seen == [{"message": "m"}]
