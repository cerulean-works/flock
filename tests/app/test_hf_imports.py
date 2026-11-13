"""Hugging Face imports from the web app (017, D124): starting, following and cancelling them,
the size limit, imports cut short by a restart, the access token kept out of the database and
the logs, and whole imports of a model and a dataset on moto. The Hub, its files and its
dataset viewer are an httpx mock (for the API's own client and for huggingface_hub's)."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

import asyncpg
import httpx
import httpx2
import huggingface_hub
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from huggingface_hub.utils import _http

from app.api.src.core.causal_lms import CAUSAL_LMS
from app.api.src.core.hf_imports import HFImports
from app.api.src.core.models import Models
from tests.app.conftest import Client, Flocks, S3Flock
from tests.conftest import S3Server

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator


IMPORTS = "/api/v1/orgs/flock/imports/hf"
SHA = "c" * 40
TOKEN = "hf_test_secret_token_42"
HUB = "https://hub.test"
VIEWER = "https://viewer.test"
MIB = 1024 * 1024
SIZES: dict[str, Any] = {
    "vocab_size": 64,
    "hidden_size": 16,
    "intermediate_size": 32,
    "num_hidden_layers": 2,
    "num_attention_heads": 2,
    "num_key_value_heads": 1,
    "max_position_embeddings": 32,
    "bos_token_id": 1,
    "eos_token_id": 1,
}


# ---------------------------------------------------------------------- the fake Hub


@dataclass
class Repo:
    files: dict[str, bytes]
    private: bool = False
    card: dict[str, Any] | None = None


@dataclass
class FakeHub:
    """The Hub's API, resolve URLs (with ranges) and dataset viewer, as httpx transports.

    Downloads of ``held`` files send their first bytes, then wait for ``gate`` (and end there
    if ``cut``)."""

    models: dict[str, Repo] = field(default_factory=dict)
    datasets: dict[str, Repo] = field(default_factory=dict)
    viewer: dict[str, dict[str, Any]] = field(default_factory=dict)
    held: set[str] = field(default_factory=set)
    gate: threading.Event = field(default_factory=threading.Event)
    requests: list[tuple[str, str | None]] = field(default_factory=list)
    cut: bool = False

    def __post_init__(self) -> None:
        self.gate.set()

    def answer(
        self, url: httpx.URL | httpx2.URL, headers: Any
    ) -> tuple[int, dict[str, str], bytes, bool]:
        auth = headers.get("authorization")
        self.requests.append((str(url), auth))
        path = unquote(url.path)
        if url.host == "viewer.test":
            repo = url.params.get("dataset", "")
            found = self.viewer.get(repo, {}).get(path.strip("/"))
            if found is None:
                return 404, {}, b'{"error": "not supported"}', False
            return 200, {"content-type": "application/json"}, json.dumps(found).encode(), False
        if m := re.fullmatch(r"/api/(models|datasets)/([^/]+/[^/]+)/revision/(.+)", path):
            kind, name = m[1], m[2]
            repo = (self.models if kind == "models" else self.datasets).get(name)
            if repo is None or (repo.private and auth != f"Bearer {TOKEN}"):
                return 401, {"x-error-code": "RepoNotFound"}, b"{}", False
            if m[3] not in ("main", SHA):
                return 404, {"x-error-code": "RevisionNotFound"}, b"{}", False
            body = {
                "id": name,
                "sha": SHA,
                "siblings": [{"rfilename": f, "size": len(d)} for f, d in repo.files.items()],
                "cardData": repo.card,
            }
            return 200, {"content-type": "application/json"}, json.dumps(body).encode(), False
        if m := re.fullmatch(r"/(datasets/)?([^/]+/[^/]+)/resolve/([^/]+)/(.+)", path):
            repo = (self.datasets if m[1] else self.models).get(m[2])
            if repo is None or (repo.private and auth != f"Bearer {TOKEN}"):
                return 401, {"x-error-code": "RepoNotFound"}, b"", False
            data = repo.files.get(m[4])
            if data is None:
                return 404, {"x-error-code": "EntryNotFound"}, b"", False
            if r := re.fullmatch(r"bytes=(\d+)-(\d*)", headers.get("range") or ""):
                start = int(r[1])
                end = int(r[2]) if r[2] else len(data) - 1
                part = data[start : end + 1]
                content_range = f"bytes {start}-{start + len(part) - 1}/{len(data)}"
                # Only whole downloads are held: a header's ranged reads go through.
                held = not r[2] and m[4] in self.held
                return 206, {"content-range": content_range}, part, held
            return 200, {}, data, m[4] in self.held
        return 404, {}, b"", False

    async def _slowly(self, data: bytes) -> AsyncIterator[bytes]:
        yield data[:1024]
        while not self.gate.is_set():
            await asyncio.sleep(0.02)
        if not self.cut:
            yield data[1024:]

    def transport(self) -> httpx.MockTransport:
        async def handle(request: httpx.Request) -> httpx.Response:
            status, headers, body, held = self.answer(request.url, request.headers)
            if held:
                return httpx.Response(status, headers=headers, content=self._slowly(body))
            return httpx.Response(status, headers=headers, content=body)

        return httpx.MockTransport(handle)

    def sync_transport(self) -> httpx2.MockTransport:
        """For huggingface_hub, which plans a dataset's download."""

        def handle(request: httpx2.Request) -> httpx2.Response:
            status, headers, body, _ = self.answer(request.url, request.headers)
            return httpx2.Response(status, headers=headers, content=body)

        return httpx2.MockTransport(handle)

    def authorized(self) -> list[str | None]:
        return [auth for url, auth in self.requests if not url.startswith(VIEWER)]


