"""Hugging Face imports (D9) and the fallback to them (D10), against fake repos: nothing touches the network."""

import http.server
import json
import shutil
import threading
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import httpx
import lance
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from click.testing import CliRunner
from huggingface_hub.errors import RepositoryNotFoundError

import flock.hub.write
from flock.hub import (
    DatasetNotFound,
    HubConfig,
    ImportTooLarge,
    import_hf_dataset,
    importer,
    load_dataset,
)
from flock.hub.cli import main as cli
from flock.hub.config import set_default
from flock.hub.importer import (
    DOLMA_SCHEMA,
    FAKE_ROOT_ENV,
    HfSource,
    LocalSource,
    RepoNotFound,
    hf_tag,
    mirror_repo_id,
    plan,
    provenance_card,
)
from tests.hub.hf_repos import (
    DOLMA,
    MMLU,
    MMLU_README_BODY,
    dolma_docs,
    make_dolma,
    make_mmlu,
    mmlu_rows,
)
from tests.hub.utils import uri

DOLMA_SHARDS = 3
DOLMA_ROWS = DOLMA_SHARDS * 5


class Crash(Exception):
    """Stands in for a process dying at an injected point."""


@pytest.fixture
def repos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fake Hugging Face repos, which the importer uses instead of the Hub."""
    root = tmp_path / "hf-repos"
    make_mmlu(root)
    make_dolma(root, DOLMA_SHARDS)
    monkeypatch.setenv(FAKE_ROOT_ENV, str(root))
    return root


def sha_of(repo: str, root: Path) -> str:
    return LocalSource(root).sha(repo, None)


def lance_ds(hub: HubConfig, repo_id: str) -> lance.LanceDataset:
    return lance.dataset(uri(hub, repo_id), storage_options=hub.lance_storage_options())


def transactions(hub: HubConfig, repo_id: str) -> list[tuple[int, str, dict]]:
    """Every version's operation and transaction properties."""
    ds = lance_ds(hub, repo_id)
    found = []
    for item in ds.versions():
        transaction = ds.read_transaction(item["version"])
        assert transaction is not None
        properties = dict(transaction.transaction_properties or {})
        found.append((item["version"], type(transaction.operation).__name__, properties))
    return found


# ---------------------------------------------------------------------- planning


def test_plan_follows_the_readme_configs(repos):
    anatomy = plan(MMLU, "anatomy")
    assert anatomy.sha == sha_of(MMLU, repos)
    assert [(f.path, f.split) for f in anatomy.files] == [
        ("anatomy/dev-00000-of-00001.parquet", "dev"),
        ("anatomy/test-00000-of-00001.parquet", "test"),
        ("anatomy/validation-00000-of-00001.parquet", "validation"),
    ]
    assert anatomy.total_bytes == sum((repos / MMLU / f.path).stat().st_size for f in anatomy.files)
    with pytest.raises(ValueError, match="several configs"):
        plan(MMLU)
    with pytest.raises(ValueError, match="no config 'nope'"):
        plan(MMLU, "nope")
    with pytest.raises(RepoNotFound):
        plan("nobody/nothing")
    with pytest.raises(RepoNotFound):
        plan(MMLU, "anatomy", revision="v2")

    # Without configs in the README, every data file counts, in one split.
    dolma = plan(DOLMA)
    assert [f.path for f in dolma.files] == [
        "data/part-0000.json.gz",
        "data/part-0001.jsonl.zst",
        "data/part-0002.json.gz",
    ]
    assert {f.split for f in dolma.files} == {"train"}
    assert [f.path for f in plan(DOLMA, data_files=["data/*.zst"]).files] == [
        "data/part-0001.jsonl.zst"
    ]
    assert [f.path for f in plan(DOLMA, data_files=["data"]).files] == [f.path for f in dolma.files]


