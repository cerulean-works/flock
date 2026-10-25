"""Hugging Face mirrors (D50): the server imports into its mirror org with `python -m flock.hub.importer`, against fake repos."""

import threading
import time
from pathlib import Path

import lance
import pytest

from flock.hub.importer import FAKE_ROOT_ENV, LocalSource
from tests.hub.hf_repos import DOLMA, MMLU, make_dolma, make_mmlu

MIRRORS = "/api/v1/hub/mirrors"


@pytest.fixture
def repos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fake Hugging Face repos; the server and its importer subprocesses both read them."""
    root = tmp_path / "hf-repos"
    make_mmlu(root)
    make_dolma(root)
    monkeypatch.setenv(FAKE_ROOT_ENV, str(root))
    return root


@pytest.fixture
def hub(make_hub, repos):
    return make_hub()


def wait(api, mirror: dict, timeout: float = 120) -> dict:
    deadline = time.monotonic() + timeout
    while mirror["state"] == "running":
        assert time.monotonic() < deadline, mirror
        time.sleep(0.2)
        response = api.get(f"{MIRRORS}/{mirror['id']}")
        assert response.status == 200, response.body
        mirror = response.json()
    return mirror


def test_mirror_lifecycle(make_hub, hub, repos):
    api = hub.client()
    # Not a member of the mirror org: the server imports with mirror-writer credentials.
    member = hub.add_user("member@lab.test")
    sha = LocalSource(repos).sha(MMLU, None)
    listed = api.get(MIRRORS, repo=MMLU, config="anatomy").json()
    assert listed == {"dataset": "hf/cais-test--mmlu--anatomy", "mirrors": []}

    started = member.api.post(MIRRORS, {"repo": MMLU, "config": "anatomy"})
    assert started.status == 202, started.body
    mirror = started.json()
    assert mirror | {"id": 0, "started_at": 0, "progress": None} == {
        "id": 0, "state": "running", "repo": MMLU, "config": "anatomy", "sha": sha,
        "dataset": "hf/cais-test--mmlu--anatomy", "version": None, "error": None,
        "progress": None, "started_at": 0, "finished_at": None,
    }  # fmt: skip
    done = wait(api, mirror)
    assert done["state"] == "finished", done
    assert done["version"] == 2  # the data, then the card

    # Asking again, by branch or by sha, finds it: 200, the same id, no new import.
    for revision in (None, "main", sha):
        again = api.post(MIRRORS, {"repo": MMLU, "config": "anatomy", "revision": revision})
        assert again.status == 200, again.body
        assert again.json()["id"] == mirror["id"]
    listed = api.get(MIRRORS, repo=MMLU, config="anatomy").json()
    assert [m["id"] for m in listed["mirrors"]] == [mirror["id"]]

    # The mirror dataset: internal, its info names the mirrored repo, versions authored by the
    # requester (the proxy records the user of the mirror-writer credentials), the data tagged
    # and carded.
    path = "/api/v1/datasets/hf/cais-test--mmlu--anatomy"
    info = api.get(path).json()
    assert info["visibility"] == "internal"
    assert info["mirror"] == {"repo": MMLU, "config": "anatomy", "sha": sha}
    assert (info["latest_version"], info["rows"]) == (2, 7)
    versions = api.get(f"{path}/versions").json()["versions"]
    assert [(v["version"], v["op"]) for v in versions] == [(2, "UpdateConfig"), (1, "Overwrite")]
    assert {v["author"]["email"] for v in versions} == {"member@lab.test"}
    assert versions[0]["tags"] == [f"hf-{sha[:8]}"]
    assert versions[1]["message"].startswith(f"Import {MMLU} (anatomy)")
    readme = api.get(f"{path}/readme").json()["content"]
    assert f"commit `{sha}`" in readme
    upstream = lance.dataset(
        f"s3://{hub.bucket.bucket}/datasets/{info['id']}",
        storage_options=hub.bucket.storage_options(),
    )
    transaction = upstream.read_transaction(1)
    assert transaction is not None
    assert (transaction.transaction_properties or {})["flock.source"] == f"hf:{MMLU}@{sha}"

    # Anyone signed in can read it, members of the mirror org or not.
    assert member.api.get(path).status == 200
    rows = member.api.get(f"{path}/rows", limit=10).json()
    assert rows["total"] == 7
    assert member.api.get(f"{MIRRORS}/{mirror['id']}").json()["state"] == "finished"

    # A restart keeps finished mirrors and their datasets' mirror info.
    hub.stop()
    restarted = make_hub(reuse=hub)
    assert restarted.client().get(path).json()["mirror"]["sha"] == sha


def test_concurrent_requests_share_one_import(hub):
    api = hub.client()
    clients = [hub.client(), hub.client()]
    answers: list = [None, None]

    def ask(i: int) -> None:
        answers[i] = clients[i].post(MIRRORS, {"repo": MMLU, "config": "abstract_algebra"})

    threads = [threading.Thread(target=ask, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert [a.status for a in answers] == [202, 202], [a.body for a in answers]
    assert answers[0].json()["id"] == answers[1].json()["id"]
    assert len(api.get(MIRRORS, repo=MMLU, config="abstract_algebra").json()["mirrors"]) == 1
    assert wait(api, answers[0].json())["state"] == "finished"
    count = hub.call(hub.store.db.fetchval("SELECT count(*) FROM hub_mirrors"))
    assert count == 1


def test_too_large(make_hub, repos):
    server = make_hub(hub_auto_import_max_bytes=100)
    api = server.client()
    sha = LocalSource(repos).sha(DOLMA, None)
    refused = api.post(MIRRORS, {"repo": DOLMA})
    assert refused.status == 422, refused.body
    body = refused.json()
    assert "more than the 100" in body["error"]
    # Into the requester's own org: only the mirror org's admins may write to it directly.
    assert body["command"] == (
        f"flock-hub import --repo {DOLMA} --revision {sha} "
        "--target flock/allenai-test--dolma --workdir allenai-test--dolma"
    )
    assert server.client().post("/api/v1/orgs", {"name": "lab"}).status == 201
    member = server.add_user("member@lab.test", org="lab")
    command = member.api.post(MIRRORS, {"repo": DOLMA}).json()["command"]
    assert "--target lab/allenai-test--dolma" in command
    assert api.get(MIRRORS, repo=DOLMA).json()["mirrors"] == []
    assert server.call(server.store.db.fetchval("SELECT count(*) FROM hub_mirrors")) == 0


def test_bad_requests(hub):
    api = hub.client()
    assert api.post(MIRRORS, {"repo": "nobody/nothing"}).status == 404
    assert api.post(MIRRORS, {"repo": MMLU}).status == 400  # several configs, none given
    assert api.post(MIRRORS, {"repo": MMLU, "config": "nope"}).status == 400
    assert api.post(MIRRORS, {"repo": MMLU, "config": "anatomy", "revision": "v9"}).status == 404
    for bad in ({"repo": "a/b/c"}, {"repo": "../x"}, {"repo": MMLU, "config": "a/b"}, {}):
        assert api.post(MIRRORS, bad).status == 422, bad
    assert api.get(f"{MIRRORS}/12345").status == 404


def test_hub_off(api):
    assert api.post(MIRRORS, {"repo": MMLU}).status == 503
    assert api.get(MIRRORS, repo=MMLU).status == 503


def test_failure_is_recorded_and_retried(hub, repos):
    api = hub.client()
    broken = repos / "someone" / "broken"
    broken.mkdir(parents=True)
    (broken / "data.parquet").write_bytes(b"not a parquet file")
    mirror = api.post(MIRRORS, {"repo": "someone/broken"}).json()
    failed = wait(api, mirror)
    assert failed["state"] == "failed"
    assert failed["error"]
    assert failed["finished_at"] is not None

    # Asking again starts over, in the same row.
    retried = api.post(MIRRORS, {"repo": "someone/broken"})
    assert retried.status == 202
    assert retried.json()["id"] == mirror["id"]
    assert retried.json()["error"] is None
    assert wait(api, retried.json())["state"] == "failed"


def test_restart_marks_running_mirrors_failed(make_hub, hub):
    hub.call(
        hub.store.db.execute(
            "INSERT INTO hub_mirrors (repo, config, sha, state, started_at) "
            "VALUES ('o/r', '', $1, 'running', now())",
            "a" * 40,
        )
    )
    hub.stop()
    restarted = make_hub(reuse=hub)
    mirror = restarted.client().get(MIRRORS, repo="o/r").json()["mirrors"][0]
    assert mirror["state"] == "failed"
    assert "restarted" in mirror["error"]