@pytest.fixture
def hub() -> Iterator[FakeHub]:
    fake = FakeHub()
    huggingface_hub.set_client_factory(
        lambda: httpx2.Client(transport=fake.sync_transport(), follow_redirects=True)
    )
    yield fake
    fake.gate.set()
    huggingface_hub.set_client_factory(_http.default_client_factory)


def serve(server: S3Flock, hub: FakeHub) -> S3Flock:
    server.store.hf_imports.transport = hub.transport()
    return server


@pytest.fixture
def make_s3(make_flock: Flocks, s3_server: S3Server, hub: FakeHub) -> Callable[..., S3Flock]:
    def make(reuse: S3Flock | None = None, **settings: Any) -> S3Flock:
        settings.setdefault("hf_endpoint", HUB)
        settings.setdefault("hf_datasets_server", VIEWER)
        settings.setdefault("hf_import_workers", 2)
        return serve(make_flock.s3(s3_server, reuse=reuse, **settings), hub)

    return make


@pytest.fixture
def flock(make_s3: Callable[..., S3Flock]) -> S3Flock:
    return make_s3()


@pytest.fixture(autouse=True)
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    """Heartbeats often enough that the database follows a held import."""
    monkeypatch.setattr(HFImports, "HEARTBEAT", 0.2)


def sql(server: S3Flock, query: str, *args: Any) -> list[asyncpg.Record]:
    async def run() -> list[asyncpg.Record]:
        conn = await asyncpg.connect(server.database_url)
        try:
            return await conn.fetch(query, *args)
        finally:
            await conn.close()

    return asyncio.run(run())


def model_repo(folder: Path, *, shard: str | None = None, **sizes: Any) -> Repo:
    """A tiny transformers model saved as a Hub repository holds it."""
    import torch
    from transformers import AutoModelForCausalLM, LlamaConfig

    torch.manual_seed(0)
    model = AutoModelForCausalLM.from_config(LlamaConfig(**{**SIZES, **sizes}))
    model.save_pretrained(folder, **({"max_shard_size": shard} if shard else {}))
    (folder / "README.md").write_text("# tiny\n")
    (folder / "tokenizer_config.json").write_text("{}")
    (folder / "notes.txt").write_text("not copied")
    return Repo({p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()})