def test_hf_source_uses_hf_api(repos, monkeypatch):
    """The real source, with `HfApi` and `hf_hub_download` answering from a fake repo."""
    local = LocalSource(repos)
    sha = local.sha(MMLU, None)
    calls: list[tuple] = []

    class FakeApi:
        def __init__(self, token=None):
            self.token = token

        def dataset_info(self, repo, revision=None, files_metadata=False):
            calls.append((repo, revision, files_metadata, self.token))
            if repo != MMLU:
                # huggingface_hub's own httpx fork makes its responses; it only reads headers.
                raise RepositoryNotFoundError("no such repo", response=Mock(headers={}))
            siblings = [
                SimpleNamespace(rfilename=f.path, size=f.size) for f in local.files(repo, sha)
            ]
            return SimpleNamespace(sha=sha, siblings=siblings)

    def fake_download(repo, path, repo_type, revision, local_dir, token):
        assert (repo_type, revision) == ("dataset", sha)
        return str(local.download(repo, sha, path, Path(local_dir)))

    monkeypatch.setattr(importer, "HfApi", FakeApi)
    monkeypatch.setattr(importer, "hf_hub_download", fake_download)
    source = HfSource(token="hf_test")
    found = plan(MMLU, "all", src=source)
    assert found.sha == sha
    assert [f.split for f in found.files] == ["dev", "test", "validation"]
    assert calls[0] == (MMLU, None, True, "hf_test")
    assert source.dataset_path(MMLU, sha) == (MMLU, {"revision": sha, "token": "hf_test"})
    with pytest.raises(RepoNotFound):
        source.sha("nobody/nothing", None)


def test_provenance_card_keeps_the_front_matter():
    card = provenance_card("---\nlicense: mit\n---\n# Title\n\nBody\n", "o/r", "c", "a" * 40)
    front, header, rest = card.split("\n", 3)[0:3], card.split("\n")[3], card.split("\n", 4)[4]
    assert front == ["---", "license: mit", "---"]
    assert header.startswith("> Imported from Hugging Face [`o/r`]")
    assert "config `c`, commit `" + "a" * 40 + "`" in header
    assert rest.strip() == "# Title\n\nBody"
    assert provenance_card(None, "o/r", None, "b" * 40).startswith("> Imported")


# ---------------------------------------------------------------------- small mode


def test_small_mode(repos, hub, cache_dir):
    sha = sha_of(MMLU, repos)
    ds = import_hf_dataset(MMLU, "anatomy", hub_config=hub)
    assert ds is not None
    target = mirror_repo_id(MMLU, "anatomy")
    assert target == "hf/cais-test--mmlu--anatomy"
    assert ds.repo_id == target

    # Every split, with a split column, in one data commit; then the card's version, tagged.
    rows = list(ds)
    assert len(rows) == sum(mmlu_rows("anatomy", s) for s in ("test", "validation", "dev"))
    assert Counter(r["split"] for r in rows) == {"test": 4, "validation": 2, "dev": 1}
    assert rows[0]["choices"] == ["a0", "b0", "c0", "d0"]
    history = transactions(hub, target)
    assert [op for _, op, _ in history] == ["Overwrite", "UpdateConfig"]
    _, _, properties = history[0]
    assert properties["flock.source"] == f"hf:{MMLU}@{sha}"
    assert properties["__lance_commit_message"].startswith(f"Import {MMLU} (anatomy) at {sha[:8]}")
    assert ds.tags == {hf_tag(sha): ds.version}
    assert ds.version == history[-1][0]

    card = ds.card
    assert card is not None
    assert card.startswith("---\nlicense: mit\n")
    assert f"[`{MMLU}`](https://huggingface.co/datasets/{MMLU}), config `anatomy`" in card
    assert sha in card
    assert card.endswith(MMLU_README_BODY)

    # The split filter is pushed down to Lance, streaming or not.
    test = load_dataset(target, filter="split = 'test'", hub_config=hub)
    assert test.num_rows == 4 and {r["split"] for r in test} == {"test"}
    dev = load_dataset(
        target, filter="split = 'dev'", streaming=False, cache_dir=cache_dir, hub_config=hub
    )
    assert [r["question"] for r in dev] == ["anatomy dev question 0?"]


def test_small_mode_single_split_has_no_split_column(repos, hub):
    repo = repos / "someone" / "single"
    repo.mkdir(parents=True)
    pq.write_table(pa.table({"x": [1, 2, 3]}), repo / "data.parquet")
    ds = import_hf_dataset("someone/single", target="flock/single", hub_config=hub)
    assert ds is not None
    assert ds.column_names == ["x"]
    assert [r["x"] for r in ds] == [1, 2, 3]
    assert ds.card is not None and ds.card.startswith("> Imported from Hugging Face")


def test_import_options_need_a_workdir(repos, hub):
    cases: list[dict[str, Any]] = [{"stage": "download"}, {"data_files": ["x"]}, {"urls_file": "u"}]
    for kwargs in cases:
        with pytest.raises(ValueError, match="need a workdir"):
            import_hf_dataset(DOLMA, hub_config=hub, **kwargs)


# ---------------------------------------------------------------------- staged mode


