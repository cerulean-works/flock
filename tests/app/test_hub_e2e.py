"""The hub end to end: a flock, its S3 proxy and the test S3 server, used through `flock.hub` in
server mode (an API URL and API key, as on a training node).

Runs on moto by default. With `FLOCK_TEST_S3_ENDPOINT` (see `tests/conftest.py`) it runs
against a real S3 server: objects go under fresh dataset uuids in the shared bucket, and every
dataset is deleted (and its objects purged) at the end.
"""

import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import lance
import pyarrow as pa
import pytest

from flock.hub import DatasetNotFound, HubConfig, import_hf_dataset, load_dataset, push
from flock.hub.config import REFRESH_MARGIN
from flock.hub.importer import FAKE_ROOT_ENV, LocalSource
from tests.app.conftest import Client, Flocks, HubFlock
from tests.hub.hf_repos import MMLU, make_mmlu, mmlu_rows

DATASETS = "/api/v1/datasets"
# Temporary credentials outlive the client's refresh margin by this many seconds, so a test can
# watch them being replaced.
CREDENTIALS_LEFT = 3.0


@dataclass
class Person:
    email: str
    api: Client
    hub: HubConfig  # flock.hub in server mode, with the person's API key


@dataclass
class Env:
    server: HubFlock
    alice: Person  # member of flock
    bob: Person  # member of flock
    olga: Person  # member of lab only: an outsider to flock's datasets
    run: str

    def name(self, what: str) -> str:
        """A dataset name of this run's own."""
        return f"e2e-{self.run}-{what}"

    def versions(self, repo: str) -> list[dict]:
        listed = self.server.client().get(f"{DATASETS}/{repo}/versions")
        assert listed.status == 200, listed.body
        return listed.json()["versions"]


@pytest.fixture(scope="module")
def env(make_module_flock: Flocks, s3_server, tmp_path_factory) -> Iterator[Env]:
    server = make_module_flock.hub(
        s3_server.bucket(), hub_credentials_ttl=REFRESH_MARGIN + CREDENTIALS_LEFT
    )
    assert server.client().post("/api/v1/orgs", {"name": "lab"}).status == 201
    people = {}
    for email, org in [
        ("alice@flock.test", "flock"),
        ("bob@flock.test", "flock"),
        ("olga@flock.test", "lab"),
    ]:
        user = server.add_user(email, org=org)
        people[email] = Person(email, user.api, HubConfig(api_url=server.url, api_key=user.api_key))
    yield Env(
        server,
        people["alice@flock.test"],
        people["bob@flock.test"],
        people["olga@flock.test"],
        os.urandom(3).hex(),
    )
    # Leave nothing behind on a shared bucket.
    admin = server.client()
    for org in ("flock", "lab", server.store.settings.hub_mirror_org):
        for info in admin.get(f"{DATASETS}/{org}").json()["datasets"]:
            assert admin.delete(f"{DATASETS}/{org}/{info['name']}").status == 204
    server.call(server.store.datasets.flush())


@pytest.fixture(autouse=True)
def hf_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-streaming loads go to an HF cache of the test's own."""
    monkeypatch.setenv("HF_DATASETS_CACHE", str(tmp_path / "hf-cache"))


def table(start: int, n: int, writer: str) -> pa.Table:
    return pa.table(
        {
            "id": pa.array(range(start, start + n), pa.int64()),
            "text": [f"row {i}" for i in range(start, start + n)],
            "writer": [writer] * n,
        }
    )


def ids(dataset) -> list[int]:
    return [row["id"] for row in dataset]


# ---------------------------------------------------------------------- datasets