def parquet(rows: list[dict[str, Any]]) -> bytes:
    sink = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows), sink)
    return sink.getvalue()


def dataset_repo() -> Repo:
    return Repo(
        {
            "README.md": b"# tiny data\n",
            "data/train-00000-of-00001.parquet": parquet(
                [{"text": f"row {i}", "n": i} for i in range(5)]
            ),
            "data/test-00000-of-00001.parquet": parquet([{"text": "held out", "n": 99}]),
        }
    )


def finished(api: Client, pk: int, wait_for: Callable[..., Any], timeout: float = 120) -> dict:
    def ended() -> dict | None:
        job = api.get(f"{IMPORTS}/{pk}").json()
        return job if job["state"] != "running" else None

    return wait_for(ended, timeout=timeout)


def model_body(**fields: Any) -> dict[str, Any]:
    return {"kind": "model", "repo": "acme/tiny", "name": "tiny", **fields}


def version_files(server: S3Flock, name: str, number: int) -> dict[str, bytes]:
    """A version's files, as stored upstream."""
    org = server.call(server.store.org("flock"))
    bucket = f"{server.bucket_prefix}{org.pk:05d}-flock-models"
    version = server.client().get(f"/api/v1/orgs/flock/models/{name}/versions/{number}").json()
    s3 = server.s3.client()
    listed = s3.list_objects_v2(Bucket=bucket, Prefix=f"{name}/u/")["Contents"]
    keys = {o["Key"].rpartition("/")[2]: o["Key"] for o in listed}
    return {
        f["path"]: s3.get_object(Bucket=bucket, Key=keys[f["path"]])["Body"].read()
        for f in version["files"]
    }


def expected_files(folder: Path, tmp_path: Path) -> dict[str, bytes]:
    """What ``flock models import`` stages for the same repository and commit."""
    from flock.hub.models import hf

    staging = tmp_path / "staged"
    hf.stage(hf.Snapshot(folder, "acme/tiny", SHA), staging)
    return {p.name: p.read_bytes() for p in staging.iterdir()}


def without_time(files: dict[str, bytes]) -> dict[str, Any]:
    out: dict[str, Any] = dict(files)
    provenance = json.loads(out.pop("flock.json"))
    assert provenance.pop("saved_at")
    out["flock.json"] = provenance
    return out


# ---------------------------------------------------------------------- the model list


def test_the_causal_lm_list_matches_transformers() -> None:
    from transformers.models.auto.modeling_auto import MODEL_FOR_CAUSAL_LM_MAPPING_NAMES

    assert dict(MODEL_FOR_CAUSAL_LM_MAPPING_NAMES) == CAUSAL_LMS, (
        "write app/api/src/core/causal_lms.py again: uv run python -m app.api.src.core.causal_lms"
    )


# ---------------------------------------------------------------------- models


def test_a_model_import_makes_the_version_the_cli_makes(
    flock: S3Flock, hub: FakeHub, tmp_path: Path, wait_for: Callable[..., Any]
) -> None:
    hub.models["acme/tiny"] = model_repo(tmp_path / "repo", shard="20KB")
    api = flock.client()
    started = api.post(IMPORTS, model_body(tags=["base"], primary=True))
    assert started.status == 202, started.body
    job = finished(api, started.json()["id"], wait_for)
    assert job["state"] == "finished", job["error"]
    assert job["result"] == {"model": "tiny", "version": 1}
    assert job["sha"] == SHA
    assert job["progress"] == 1.0
    assert job["bytes_done"] == job["bytes_total"] > 0
    assert job["started_by"]["email"] == flock.ADMIN_EMAIL

    stored = version_files(flock, "tiny", 1)
    assert "model.safetensors.index.json" in stored
    assert "notes.txt" not in stored
    assert without_time(stored) == without_time(expected_files(tmp_path / "repo", tmp_path))
    model = api.get("/api/v1/orgs/flock/models/tiny").json()
    assert model["primary"] == 1
    assert [t["name"] for t in model["tags"]] == ["base"]

    # The same commit again answers the version it made.
    again = api.post(IMPORTS, model_body())
    assert again.status == 202
    job = finished(api, again.json()["id"], wait_for)
    assert job["result"] == {"model": "tiny", "version": 1, "existing": True}