def check_dolma(hub: HubConfig, ds, sha: str) -> None:
    assert ds is not None
    assert ds.repo_id == "flock/dolma"
    assert lance_ds(hub, "flock/dolma").schema.remove_metadata() == DOLMA_SCHEMA
    rows = sorted(ds, key=lambda r: r["id"])
    expected = sorted(
        (d for s in range(DOLMA_SHARDS) for d in dolma_docs(s)), key=lambda d: d["id"]
    )
    assert len(rows) == DOLMA_ROWS
    for row, doc in zip(rows, expected, strict=True):
        assert (row["id"], row["text"], row["added"], row["created"]) == (
            doc["id"], doc["text"], doc["added"], doc["created"],
        )  # fmt: skip
        metadata = json.loads(row["metadata"]) if row["metadata"] is not None else None
        assert metadata == doc.get("metadata")
        attributes = json.loads(row["attributes"]) if row["attributes"] is not None else None
        assert attributes == doc.get("attributes")
    history = transactions(hub, "flock/dolma")
    data = [h for h in history if h[1] == "Overwrite"]
    assert len(data) == 1, history
    assert data[0][2]["flock.source"] == f"hf:{DOLMA}@{sha}"
    assert ds.tags == {hf_tag(sha): ds.version}
    assert ds.card is not None and f"commit `{sha}`" in ds.card


def staged(hub: HubConfig, workdir: Path, **kwargs: Any):
    return import_hf_dataset(DOLMA, target="flock/dolma", workdir=workdir, hub_config=hub, **kwargs)


def test_staged_mode(repos, hub, tmp_path):
    ds = staged(hub, tmp_path / "work", num_proc=2)
    check_dolma(hub, ds, sha_of(DOLMA, repos))
    state = json.loads((tmp_path / "work" / "state.json").read_text())
    assert [u["id"] for u in state["units"]] == ["00000", "00001", "00002"]
    # Rerunning a finished import changes nothing.
    again = staged(hub, tmp_path / "work")
    assert again is not None and again.version == ds.version
    assert len(lance_ds(hub, "flock/dolma").versions()) == 2


def test_staged_stages_one_at_a_time(repos, hub, tmp_path):
    work = tmp_path / "work"
    with pytest.raises(ValueError, match="download stage isn't done"):
        staged(hub, work, stage="process")
    assert staged(hub, work, stage="download") is None
    with pytest.raises(ValueError, match="process stage isn't done"):
        staged(hub, work, stage="upload")
    assert staged(hub, work, stage="process") is None
    check_dolma(hub, staged(hub, work, stage="upload"), sha_of(DOLMA, repos))


def test_staged_refuses_fields_beyond_dolma(repos, hub, tmp_path):
    repo = repos / "someone" / "odd"
    repo.mkdir(parents=True)
    (repo / "docs.jsonl").write_text(json.dumps({"id": "1", "text": "t", "extra": 5}) + "\n")
    with pytest.raises(ValueError, match="docs.jsonl isn't Dolma documents"):
        import_hf_dataset("someone/odd", target="flock/odd", workdir=tmp_path / "w", hub_config=hub)


def test_staged_workdir_holds_one_import(repos, hub, tmp_path):
    staged(hub, tmp_path / "work", stage="download")
    with pytest.raises(ValueError, match="target='flock/dolma'"):
        import_hf_dataset(DOLMA, target="flock/other", workdir=tmp_path / "work", hub_config=hub)
    with pytest.raises(ValueError, match="holds an import of"):
        staged(hub, tmp_path / "work", revision="0" * 40)


def test_staged_resumes_after_a_crash_in_download(repos, hub, tmp_path, monkeypatch):
    downloads: Counter[str] = Counter()
    real = LocalSource.download

    def download(self, repo, sha, path, dest):
        downloads[path] += 1
        if path.endswith("0001.jsonl.zst") and downloads[path] == 1:
            raise Crash
        return real(self, repo, sha, path, dest)

    monkeypatch.setattr(LocalSource, "download", download)
    with pytest.raises(Crash):
        staged(hub, tmp_path / "work")
    check_dolma(hub, staged(hub, tmp_path / "work"), sha_of(DOLMA, repos))
    # Files downloaded before the crash weren't downloaded again.
    assert {p: n for p, n in downloads.items() if p.startswith("data/")} == {
        "data/part-0000.json.gz": 1,
        "data/part-0001.jsonl.zst": 2,
        "data/part-0002.json.gz": 1,
    }