def test_dataset_lifecycle(env: Env):
    alice, bob = env.alice, env.bob
    repo = f"flock/{env.name('lifecycle')}"

    # push creates a missing dataset, private, as push_to_hub does.
    first = push(table(0, 10, "alice"), repo, message="first", hub_config=alice.hub)
    assert first.version == 1
    info = alice.api.get(f"{DATASETS}/{repo}").json()
    assert (info["visibility"], info["created_by"]["email"]) == ("private", alice.email)
    # The client's Lance URI comes from the API, its endpoint from POST /hub/credentials.
    assert first.uri == info["uri"] == f"s3://datasets/{repo}"
    assert alice.hub.lance_storage_options()["aws_endpoint"] == env.server.proxy.url

    # Another member appends to it; the first version stays as it was.
    second = push(table(10, 5, "bob"), repo, message="more", hub_config=bob.hub)
    assert (second.version, second.num_rows) == (2, 15)
    old = load_dataset(repo, version=1, hub_config=bob.hub)
    assert (old.version, ids(old)) == (1, list(range(10)))
    assert old[3]["text"] == "row 3"
    not_streamed = load_dataset(repo, version=1, streaming=False, hub_config=bob.hub)
    assert sorted(not_streamed["id"]) == list(range(10))

    # Edits through flock.hub: a delete with a message, a card, a tag, a restore.
    deleted = load_dataset(repo, hub_config=alice.hub).delete(
        "id < 3", message="drop the first three"
    )
    assert (deleted.version, deleted.num_rows) == (3, 12)
    carded = deleted.set_card("# Lifecycle\n\nA test dataset.\n", message="card")
    assert carded.version == 4
    assert carded.card == "# Lifecycle\n\nA test dataset.\n"
    load_dataset(repo, hub_config=bob.hub).tag("v1.0")
    restored = load_dataset(repo, version=2, hub_config=bob.hub).restore(message="back to two")
    assert (restored.version, restored.num_rows) == (5, 15)
    assert load_dataset(repo, version="v1.0", hub_config=alice.hub).version == 4

    # The history: ops, messages and authors, as the proxy recorded them.
    versions = env.versions(repo)
    assert [
        (v["version"], v["op"], v["message"], v["author"]["email"], v["tags"]) for v in versions
    ] == [
        (5, "Restore", "back to two", bob.email, []),
        (4, "UpdateConfig", "card", alice.email, ["v1.0"]),
        (3, "Delete", "drop the first three", alice.email, []),
        (2, "Append", "more", bob.email, []),
        (1, "Overwrite", "first", alice.email, []),
    ]
    assert [v["rows"] for v in versions] == [15, 12, 12, 15, 10]

    # The API's own writes are recorded as the acting user too.
    card = bob.api.request("PUT", f"{DATASETS}/{repo}/readme", body={"content": "# By Bob"})
    assert card.status == 200, card.body
    assert env.versions(repo)[0]["author"]["email"] == bob.email
    assert load_dataset(repo, hub_config=alice.hub).card == "# By Bob"


def test_streaming_resume(env: Env):
    repo = f"flock/{env.name('stream')}"
    for i in range(4):  # a fragment per push, so a shard each
        push(table(i * 25, 25, "alice"), repo, hub_config=env.alice.hub)
    ds = load_dataset(repo, batch_size=8, hub_config=env.bob.hub)
    assert ds.num_shards == 4
    for stop in (0, 13, 25, 60, 99, 100):
        ds = load_dataset(repo, batch_size=8, hub_config=env.bob.hub)
        iterator = iter(ds)
        seen = [next(iterator)["id"] for _ in range(stop)]
        resumed = load_dataset(repo, batch_size=8, hub_config=env.bob.hub)
        resumed.load_state_dict(ds.state_dict())
        assert seen + ids(resumed) == list(range(100)), stop


def test_credentials_are_refreshed(env: Env):
    repo = f"flock/{env.name('refresh')}"
    hub = HubConfig(api_url=env.server.url, api_key=env.alice.hub.api_key)
    push(table(0, 10, "alice"), repo, hub_config=hub)
    before = hub.lance_storage_options()["aws_access_key_id"]
    assert hub.lance_storage_options()["aws_access_key_id"] == before  # still fresh
    time.sleep(CREDENTIALS_LEFT + 0.5)
    after = hub.lance_storage_options()["aws_access_key_id"]
    assert after != before
    assert ids(load_dataset(repo, hub_config=hub)) == list(range(10))


def test_read_only_credentials_cannot_write(env: Env):
    repo = f"flock/{env.name('read-only')}"
    push(table(0, 5, "alice"), repo, hub_config=env.alice.hub)
    creds = env.server.credentials(env.alice.api, read_only=True)
    assert creds["endpoint"] == env.server.proxy.url
    options = {
        "aws_endpoint": creds["endpoint"],
        "aws_access_key_id": creds["access_key_id"],
        "aws_secret_access_key": creds["secret_access_key"],
        "aws_session_token": creds["session_token"],
        "aws_region": creds["region"],
        "allow_http": "true",
    }
    uri = f"s3://datasets/{repo}"
    assert lance.dataset(uri, storage_options=options).count_rows() == 5
    with pytest.raises(OSError, match="AccessDenied|Access Denied|403"):
        lance.write_dataset(table(5, 5, "alice"), uri, mode="append", storage_options=options)
    assert [v["version"] for v in env.versions(repo)] == [1]