def test_a_large_model_streams_in_parts(
    flock: S3Flock,
    hub: FakeHub,
    tmp_path: Path,
    wait_for: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Models, "PART_SIZE", 5 * MIB)
    hub.models["acme/tiny"] = model_repo(
        tmp_path / "repo", vocab_size=2048, hidden_size=1024, num_hidden_layers=1
    )
    assert len(hub.models["acme/tiny"].files["model.safetensors"]) > 3 * 5 * MIB
    api = flock.client()
    job = finished(api, api.post(IMPORTS, model_body()).json()["id"], wait_for)
    assert job["state"] == "finished", job["error"]
    stored = version_files(flock, "tiny", 1)
    assert without_time(stored) == without_time(expected_files(tmp_path / "repo", tmp_path))


def test_progress_is_saved_and_a_cancel_aborts_the_upload(
    flock: S3Flock, hub: FakeHub, tmp_path: Path, wait_for: Callable[..., Any]
) -> None:
    hub.models["acme/tiny"] = model_repo(tmp_path / "repo")
    hub.held.add("model.safetensors")
    hub.gate.clear()
    api = flock.client()
    started = api.post(IMPORTS, model_body())
    assert started.status == 202, started.body
    pk = started.json()["id"]
    assert started.json()["state"] == "running"

    job = wait_for(lambda: (j := api.get(f"{IMPORTS}/{pk}").json())["bytes_done"] > 0 and j)
    assert job["state"] == "running" and job["phase"] == "uploading"
    assert 0 < job["progress"] < 1
    # Saved to the database too, for the other replicas.
    wait_for(lambda: sql(flock, "SELECT bytes_done FROM hf_imports WHERE pk = $1", pk)[0][0])
    listed = api.get(IMPORTS).json()["imports"]
    assert [(j["id"], j["state"]) for j in listed] == [(pk, "running")]

    cancelled = api.delete(f"{IMPORTS}/{pk}")
    assert cancelled.status == 200
    assert cancelled.json()["state"] == "cancelled"
    assert sql(flock, "SELECT state FROM model_uploads")[0][0] == "aborted"
    org = flock.call(flock.store.org("flock"))
    bucket = f"{flock.bucket_prefix}{org.pk:05d}-flock-models"
    assert flock.s3.client().list_objects_v2(Bucket=bucket).get("KeyCount") == 0
    row = sql(flock, "SELECT state, finished_at FROM hf_imports WHERE pk = $1", pk)[0]
    assert row[0] == "cancelled" and row[1] is not None


def test_only_its_starter_or_an_admin_can_cancel(
    flock: S3Flock, hub: FakeHub, tmp_path: Path, wait_for: Callable[..., Any]
) -> None:
    hub.models["acme/tiny"] = model_repo(tmp_path / "repo")
    hub.held.add("model.safetensors")
    hub.gate.clear()
    alice = flock.add_user("alice@lab.test")
    bob = flock.add_user("bob@lab.test")
    started = alice.api.post(IMPORTS, model_body())
    assert started.status == 202, started.body
    pk = started.json()["id"]
    assert bob.api.get(f"{IMPORTS}/{pk}").status == 200
    assert bob.api.delete(f"{IMPORTS}/{pk}").status == 403
    assert flock.client().delete(f"{IMPORTS}/{pk}").json()["state"] == "cancelled"
    outsider = flock.add_user("eve@else.test", org="flock")
    flock.client().delete(f"/api/v1/orgs/flock/members/{outsider.id}")
    assert outsider.api.get(IMPORTS).status == 404