def test_staged_resumes_after_a_crash_in_process(repos, hub, tmp_path, monkeypatch):
    processed: list[str] = []
    real = importer.process_file

    def process(raw, out, split, cache_dir, marker):
        processed.append(Path(raw).name)
        if len(processed) == 2:
            raise Crash
        return real(raw, out, split, cache_dir, marker)

    monkeypatch.setattr(importer, "process_file", process)
    with pytest.raises(Crash):
        staged(hub, tmp_path / "work")
    assert not (tmp_path / "work" / "upload").exists()
    check_dolma(hub, staged(hub, tmp_path / "work"), sha_of(DOLMA, repos))
    # The first file wasn't converted again; the one that crashed was.
    assert processed == [
        "00000-part-0000.json.gz",
        "00001-part-0001.jsonl.zst",
        "00001-part-0001.jsonl.zst",
        "00002-part-0002.json.gz",
    ]


@pytest.mark.parametrize("where", ["shard", "commit", "card"])
def test_staged_resumes_after_a_crash_in_upload(repos, hub, tmp_path, monkeypatch, where):
    """Crashes after the first shard, between Lance's commit and `committed.json`, and between the commit and the card."""
    crashed = []

    def once() -> None:
        if not crashed:
            crashed.append(True)
            raise Crash

    if where == "shard":
        real_write = flock.hub.write.DatasetWriter.write_shard

        def write_shard(self, shard_id, data):
            if shard_id == "00001":
                once()
            return real_write(self, shard_id, data)

        monkeypatch.setattr(flock.hub.write.DatasetWriter, "write_shard", write_shard)
    elif where == "commit":
        real_mark = flock.hub.write._mark_committed

        def mark(staging_dir, version):
            once()
            real_mark(staging_dir, version)

        monkeypatch.setattr(flock.hub.write, "_mark_committed", mark)
    else:
        real_finish = importer._finish

        def finish(*args):
            once()
            return real_finish(*args)

        monkeypatch.setattr(importer, "_finish", finish)

    with pytest.raises(Crash):
        staged(hub, tmp_path / "work")
    check_dolma(hub, staged(hub, tmp_path / "work"), sha_of(DOLMA, repos))
    # One data commit and one card version, whatever the crash.
    assert [op for _, op, _ in transactions(hub, "flock/dolma")] == ["Overwrite", "UpdateConfig"]


class RangeHandler(http.server.BaseHTTPRequestHandler):
    """Serves files from `root`, honouring `Range: bytes=N-`, and records the ranges asked for."""

    root: Path
    ranges: list[tuple[str, str | None]]

    def do_GET(self) -> None:
        path = self.root / self.path.lstrip("/")
        if not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        asked = self.headers.get("Range")
        self.ranges.append((self.path, asked))
        if asked:
            start = int(asked.removeprefix("bytes=").rstrip("-"))
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
            data = data[start:]
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture
def file_server(repos) -> Iterator[tuple[str, list]]:
    ranges: list[tuple[str, str | None]] = []
    handler = type("Handler", (RangeHandler,), {"root": repos / DOLMA, "ranges": ranges})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", ranges
    server.shutdown()


def test_staged_urls_file_resumes_with_range(repos, hub, tmp_path, file_server):
    base, ranges = file_server
    names = ["part-0000.json.gz", "part-0001.jsonl.zst", "part-0002.json.gz"]
    urls = tmp_path / "urls.txt"
    urls.write_text("# a comment\n" + "".join(f"{base}/data/{n}\n" for n in names))
    work = tmp_path / "work"
    assert staged(hub, work, urls_file=str(urls), stage="download") is None
    assert [r for _, r in ranges] == [None, None, None]

    # A partial download resumes where it stopped.
    raw = work / "raw" / f"00001-{names[1]}"
    full = raw.read_bytes()
    raw.unlink()
    (work / "raw" / "00001.done").unlink()
    raw.with_name(raw.name + ".part").write_bytes(full[:10])
    ds = staged(hub, work, urls_file=str(urls))
    assert ranges[-1] == (f"/data/{names[1]}", "bytes=10-")
    assert raw.read_bytes() == full
    check_dolma(hub, ds, sha_of(DOLMA, repos))


def test_urls_file_from_the_repo(repos, hub, tmp_path, file_server):
    base, _ = file_server
    (repos / DOLMA / "urls").mkdir()
    (repos / DOLMA / "urls" / "v1.txt").write_text(f"{base}/data/part-0000.json.gz\n")
    ds = staged(hub, tmp_path / "work", urls_file="urls/v1.txt")
    assert ds is not None and ds.num_rows == 5