def test_visibility_for_outsiders(env: Env):
    private = f"flock/{env.name('private')}"
    internal = f"flock/{env.name('internal')}"
    push(table(0, 5, "alice"), private, hub_config=env.alice.hub)
    push(table(0, 7, "alice"), internal, private=False, hub_config=env.alice.hub)
    assert env.alice.api.get(f"{DATASETS}/{internal}").json()["visibility"] == "internal"

    olga = env.olga.hub
    with pytest.raises(DatasetNotFound):
        load_dataset(private, auto_import=False, hub_config=olga)
    # Over S3 too, with her own credentials.
    with pytest.raises((OSError, ValueError), match="AccessDenied"):
        lance.dataset(f"s3://datasets/{private}", storage_options=olga.lance_storage_options())
    readable = load_dataset(internal, auto_import=False, hub_config=olga)
    assert ids(readable) == list(range(7))
    # Reading isn't writing.
    with pytest.raises(OSError, match="AccessDenied"):
        push(table(7, 1, "olga"), internal, hub_config=olga)
    assert [v["version"] for v in env.versions(internal)] == [1]


# ---------------------------------------------------------------------- mirrors


def test_mirror_fallback(env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "hf-repos"
    make_mmlu(root)
    # The API and its importer subprocess read the fake repos instead of Hugging Face.
    monkeypatch.setenv(FAKE_ROOT_ENV, str(root))
    sha = LocalSource(root).sha(MMLU, None)

    # Not on the hub: the server imports it through the proxy, and the client loads the mirror.
    ds = load_dataset(MMLU, config="anatomy", hub_config=env.alice.hub)
    mirror = "hf/cais-test--mmlu--anatomy"
    assert ds.repo_id == mirror
    expected = sum(mmlu_rows("anatomy", split) for split in ("test", "validation", "dev"))
    assert ds.num_rows == expected
    assert sorted({row["split"] for row in ds}) == ["dev", "test", "validation"]
    test_rows = load_dataset(
        MMLU, config="anatomy", filter="split = 'test'", hub_config=env.bob.hub
    )
    assert test_rows.num_rows == mmlu_rows("anatomy", "test")

    info = env.alice.api.get(f"{DATASETS}/{mirror}").json()
    assert info["mirror"] == {"repo": MMLU, "config": "anatomy", "sha": sha}
    # The requester is the author of the mirror's versions (its credentials were theirs).
    versions = env.versions(mirror)
    assert [(v["op"], v["author"]["email"]) for v in versions] == [
        ("UpdateConfig", env.alice.email),
        ("Overwrite", env.alice.email),
    ]
    assert versions[0]["tags"] == [f"hf-{sha[:8]}"]
    # Internal: an outsider loads it too, with no new import.
    again = load_dataset(MMLU, config="anatomy", hf_revision=sha, hub_config=env.olga.hub)
    assert (again.repo_id, again.version) == (mirror, ds.version)
    mirrors = env.alice.api.get("/api/v1/hub/mirrors", repo=MMLU, config="anatomy").json()
    assert len(mirrors["mirrors"]) == 1


def test_admins_import_into_the_mirror_org(env: Env, tmp_path: Path, monkeypatch):
    root = tmp_path / "hf-repos"
    make_mmlu(root)
    monkeypatch.setenv(FAKE_ROOT_ENV, str(root))
    # What a 422's command does, run by an admin of the mirror org with --target in it.
    admin = HubConfig(api_url=env.server.url, api_key=env.server.api_key)
    mirror = "hf/cais-test--mmlu--abstract_algebra"
    imported = import_hf_dataset(MMLU, "abstract_algebra", None, mirror, hub_config=admin)
    assert imported is not None
    # Members can't write there themselves.
    with pytest.raises(OSError, match="AccessDenied"):
        push({"x": [1]}, mirror, mode="overwrite", hub_config=env.alice.hub)

    # load_dataset finds it with no mirror record, and asks the server for no import.
    ds = load_dataset(MMLU, config="abstract_algebra", hub_config=env.bob.hub)
    assert (ds.repo_id, ds.version) == (mirror, imported.version)
    mirrors = env.bob.api.get("/api/v1/hub/mirrors", repo=MMLU, config="abstract_algebra")
    assert mirrors.json()["mirrors"] == []