def test_models_that_cant_be_imported_are_refused_first(
    flock: S3Flock, hub: FakeHub, tmp_path: Path
) -> None:
    api = flock.client()
    assert api.post(IMPORTS, model_body()).status == 404  # not on the Hub

    hub.models["acme/pickled"] = Repo({"config.json": b'{"model_type": "llama"}', "a.bin": b"x"})
    refused = api.post(IMPORTS, model_body(repo="acme/pickled"))
    assert refused.status == 422
    assert "pickled" in refused.json()["error"]

    config = json.dumps({"model_type": "bert-classifier"}).encode()
    hub.models["acme/other"] = Repo({"config.json": config, "model.safetensors": b"x"})
    refused = api.post(IMPORTS, model_body(repo="acme/other"))
    assert refused.status == 422
    assert "isn't a causal language model" in refused.json()["error"]

    config = json.dumps({"model_type": "llama", "auto_map": {"AutoModel": "x.Y"}}).encode()
    hub.models["acme/remote"] = Repo({"config.json": config, "model.safetensors": b"x"})
    assert api.post(IMPORTS, model_body(repo="acme/remote")).status == 422

    assert api.post(IMPORTS, model_body(name="Not A Name")).status == 422
    assert api.post(IMPORTS, {"kind": "model", "repo": "../x"}).status == 422
    assert sql(flock, "SELECT count(*) FROM hf_imports")[0][0] == 0


def test_the_size_limit_refuses_with_the_command_to_run(
    make_s3: Callable[..., S3Flock], hub: FakeHub, tmp_path: Path
) -> None:
    flock = make_s3(hf_import_max_bytes=1000)
    hub.models["acme/tiny"] = model_repo(tmp_path / "repo")
    hub.datasets["acme/data"] = dataset_repo()
    api = flock.client()
    refused = api.post(IMPORTS, model_body(revision="v1", tags=["base"], primary=True))
    assert refused.status == 404  # no revision v1
    refused = api.post(IMPORTS, model_body(tags=["base"], primary=True))
    assert refused.status == 409
    body = refused.json()
    assert body["too_large"] is True and body["size"] > 1000
    assert body["command"] == "flock models import acme/tiny flock/tiny --tag base --primary"
    assert "more than the 1000 B this server imports" in body["error"]

    preview = api.get(f"{IMPORTS}/preview", kind="model", repo="acme/tiny").json()
    assert preview["too_large"] is True and preview["max_bytes"] == 1000

    big = parquet([{"text": "x" * 2000}])
    hub.datasets["acme/data"].files["data/train-00000-of-00001.parquet"] = big
    refused = api.post(
        IMPORTS, {"kind": "dataset", "repo": "acme/data", "dest": "hf/data", "splits": ["train"]}
    )
    assert refused.status == 409
    assert refused.json()["command"] == (
        "flock datasets import acme/data hf/data --config default --split train --org flock"
    )
    assert sql(flock, "SELECT count(*) FROM hf_imports")[0][0] == 0


def test_imports_cut_short_by_a_restart_are_interrupted(
    make_s3: Callable[..., S3Flock], hub: FakeHub, tmp_path: Path, wait_for: Callable[..., Any]
) -> None:
    server = make_s3()
    hub.models["acme/tiny"] = model_repo(tmp_path / "repo")
    hub.held.add("model.safetensors")
    hub.gate.clear()
    pk = server.client().post(IMPORTS, model_body()).json()["id"]
    wait_for(lambda: server.client().get(f"{IMPORTS}/{pk}").json()["bytes_done"] > 0)
    server.stop()

    restarted = make_s3(reuse=server)
    job = restarted.client().get(f"{IMPORTS}/{pk}").json()
    assert job["state"] == "interrupted"
    assert job["finished_at"] is not None

    # An import whose replica stopped without a word is marked once its heartbeat is stale.
    sql(
        restarted,
        "UPDATE hf_imports SET state = 'running', heartbeat_at = now() - interval '1 hour'",
    )
    restarted.call(restarted.store.hf_imports.sweep())
    assert restarted.client().get(f"{IMPORTS}/{pk}").json()["state"] == "interrupted"