# ---------------------------------------------------------------------- the command line


def test_cli(repos, hub, tmp_path, monkeypatch):
    monkeypatch.setattr(flock.hub.config, "_default", None)
    set_default(hub)
    runner = CliRunner()
    result = runner.invoke(
        cli, ["import", "--repo", MMLU, "--config", "anatomy", "--target", "flock/mmlu"]
    )
    assert result.exit_code == 0, result.output
    assert "flock/mmlu version 2: 7 rows" in result.output

    work = str(tmp_path / "work")
    args = ["import", "--repo", DOLMA, "--target", "flock/dolma", "--workdir", work]
    result = runner.invoke(cli, [*args, "--stage", "download", "--num-proc", "2"])
    assert result.exit_code == 0, result.output
    assert "stage download done" in result.output
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert f"flock/dolma version 2: {DOLMA_ROWS} rows" in result.output

    result = runner.invoke(cli, ["import", "--repo", DOLMA, "--stage", "process"])
    assert result.exit_code == 1
    assert "need a workdir" in result.output
    result = runner.invoke(cli, ["import", "--repo", "nobody/nothing"])
    assert result.exit_code == 1
    assert "not found" in result.output


# ---------------------------------------------------------------------- the fallback (D10)


def fail_import(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("imported, but shouldn't have")

    monkeypatch.setattr(importer, "import_hf_dataset", refuse)


def test_fallback_order_in_direct_mode(repos, hub, monkeypatch):
    sha = sha_of(MMLU, repos)
    with pytest.raises(DatasetNotFound):
        load_dataset(MMLU, config="anatomy", auto_import=False, hub_config=hub)
    with pytest.raises(DatasetNotFound, match="nor on Hugging Face"):
        load_dataset("nobody/nothing", hub_config=hub)

    # No hub dataset and no mirror: imported, then loaded from the mirror.
    ds = load_dataset(MMLU, config="anatomy", hub_config=hub)
    assert ds.repo_id == "hf/cais-test--mmlu--anatomy"
    assert ds.num_rows == 7
    assert ds.tags == {hf_tag(sha): ds.version}

    # Now the mirror is there: loaded without importing, by sha, by branch or as the latest.
    fail_import(monkeypatch)
    assert load_dataset(MMLU, config="anatomy", hub_config=hub).version == ds.version
    assert (
        load_dataset(MMLU, config="anatomy", hf_revision=sha, hub_config=hub).version == ds.version
    )
    assert (
        load_dataset(MMLU, config="anatomy", hf_revision="main", hub_config=hub).version
        == ds.version
    )
    assert load_dataset(MMLU, 1, config="anatomy", hub_config=hub).version == 1

    # A hub dataset of that name comes first.
    flock.hub.push({"x": [1]}, MMLU, hub_config=hub)
    found = load_dataset(MMLU, config="anatomy", hub_config=hub)
    assert (found.repo_id, found.column_names) == (MMLU, ["x"])


def test_fallback_imports_a_new_revision(repos, hub):
    first = load_dataset(MMLU, config="anatomy", hub_config=hub)
    old_sha = sha_of(MMLU, repos)
    shutil.copyfile(
        repos / MMLU / "all" / "test-00000-of-00001.parquet",
        repos / MMLU / "anatomy" / "test-00000-of-00001.parquet",
    )
    new_sha = sha_of(MMLU, repos)
    assert new_sha != old_sha
    # The latest import is still what `hf_revision=None` gives; a revision asks for a new one.
    assert load_dataset(MMLU, config="anatomy", hub_config=hub).version == first.version
    second = load_dataset(MMLU, config="anatomy", hf_revision="main", hub_config=hub)
    assert second.version > first.version
    assert second.num_rows == 7 + 4
    assert set(second.tags) == {hf_tag(old_sha), hf_tag(new_sha)}
    # The old import stays reachable through its tag.
    assert second.checkout(hf_tag(old_sha)).num_rows == 7


class FakeServer:
    """The mirror endpoints of the API, over a bucket: mirrors are written straight to it."""

    def __init__(self, bucket, hub: HubConfig, max_bytes: int = 10**12) -> None:
        self.bucket = bucket
        self.direct = hub
        self.max_bytes = max_bytes
        self.mirrors: dict[int, dict] = {}
        self.posts = 0
        self.polls = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v1")
        if path == "/hub/credentials":
            return httpx.Response(200, json={
                "endpoint": self.bucket.endpoint, "region": self.bucket.region,
                "access_key_id": self.bucket.access_key_id,
                "secret_access_key": self.bucket.secret_access_key,
                "session_token": None, "expires_at": 4e9,
            })  # fmt: skip
        if path.startswith("/datasets/"):
            org, name = path.removeprefix("/datasets/").split("/")
            if org != "hf" or not any(m["dataset"] == f"hf/{name}" for m in self.mirrors.values()):
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(200, json={"id": name, "uri": self.direct.dataset_uri(org, name)})
        if path == "/hub/mirrors" and request.method == "GET":
            repo, config = request.url.params["repo"], request.url.params.get("config")
            dataset = mirror_repo_id(repo, config)
            mirrors = [m for m in self.mirrors.values() if m["dataset"] == dataset]
            return httpx.Response(200, json={"dataset": dataset, "mirrors": mirrors})
        if path == "/hub/mirrors":
            self.posts += 1
            body = json.loads(request.content)
            found = plan(body["repo"], body["config"], body["revision"])
            for known in self.mirrors.values():
                if (known["repo"], known["config"], known["sha"]) == (
                    body["repo"], body["config"], found.sha,
                ):  # fmt: skip
                    return httpx.Response(200 if known["state"] == "finished" else 202, json=known)
            if found.total_bytes > self.max_bytes:
                command = f"flock-hub import --repo {body['repo']} --revision {found.sha}"
                return httpx.Response(422, json={"error": "too large", "command": command})
            mirror = {
                "id": len(self.mirrors) + 1, "state": "running", "repo": body["repo"],
                "config": body["config"], "sha": found.sha,
                "dataset": mirror_repo_id(body["repo"], body["config"]), "version": None,
                "error": None, "progress": {"stage": "load", "done": 0, "total": 1},
            }  # fmt: skip
            self.mirrors[mirror["id"]] = mirror
            return httpx.Response(202, json=mirror)
        if path.startswith("/hub/mirrors/"):
            self.polls += 1
            mirror = self.mirrors[int(path.rpartition("/")[2])]
            if mirror["state"] == "running":  # the import finishes between two polls
                ds = import_hf_dataset(
                    mirror["repo"], mirror["config"], mirror["sha"], hub_config=self.direct
                )
                assert ds is not None
                mirror.update(state="finished", version=ds.version, progress=None)
            return httpx.Response(200, json=mirror)
        return httpx.Response(500, json={"error": f"unexpected {request.method} {path}"})


def test_fallback_through_the_server(repos, s3_bucket, monkeypatch):
    direct = HubConfig(
        root=f"s3://{s3_bucket.bucket}/{s3_bucket.prefix}hub",
        storage_options=s3_bucket.storage_options(),
    )
    server = FakeServer(s3_bucket, direct)
    hub = HubConfig(api_url="http://flock.test", api_key="k", transport=server.transport())
    monkeypatch.setattr(flock.hub.load, "POLL_SECONDS", 0.0)
    sha = sha_of(MMLU, repos)

    ds = load_dataset(MMLU, config="anatomy", hub_config=hub)
    assert (ds.repo_id, ds.num_rows) == ("hf/cais-test--mmlu--anatomy", 7)
    assert (server.posts, server.polls) == (1, 1)
    assert ds.tags == {hf_tag(sha): ds.version}

    # Known: the latest, or a sha (full or short), loads without asking for an import.
    for revision in (None, sha, sha[:7]):
        again = load_dataset(MMLU, config="anatomy", hf_revision=revision, hub_config=hub)
        assert again.version == ds.version
    assert server.posts == 1

    # A branch name can't be matched here, so the server is asked, and has the mirror.
    again = load_dataset(MMLU, config="anatomy", hf_revision="main", hub_config=hub)
    assert again.version == ds.version
    assert (server.posts, server.polls) == (2, 1)


def test_fallback_too_large(repos, s3_bucket):
    direct = HubConfig(
        root=f"s3://{s3_bucket.bucket}/hub", storage_options=s3_bucket.storage_options()
    )
    server = FakeServer(s3_bucket, direct, max_bytes=10)
    hub = HubConfig(api_url="http://flock.test", api_key="k", transport=server.transport())
    with pytest.raises(ImportTooLarge) as raised:
        load_dataset(DOLMA, hub_config=hub)
    assert raised.value.command.startswith(f"flock-hub import --repo {DOLMA}")
    assert raised.value.command in str(raised.value)
