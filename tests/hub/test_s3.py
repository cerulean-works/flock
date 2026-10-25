"""The same reads and writes on S3 (the `s3_bucket` fixture), and credentials refreshed mid-stream."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

import flock.hub.config
from flock.hub import DatasetWriter, HubConfig, load_dataset, push
from flock.hub.config import REFRESH_MARGIN
from tests.conftest import S3Bucket
from tests.hub.utils import numbers, write


@pytest.fixture
def s3_hub(s3_bucket: S3Bucket) -> HubConfig:
    """Direct mode on a fresh bucket."""
    return HubConfig(
        root=f"s3://{s3_bucket.bucket}/{s3_bucket.prefix}datasets",
        storage_options=s3_bucket.storage_options(),
    )


def ids(ds) -> list[int]:
    return [row["id"] for row in ds]


def test_reads_versions_and_random_access(s3_hub: HubConfig, cache_dir: str) -> None:
    push(numbers(10), "flock/s3", hub_config=s3_hub)
    write(s3_hub, "flock/s3", numbers(10, start=10), mode="append", max_rows_per_file=4)

    for streaming in (True, False):
        latest = load_dataset(
            "flock/s3", streaming=streaming, hub_config=s3_hub, cache_dir=cache_dir
        )
        assert latest.version == 2 and ids(latest) == list(range(20))
        assert latest[13]["text"] == "row 13" and latest[-1]["id"] == 19
        assert latest.take_rows([19, 0, 0])["id"] == [19, 0, 0]
        v1 = load_dataset(
            "flock/s3", 1, streaming=streaming, hub_config=s3_hub, cache_dir=cache_dir
        )
        assert ids(v1) == list(range(10))
        filtered = load_dataset(
            "flock/s3",
            filter="id >= 15",
            streaming=streaming,
            hub_config=s3_hub,
            cache_dir=cache_dir,
        )
        assert ids(filtered) == list(range(15, 20)) and filtered[0]["id"] == 15

    # A commit after loading stays invisible.
    pinned = load_dataset("flock/s3", hub_config=s3_hub)
    push(numbers(1, start=99), "flock/s3", hub_config=s3_hub)
    assert ids(pinned) == list(range(20)) and pinned.latest_version == 3


def test_edits_tags_and_cards(s3_hub: HubConfig) -> None:
    ds = push(numbers(6), "flock/edits", hub_config=s3_hub)
    carded = ds.set_card("# On S3\n")
    assert carded.card == "# On S3\n" and ds.card is None
    carded.tag("v1.0")
    assert load_dataset("flock/edits", "v1.0", hub_config=s3_hub).card == "# On S3\n"
    deleted = carded.delete("id < 2")
    assert ids(deleted) == [2, 3, 4, 5]
    assert ids(deleted.checkout(1).restore()) == list(range(6))


def test_writer_resumes_on_s3(
    s3_hub: HubConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    push(numbers(2, start=-2), "flock/upload", hub_config=s3_hub)

    def run() -> int:
        writer = DatasetWriter("flock/upload", staging_dir=tmp_path / "s", hub_config=s3_hub)
        for i in range(3):
            writer.write_shard(f"{i}", numbers(3, start=3 * i))
        return writer.commit("from s3")

    def crash(*args: Any) -> None:
        raise RuntimeError("killed")

    monkeypatch.setattr(flock.hub.write, "_mark_committed", crash)
    with pytest.raises(RuntimeError):
        run()
    monkeypatch.undo()
    assert run() == 2
    assert ids(load_dataset("flock/upload", hub_config=s3_hub)) == [-2, -1, *range(9)]
    assert load_dataset("flock/upload", hub_config=s3_hub).latest_version == 2


class FakeAPI:
    """The Flock API's dataset and credential endpoints, handing out the bucket's keys."""

    def __init__(self, bucket: S3Bucket, ttl: float) -> None:
        self.bucket = bucket
        self.ttl = ttl
        self.now = 1_000_000.0
        self.issued = 0
        self.patches: list[tuple[str, Any]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer flk_test"
        path = request.url.path
        if path == "/api/v1/hub/credentials":
            self.issued += 1
            return httpx.Response(
                200,
                json={
                    "endpoint": self.bucket.endpoint,
                    "region": self.bucket.region,
                    "access_key_id": self.bucket.access_key_id,
                    "secret_access_key": self.bucket.secret_access_key,
                    # The test RGW knows no session tokens; the real API always sends one.
                    "session_token": "",
                    "expires_at": self.now + self.ttl,
                },
            )
        if request.method == "GET" and path.startswith("/api/v1/datasets/"):
            org, name = path.removeprefix("/api/v1/datasets/").split("/")
            return httpx.Response(
                200,
                json={
                    "id": f"uuid-{org}-{name}",
                    "org": org,
                    "name": name,
                    "uri": f"s3://{self.bucket.bucket}/{self.bucket.prefix}datasets/{org}/{name}",
                },
            )
        if request.method == "PATCH":
            self.patches.append((path, json.loads(request.content)))
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"error": "not found"})


@pytest.fixture
def api(s3_bucket: S3Bucket, monkeypatch: pytest.MonkeyPatch) -> FakeAPI:
    fake = FakeAPI(s3_bucket, ttl=REFRESH_MARGIN + 60)
    monkeypatch.setattr(flock.hub.config.time, "time", lambda: fake.now)
    return fake


def server_config(api: FakeAPI) -> HubConfig:
    return HubConfig(
        api_url="https://flock.test", api_key="flk_test", transport=httpx.MockTransport(api.handle)
    )


def test_credentials_refresh_during_a_long_stream(api: FakeAPI, s3_hub: HubConfig) -> None:
    write(s3_hub, "flock/long", numbers(12), max_rows_per_file=3)  # 4 fragments
    hub = server_config(api)
    ds = load_dataset("flock/long", batch_size=1, hub_config=hub)
    issued_at_load = api.issued

    seen = []
    for row in ds:
        seen.append(row["id"])
        api.now += 25  # credentials last 60 s past the refresh margin
    assert seen == list(range(12))
    # 12 rows x 25 s = 300 s: renewed several times, at fragment boundaries.
    assert api.issued - issued_at_load >= 3

    # Random access after the old credentials expired fetches new ones too.
    api.now += 10_000
    before = api.issued
    assert ds[11]["id"] == 11
    assert api.issued == before + 1


def test_server_mode_writes_and_messages(api: FakeAPI, tmp_path: Path) -> None:
    hub = server_config(api)
    pushed = push(numbers(4), "flock/served", message="first", hub_config=hub)
    assert pushed.version == 1 and ids(pushed) == [0, 1, 2, 3]
    deleted = pushed.delete("id = 0", message="drop row 0")
    assert ids(deleted) == [1, 2, 3]
    # Deletes can't carry a commit message, so it goes to the server.
    assert api.patches == [("/api/v1/datasets/flock/served/versions/2", {"message": "drop row 0"})]
    # The cache is keyed by the server's dataset id.
    assert (
        load_dataset(
            "flock/served", streaming=False, hub_config=hub, cache_dir=str(tmp_path)
        ).num_rows
        == 3
    )