def test_the_token_is_used_and_never_stored_or_logged(
    flock: S3Flock,
    hub: FakeHub,
    tmp_path: Path,
    wait_for: Callable[..., Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    repo = model_repo(tmp_path / "repo")
    repo.private = True
    hub.models["acme/tiny"] = repo
    api = flock.client()
    assert api.post(IMPORTS, model_body()).status == 404
    preview = api.get(
        f"{IMPORTS}/preview", {"x-hf-token": TOKEN}, kind="model", repo="acme/tiny"
    ).json()
    assert preview["model_type"] == "llama" and preview["refusal"] is None
    started = api.post(IMPORTS, model_body(token=TOKEN))
    assert started.status == 202, started.body
    assert TOKEN not in started.body.decode()
    job = finished(api, started.json()["id"], wait_for)
    assert job["state"] == "finished", job["error"]

    hub.requests.clear()
    started = api.post(IMPORTS, model_body(token=TOKEN))
    finished(api, started.json()["id"], wait_for)
    assert set(hub.authorized()) == {f"Bearer {TOKEN}"}

    for table in ("hf_imports", "model_uploads", "model_versions", "models"):
        rows = sql(flock, f"SELECT row_to_json(t)::text FROM {table} t")
        assert rows and all(TOKEN not in row[0] for row in rows)
    assert TOKEN not in caplog.text
    assert TOKEN not in api.get(IMPORTS).body.decode()


def test_a_failed_import_says_why(
    flock: S3Flock, hub: FakeHub, tmp_path: Path, wait_for: Callable[..., Any]
) -> None:
    hub.models["acme/tiny"] = model_repo(tmp_path / "repo")
    hub.held.add("model.safetensors")
    hub.gate.clear()
    api = flock.client()
    pk = api.post(IMPORTS, model_body()).json()["id"]
    wait_for(lambda: api.get(f"{IMPORTS}/{pk}").json()["bytes_done"] > 0)
    hub.cut = True  # the download ends early, as if the file changed on the Hub
    hub.gate.set()
    job = finished(api, pk, wait_for)
    assert job["state"] == "failed"
    assert job["error"] == "acme/tiny's model.safetensors changed on the Hub while it was imported."
    assert sql(flock, "SELECT state FROM model_uploads")[0][0] == "aborted"


# ---------------------------------------------------------------------- datasets


def test_a_dataset_import_writes_lance_datasets(
    flock: S3Flock, hub: FakeHub, wait_for: Callable[..., Any]
) -> None:
    hub.datasets["acme/data"] = dataset_repo()
    api = flock.client()
    started = api.post(IMPORTS, {"kind": "dataset", "repo": "acme/data", "dest": "hf/data"})
    assert started.status == 202, started.body
    assert started.json()["files_total"] == 2
    job = finished(api, started.json()["id"], wait_for, timeout=180)
    assert job["state"] == "finished", job["error"]
    assert job["config"] == "default" and job["splits"] == ["train", "test"]
    assert job["rows_done"] == 6 and job["files_done"] == 2 and job["progress"] == 1.0
    datasets = sorted(job["result"]["datasets"], key=lambda d: d["split"])
    assert [(d["path"], d["rows"]) for d in datasets] == [("hf/data/test", 1), ("hf/data/train", 5)]

    def read(route: str) -> Any:
        found = api.request(
            "GET", f"/api/v1/orgs/flock/datasets/{route}", {"path": "hf/data/train"}
        )
        assert found.status == 200, found.body
        return found.json()

    assert read("info")["rows"] == 5
    assert "row 4" in json.dumps(read("rows"))
    assert "tiny data" in json.dumps(read("readme"))

    # The same folder again is refused before anything starts.
    again = api.post(IMPORTS, {"kind": "dataset", "repo": "acme/data", "dest": "hf/data"})
    assert again.status == 409
    assert "already a dataset" in again.json()["error"]


def test_dataset_previews_list_configs_and_splits(flock: S3Flock, hub: FakeHub) -> None:
    hub.datasets["acme/data"] = dataset_repo()
    api = flock.client()
    # Without the dataset viewer, from the files a download would take.
    preview = api.get(f"{IMPORTS}/preview", kind="dataset", repo="acme/data").json()
    assert preview["default_config"] == "default"
    [config] = preview["configs"]
    assert config["name"] == "default"
    assert [s["name"] for s in config["splits"]] == ["train", "test"]
    assert all(s["size"] > 0 for s in config["splits"])

    hub.viewer["acme/data"] = {
        "splits": {
            "splits": [
                {"dataset": "acme/data", "config": "en", "split": "train"},
                {"dataset": "acme/data", "config": "en", "split": "test"},
                {"dataset": "acme/data", "config": "fr", "split": "train"},
            ]
        },
        "size": {
            "size": {
                "configs": [{"config": "en", "num_bytes_original_files": 1234}],
                "splits": [
                    {
                        "config": "en",
                        "split": "train",
                        "num_bytes_parquet_files": 1000,
                        "num_rows": 9,
                    }
                ],
            }
        },
    }
    preview = api.get(f"{IMPORTS}/preview", kind="dataset", repo="acme/data").json()
    assert preview["default_config"] == "en"
    assert preview["configs"][0] == {
        "name": "en",
        "size": 1234,
        "splits": [
            {"name": "train", "size": 1000, "rows": 9},
            {"name": "test", "size": None, "rows": None},
        ],
    }
    assert [c["name"] for c in preview["configs"]] == ["en", "fr"]
    missing = api.get(f"{IMPORTS}/preview", kind="dataset", repo="acme/none")
    assert missing.status == 404
    assert "isn't on the Hub" in missing.json()["error"]


def test_model_previews_say_why_a_model_is_refused(
    flock: S3Flock, hub: FakeHub, tmp_path: Path
) -> None:
    hub.models["acme/tiny"] = model_repo(tmp_path / "repo")
    hub.models["acme/pickled"] = Repo({"config.json": b'{"model_type": "llama"}', "a.bin": b"x"})
    api = flock.client()
    preview = api.get(f"{IMPORTS}/preview", kind="model", repo="acme/tiny").json()
    assert preview["sha"] == SHA and preview["model_type"] == "llama"
    assert preview["refusal"] is None and preview["too_large"] is False
    weights = len(hub.models["acme/tiny"].files["model.safetensors"])
    assert weights < preview["size"] < weights + 10_000
    preview = api.get(f"{IMPORTS}/preview", kind="model", repo="acme/pickled").json()
    assert "pickled" in preview["refusal"]


def test_deleting_the_org_cancels_its_imports(
    flock: S3Flock, hub: FakeHub, tmp_path: Path, wait_for: Callable[..., Any]
) -> None:
    hub.models["acme/tiny"] = model_repo(tmp_path / "repo")
    hub.held.add("model.safetensors")
    hub.gate.clear()
    api = flock.client()
    assert api.post("/api/v1/orgs", {"name": "lab"}).status == 201
    pk = api.post("/api/v1/orgs/lab/imports/hf", model_body()).json()["id"]
    wait_for(lambda: api.get(f"/api/v1/orgs/lab/imports/hf/{pk}").json()["bytes_done"] > 0)
    assert api.delete("/api/v1/orgs/lab").status == 204
    assert sql(flock, "SELECT state FROM hf_imports WHERE pk = $1", pk)[0][0] == "cancelled"
    wait_for(lambda: flock.store.hf_imports.jobs == {})
