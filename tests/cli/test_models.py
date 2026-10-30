"""`flock.hub.models` and `flock models` (014, D101-D105, D107), against a flock with S3 on:
saving and loading tiny models, the journal and resuming after crashes injected at each point
of a push, the download cache, and the CLI."""

import asyncio
import dataclasses
import hashlib
import itertools
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest
import torch
from safetensors.torch import save_file

from flock.cli import credentials
from flock.hub import models
from flock.hub.models import cache, upload
from flock.hub.models._common import Ref
from flock.training.core.models import Olmo2, Olmo2Config
from tests.app.conftest import Flocks, S3Flock, User
from tests.app.utils import cli_login
from tests.conftest import S3Server

MIB = 1024 * 1024
TINY = Olmo2Config(
    vocab_size=64,
    hidden_size=16,
    intermediate_size=32,
    num_hidden_layers=2,
    num_attention_heads=2,
    num_key_value_heads=1,
    max_position_embeddings=32,
)


class Crash(Exception):
    """Stands in for the process dying at a point of a push."""


@pytest.fixture
def flock(make_flock: Flocks, s3_server: S3Server) -> S3Flock:
    return make_flock.s3(s3_server)


@pytest.fixture
def alice(flock: S3Flock) -> User:
    return flock.add_user("alice@lab.test")


@pytest.fixture(autouse=True)
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The test's own cache (after ``isolated`` clears FLOCK_* settings)."""
    folder = tmp_path / "cache"
    monkeypatch.setenv("FLOCK_CACHE", str(folder))
    return folder


@pytest.fixture
def signed_in(flock: S3Flock, alice: User) -> str:
    token = cli_login(flock, flock.client(token=alice.signup["access_token"]), "laptop")
    credentials.save(flock.url, token, flock.client(token=token).get("/api/v1/me").json())
    return token


@pytest.fixture
def small_parts(flock: S3Flock, monkeypatch: pytest.MonkeyPatch) -> int:
    """5 MiB parts (S3's smallest), so a file of 11 MiB is sent in 3."""
    monkeypatch.setattr(flock.store.models, "PART_SIZE", 5 * MIB)
    return 5 * MIB


def sql(flock: S3Flock, query: str, *args: Any) -> list[asyncpg.Record]:
    async def run() -> list[asyncpg.Record]:
        conn = await asyncpg.connect(flock.database_url)
        try:
            return await conn.fetch(query, *args)
        finally:
            await conn.close()

    return asyncio.run(run())


def tiny(seed: int = 42) -> Olmo2:
    return Olmo2(dataclasses.replace(TINY, init_seed=seed))


def same_weights(a: torch.nn.Module, b: torch.nn.Module) -> bool:
    left, right = a.state_dict(), b.state_dict()
    return left.keys() == right.keys() and all(torch.equal(left[k], right[k]) for k in left)


def folder_model(root: Path, big: int = 11 * MIB) -> Path:
    """A version's files written by hand, with a weights file of ``big`` bytes (3 parts of
    5 MiB) and a README."""
    root.mkdir(parents=True)
    (root / "config.json").write_text("{}")
    (root / "flock.json").write_text(json.dumps({"format": 1}))
    (root / "README.md").write_text("# a model\n")
    weights = torch.arange(big // 4, dtype=torch.float32)
    save_file({"w": weights}, str(root / "model.safetensors"))
    return root


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def uploads(cache_dir: Path) -> list[str]:
    root = cache_dir / "uploads"
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


class Calls:
    """Counts the S3 requests the client makes, by operation, and raises ``Crash`` on the
    ``nth`` time ``event`` fires (a botocore event such as ``after-call.s3.UploadPart``)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, module: Any = upload) -> None:
        self.counts: dict[str, int] = {}
        self.crash: tuple[str, int] | None = None
        self._seen = itertools.count(1)
        real = module.s3_client

        def client(*args: Any, **kwargs: Any) -> Any:
            made = real(*args, **kwargs)
            made.meta.events.register("before-send.s3.*", self._before)
            made.meta.events.register("after-call.s3.*", self._after)
            return made

        monkeypatch.setattr(module, "s3_client", client)

    def crash_at(self, event: str, nth: int = 1) -> None:
        self.crash, self._seen = (event, nth), itertools.count(1)

    def _check(self, event: str) -> None:
        if self.crash is not None and self.crash[0] == event and next(self._seen) == self.crash[1]:
            self.crash = None
            raise Crash(event)

    def _before(self, request: Any, event_name: str, **_: Any) -> None:
        operation = event_name.rsplit(".", 1)[-1]
        self.counts[operation] = self.counts.get(operation, 0) + 1
        self._check(f"before-send.s3.{operation}")

    def _after(self, event_name: str, **_: Any) -> None:
        self._check(event_name)

    def reset(self) -> None:
        self.counts = {}


# ---------------------------------------------------------------------- round trips (D104)


def test_round_trip_single_and_sharded(flock, signed_in, cache_dir):
    model = tiny()
    version = models.save_to_hub(model, "flock/tiny", run="flock/pretrain/abc", step=7)
    assert isinstance(version, models.ModelVersion)
    assert str(version) == "flock/tiny@1" and version.primary and version.step == 7
    assert version.files is not None
    paths = [f.path for f in version.files]
    assert paths == ["config.json", "flock.json", "model.safetensors"]
    assert uploads(cache_dir) == []  # the staging folder and journal are gone

    loaded = models.load_from_hub("flock/tiny", device="cpu")
    assert isinstance(loaded, Olmo2) and loaded.config == model.config
    assert same_weights(model, loaded)
    folder = models.download("flock/tiny@1")
    assert folder == cache_dir / "models" / "flock" / "tiny" / version.digest
    described = json.loads((folder / "flock.json").read_text())
    assert described["architecture"] == "flock.training.core.models.olmo2:Olmo2"
    assert described["config_class"] == "flock.training.core.models.olmo2:Olmo2Config"
    assert described["dtype"] == "float32"
    assert (described["run"], described["step"]) == ("flock/pretrain/abc", 7)
    assert json.loads((folder / "config.json").read_text())["hidden_size"] == 16

    # Sharded: a lower shard_size splits the state dict, with Hugging Face's index.
    other = tiny(seed=7)
    sharded = models.save_to_hub(other, "flock/tiny", shard_size=4096, tags=["small"])
    assert isinstance(sharded, models.ModelVersion)
    assert sharded.number == 2 and not sharded.primary and sharded.tags == ("small",)
    assert sharded.files is not None
    names = [f.path for f in sharded.files]
    assert "model.safetensors.index.json" in names and "model.safetensors" not in names
    assert sum(n.endswith(".safetensors") for n in names) > 2
    for ref in ("flock/tiny@2", "flock/tiny@small"):
        assert same_weights(other, models.load_from_hub(ref, device="cpu"))
    as_bf16 = models.load_from_hub("flock/tiny@2", device="cpu", dtype=torch.bfloat16)
    assert next(as_bf16.parameters()).dtype == torch.bfloat16

    # The registry calls.
    assert [v.number for v in models.versions("flock/tiny")] == [2, 1]
    assert models.set_primary("flock/tiny@small").primary
    assert same_weights(other, models.load_from_hub("flock/tiny", device="cpu"))
    assert models.tag("flock/tiny@1", "release").tags == ("release",)
    with pytest.raises(models.ModelError) as taken:
        models.tag("flock/tiny@2", "release")
    assert taken.value.status == 409 and taken.value.body["version"] == 1
    assert models.tag("flock/tiny@2", "release", move=True).tags == ("release", "small")
    models.untag("flock/tiny", "release")
    assert models.versions("flock/tiny")[0].tags == ("small",)
    assert [m["model"] for m in models.list_models()] == ["flock/tiny"]


def test_references():
    assert Ref.parse("acme/olmo2-1b") == Ref("acme", "olmo2-1b", None)
    assert Ref.parse("acme/olmo2-1b@12").version == "12"
    assert Ref.parse("acme/olmo2-1b@primary").version == "primary"
    for bad in ("olmo2-1b", "acme/olmo/1b", "acme/olmo2-1b@", "/x", "acme/x@1@2"):
        with pytest.raises(ValueError):
            Ref.parse(bad)
    with pytest.raises(ValueError, match="names a version"):
        Ref.parse("acme/olmo2-1b@12", version=False)


def test_config_must_be_a_frozen_dataclass(flock, signed_in):
    model = tiny()
    model.config = {"hidden_size": 16}
    with pytest.raises(TypeError, match="frozen dataclass"):
        models.save_to_hub(model, "flock/tiny")


def test_training_state_is_skipped_unless_asked_for(flock, signed_in, cache_dir):
    model = tiny()
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    batch = {"input_ids": torch.zeros(1, 4, dtype=torch.long), "attention_mask": torch.ones(1, 4)}
    model(batch).sum().backward()
    optimizer.step()
    state = {"optimizer": optimizer.state_dict(), "ema": {"w": torch.ones(3)}}
    version = models.save_to_hub(model, "flock/tiny", training_state=state)
    assert isinstance(version, models.ModelVersion) and version.files is not None
    assert {f.path for f in version.files} >= {
        "training_state/optimizer.safetensors",
        "training_state/ema.safetensors",
    }

    folder = models.download("flock/tiny")
    assert not (folder / "training_state").exists()
    models.load_from_hub("flock/tiny", device="cpu")
    assert not (folder / "training_state").exists()
    models.download("flock/tiny", include_training_state=True)
    assert (folder / "training_state" / "optimizer.safetensors").is_file()

    loaded = models.load_training_state("flock/tiny")
    assert set(loaded) == {"optimizer", "ema"}
    assert torch.equal(loaded["ema"]["w"], torch.ones(3))
    restored = torch.optim.AdamW(model.parameters(), lr=0.5)
    restored.load_state_dict(loaded["optimizer"])
    assert restored.param_groups[0]["lr"] == 1e-3
    first = optimizer.state_dict()["state"][0]["exp_avg"]
    assert torch.equal(restored.state_dict()["state"][0]["exp_avg"], first)


def test_allow_and_ignore_choose_the_files(flock, signed_in, tmp_path, cache_dir):
    source = folder_model(tmp_path / "m", big=1024)
    models.save_to_hub(source, "flock/m")
    folder = models.download("flock/m", allow="*.json")
    assert sorted(p.name for p in folder.iterdir() if not p.name.startswith(".")) == [
        "config.json",
        "flock.json",
    ]
    models.download("flock/m", ignore=["README.md"])
    assert (folder / "model.safetensors").is_file() and not (folder / "README.md").exists()


def test_blocking_false_returns_once_the_files_are_on_disk(
    flock, signed_in, cache_dir, monkeypatch
):
    gate = threading.Event()
    real = upload._Sender.run

    def held(self, check):
        assert gate.wait(30)
        return real(self, check)

    monkeypatch.setattr(upload._Sender, "run", held)
    model = tiny()
    expected = {k: v.clone() for k, v in model.state_dict().items()}
    pending = models.save_to_hub(model, "flock/tiny", blocking=False)
    assert isinstance(pending, models.PendingUpload)
    assert not pending.done()
    # On disk already: in the staging folder, or in the upload's once it's open.
    assert len(list((cache_dir / "uploads").rglob("model.safetensors"))) == 1
    with torch.no_grad():  # training goes on: the saved version keeps the old weights
        for parameter in model.parameters():
            parameter.add_(1)
    with pytest.raises(TimeoutError):
        pending.result(timeout=0.1)
    gate.set()
    version = pending.result(timeout=30)
    assert pending.done() and version.number == 1
    loaded = models.load_from_hub("flock/tiny", device="cpu").state_dict()
    assert all(torch.equal(loaded[k], v) for k, v in expected.items())


def test_dedupe_key_returns_the_version_already_saved(flock, signed_in, cache_dir):
    run: Any = SimpleNamespace(entity="flock", project="pretrain", id="abc")  # as a wandb.Run
    first = models.save_to_hub(tiny(), "flock/tiny", run=run, step=100)
    again = models.save_to_hub(tiny(seed=3), "flock/tiny", run=run, step=100)
    assert isinstance(first, models.ModelVersion) and isinstance(again, models.ModelVersion)
    assert again.number == first.number == 1
    assert uploads(cache_dir) == []
    assert [v.number for v in models.versions("flock/tiny")] == [1]
    (row,) = sql(flock, "SELECT dedupe_key FROM model_versions")
    assert row["dedupe_key"] == "abc:100"
    other = models.save_to_hub(tiny(), "flock/tiny", run=run, step=200, dedupe_key="mine")
    assert isinstance(other, models.ModelVersion) and other.number == 2


def test_keep_local_keeps_the_written_model(flock, signed_in, cache_dir):
    models.save_to_hub(tiny(), "flock/tiny", keep_local=True)
    (kept,) = uploads(cache_dir)
    folder = cache_dir / "uploads" / kept
    assert (folder / "model" / "model.safetensors").is_file()
    assert not (folder / "journal.json").exists()


# ---------------------------------------------------------------------- crashes (D102)


def journal_of(cache_dir: Path) -> dict:
    (ulid,) = uploads(cache_dir)
    return json.loads((cache_dir / "uploads" / ulid / "journal.json").read_text())


def check_version(source: Path, name: str = "flock/m") -> models.ModelVersion:
    folder = models.download(name)
    for path in ("README.md", "config.json", "flock.json", "model.safetensors"):
        assert sha(folder / path) == sha(source / path), path
    (version,) = models.versions(name)
    return version


@pytest.fixture
def crashed(flock, signed_in, small_parts, tmp_path, cache_dir, monkeypatch):
    """Pushes a folder one request at a time and crashes where the test says, then resumes:
    returns ``(push, calls)``, where ``push(event, nth)`` crashes, and ``calls`` counts what
    the resume sent."""
    monkeypatch.setenv("FLOCK_MODELS_CONCURRENCY", "1")
    source = folder_model(tmp_path / "m")
    calls = Calls(monkeypatch)

    def push(event: str, nth: int = 1) -> dict:
        calls.crash_at(event, nth)
        with pytest.raises(Crash):
            models.save_to_hub(source, "flock/m")
        calls.reset()
        return journal_of(cache_dir)

    return source, push, calls


def test_resume_after_a_crash_mid_part(crashed, cache_dir):
    source, push, calls = crashed
    journal = push("before-send.s3.UploadPart", 2)
    big = journal["files"]["model.safetensors"]
    assert big["upload_id"] is not None and not big["done"]
    assert all(journal["files"][p]["done"] for p in ("README.md", "config.json", "flock.json"))
    assert all(len(f["sha256"]) == 64 for f in journal["files"].values())

    version = models.save_to_hub(source, "flock/m")  # the same folder: resumes
    assert isinstance(version, models.ModelVersion) and version.number == 1
    assert calls.counts.get("UploadPart") == 2  # part 1 had landed: parts 2 and 3
    assert "PutObject" not in calls.counts and "CreateMultipartUpload" not in calls.counts
    assert uploads(cache_dir) == []
    check_version(source)


def test_resume_after_a_crash_between_parts(crashed, cache_dir, flock):
    source, push, calls = crashed
    journal = push("after-call.s3.UploadPart", 2)  # part 2 landed; the client never knew
    # A part that landed with other bytes is sent again.
    s3 = flock.s3.client()
    bucket = f"{flock.bucket_prefix}00001-flock-models"
    key = f"m/u/{journal['ulid']}/model.safetensors"
    upload_id = journal["files"]["model.safetensors"]["upload_id"]
    s3.upload_part(Bucket=bucket, Key=key, UploadId=upload_id, PartNumber=1, Body=b"x" * (5 * MIB))

    assert [v.number for v in models.resume_uploads("flock/m")] == [1]
    assert calls.counts.get("UploadPart") == 2  # parts 1 (changed) and 3
    check_version(source)


def test_resume_after_a_crash_between_files(crashed, cache_dir):
    source, push, calls = crashed
    journal = push("after-call.s3.PutObject", 2)
    assert [p for p, f in journal["files"].items() if f["done"]] == ["README.md"]
    ulid = journal["ulid"]
    version = upload.resume_journal(upload.find_journal(ulid[:10]))
    assert version.number == 1
    # config.json had landed (a HEAD says so): only flock.json and the big file are sent.
    assert calls.counts.get("PutObject") == 1 and calls.counts.get("HeadObject") == 3
    assert calls.counts.get("UploadPart") == 3
    check_version(source)


def test_resume_a_file_whose_multipart_upload_is_gone(crashed, cache_dir, flock):
    source, push, calls = crashed
    journal = push("before-send.s3.UploadPart", 3)
    s3 = flock.s3.client()
    s3.abort_multipart_upload(
        Bucket=f"{flock.bucket_prefix}00001-flock-models",
        Key=f"m/u/{journal['ulid']}/model.safetensors",
        UploadId=journal["files"]["model.safetensors"]["upload_id"],
    )
    models.resume_uploads("flock/m")
    assert calls.counts.get("CreateMultipartUpload") == 1 and calls.counts["UploadPart"] == 3
    check_version(source)


def test_resume_after_a_crash_before_the_commit(crashed, cache_dir, monkeypatch):
    source, _push, calls = crashed
    real = upload._commit
    with monkeypatch.context() as patched:

        def before(registry, journal):
            raise Crash("commit")

        patched.setattr(upload, "_commit", before)
        with pytest.raises(Crash):
            models.save_to_hub(source, "flock/m")
    assert upload._commit is real
    journal = journal_of(cache_dir)
    assert all(f["done"] for f in journal["files"].values())
    calls.reset()
    assert [v.number for v in models.resume_uploads("flock/m")] == [1]
    assert "PutObject" not in calls.counts and "UploadPart" not in calls.counts
    check_version(source)


def test_resume_after_the_commit_was_sent(crashed, cache_dir, monkeypatch):
    source, _push, calls = crashed
    real = upload._commit
    with monkeypatch.context() as patched:

        def after(registry, journal):
            real(registry, journal)
            raise Crash("commit answered")

        patched.setattr(upload, "_commit", after)
        with pytest.raises(Crash):
            models.save_to_hub(source, "flock/m")
    assert len(uploads(cache_dir)) == 1  # the journal stayed
    calls.reset()
    version = models.save_to_hub(source, "flock/m")
    assert isinstance(version, models.ModelVersion) and version.number == 1
    assert calls.counts == {}
    assert check_version(source).number == 1  # one version, not two
    assert uploads(cache_dir) == []


def test_saving_a_model_again_resumes_its_upload(flock, signed_in, cache_dir, monkeypatch):
    """A restarted job saving the same run and step finishes the earlier upload instead of
    writing the model again."""
    calls = Calls(monkeypatch)
    calls.crash_at("after-call.s3.PutObject", 2)
    with pytest.raises(Crash):
        models.save_to_hub(tiny(), "flock/tiny", run="flock/pretrain/abc", step=5)
    journal = journal_of(cache_dir)
    assert journal["staged"] and journal["options"]["dedupe_key"] == "abc:5"
    calls.reset()
    written = []
    monkeypatch.setattr("flock.hub.models.files.write_model", lambda *a, **k: written.append(1))
    version = models.save_to_hub(tiny(seed=1), "flock/tiny", run="flock/pretrain/abc", step=5)
    assert isinstance(version, models.ModelVersion)
    assert written == [] and journal["ulid"] in version.prefix
    assert same_weights(tiny(), models.load_from_hub("flock/tiny", device="cpu"))
    assert uploads(cache_dir) == []


def test_a_changed_file_refuses_to_resume(crashed, cache_dir, flock_cli):
    source, push, _calls = crashed
    journal = push("before-send.s3.UploadPart", 2)
    (source / "README.md").write_text("# changed\n")
    with pytest.raises(models.ModelError, match="README.md"):
        models.resume_uploads("flock/m")
    refused = flock_cli("models", "uploads", "resume", journal["ulid"])
    assert refused.exit_code == 1 and "README.md" in refused.output

    # Pushing the folder again starts a new upload; the old one waits to be aborted.
    version = models.save_to_hub(source, "flock/m")
    assert isinstance(version, models.ModelVersion)
    assert uploads(cache_dir) == [journal["ulid"]]
    aborted = flock_cli("models", "uploads", "abort", journal["ulid"])
    assert aborted.exit_code == 0, aborted.output
    assert uploads(cache_dir) == []


def test_an_expired_upload_starts_over(crashed, cache_dir, flock):
    source, push, calls = crashed
    journal = push("before-send.s3.UploadPart", 2)
    sql(
        flock,
        "UPDATE model_uploads SET last_activity_at = now() - interval '8 days' WHERE ulid = $1",
        journal["ulid"],
    )
    assert flock.call(flock.store.models.sweep())["expired"] == 1
    (version,) = models.resume_uploads("flock/m")
    assert version.number == 1 and journal["ulid"] not in version.prefix
    assert calls.counts.get("PutObject") == 3 and calls.counts.get("UploadPart") == 3
    assert uploads(cache_dir) == []
    check_version(source)


def test_an_expired_upload_whose_files_are_gone_is_forgotten(crashed, cache_dir, flock):
    source, push, _calls = crashed
    journal = push("before-send.s3.UploadPart", 2)
    sql(flock, "UPDATE model_uploads SET state = 'expired' WHERE ulid = $1", journal["ulid"])
    (source / "model.safetensors").unlink()
    assert models.resume_uploads("flock/m") == []
    assert uploads(cache_dir) == []


# ---------------------------------------------------------------------- downloads (D103)


@pytest.fixture
def pushed(flock, signed_in, tmp_path) -> tuple[Path, models.ModelVersion]:
    source = folder_model(tmp_path / "m", big=3 * MIB)
    version = models.save_to_hub(source, "flock/m")
    assert isinstance(version, models.ModelVersion)
    return source, version


def test_a_download_resumes_from_its_partial_file(pushed, cache_dir, monkeypatch):
    source, version = pushed
    calls = Calls(monkeypatch, cache)
    ranges = []
    real = cache._Fetcher.chunk

    def chunk(self, file, partial, target, index, count, start, end, order):
        ranges.append((file.path, start, end))
        return real(self, file, partial, target, index, count, start, end, order)

    monkeypatch.setattr(cache._Fetcher, "chunk", chunk)
    folder = cache_dir / "models" / "flock" / "m" / version.digest
    folder.mkdir(parents=True)
    data = (source / "model.safetensors").read_bytes()
    (folder / "model.safetensors.partial").write_bytes(data[:MIB])
    assert models.download("flock/m") == folder
    assert ("model.safetensors", MIB, len(data)) in ranges
    assert (folder / "model.safetensors").read_bytes() == data
    assert not (folder / "model.safetensors.partial").exists()
    assert sorted(json.loads((folder / ".complete").read_text())) == [
        "README.md",
        "config.json",
        "flock.json",
        "model.safetensors",
    ]
    # Everything is checked: a second download asks S3 nothing.
    calls.reset()
    models.download("flock/m")
    assert calls.counts == {}


def test_a_corrupted_file_is_rejected(pushed, cache_dir):
    source, version = pushed
    folder = cache_dir / "models" / "flock" / "m" / version.digest
    folder.mkdir(parents=True)
    size = (source / "model.safetensors").stat().st_size
    (folder / "model.safetensors.partial").write_bytes(b"\0" * size)
    with pytest.raises(models.ModelError, match="model.safetensors of flock/m@1 didn't match"):
        models.download("flock/m")
    assert not (folder / "model.safetensors.partial").exists()
    assert not (folder / "model.safetensors").exists()
    models.download("flock/m")  # again, from the start
    assert sha(folder / "model.safetensors") == sha(source / "model.safetensors")


SHARED = """
import json
import time

from flock.hub.models import cache

gets = []
real = cache.s3_client


def client(*args, **kwargs):
    made = real(*args, **kwargs)

    def slow(**_):
        gets.append(1)
        time.sleep(0.3)

    made.meta.events.register("before-send.s3.GetObject", slow)
    return made


cache.s3_client = client
from flock.hub import models

folder = models.download("flock/m")
print(json.dumps({"folder": str(folder), "gets": len(gets)}))
"""


def test_two_processes_share_one_download(pushed, cache_dir):
    source, version = pushed
    started = [
        subprocess.Popen(
            [sys.executable, "-c", SHARED], stdout=subprocess.PIPE, text=True, env=os.environ
        )
        for _ in range(2)
    ]
    answers = []
    for process in started:
        out, _ = process.communicate(timeout=120)
        assert process.returncode == 0
        answers.append(json.loads(out.splitlines()[-1]))
    assert {a["folder"] for a in answers} == {str(cache_dir / "models/flock/m" / version.digest)}
    assert sum(a["gets"] for a in answers) == 4  # each file once, not twice
    assert sha(Path(answers[0]["folder"]) / "model.safetensors") == sha(
        source / "model.safetensors"
    )


def test_architectures_outside_flock_training_are_refused(flock, signed_in, tmp_path):
    source = folder_model(tmp_path / "m", big=64)
    (source / "flock.json").write_text(
        json.dumps(
            {
                "format": 1,
                "architecture": "evil_package_that_is_never_imported:Model",
                "config_class": "flock.training.core.models.olmo2:Olmo2Config",
            }
        )
    )
    models.save_to_hub(source, "flock/m")
    with pytest.raises(models.ModelError, match="only classes in flock.training are loaded"):
        models.load_from_hub("flock/m", device="cpu")
    assert "evil_package_that_is_never_imported" not in sys.modules
    (source / "flock.json").write_text(
        json.dumps(
            {
                "format": 1,
                "architecture": "flock.training.core.models.olmo2:Olmo2",
                "config_class": "builtins:dict",
            }
        )
    )
    models.save_to_hub(source, "flock/m", primary=True)
    with pytest.raises(models.ModelError, match="config_class is builtins:dict"):
        models.load_from_hub("flock/m", device="cpu")


def test_read_only_keys_are_enough_to_download(pushed, flock, alice):
    keys = alice.api.get("/api/v1/me/s3-keys").json()["keys"]
    before = {k["access_key_id"] for k in keys}
    models.download("flock/m")
    keys = alice.api.get("/api/v1/me/s3-keys").json()["keys"]
    (new,) = [k for k in keys if k["access_key_id"] not in before]
    assert new["read_only"] is True


# ---------------------------------------------------------------------- the CLI (D105)


def test_cli_commands(flock, signed_in, flock_cli, tmp_path, cache_dir):
    source = folder_model(tmp_path / "m", big=1024)
    (source / ".DS_Store").write_text("skipped")
    pushed = flock_cli("models", "push", str(source), "flock/m", "--tag", "release")
    assert pushed.exit_code == 0, pushed.output
    assert pushed.output.splitlines()[-1] == "flock/m@1"
    assert "Sent " in pushed.output
    again = flock_cli("models", "push", str(source), "flock/m", "--primary")
    assert again.output.splitlines()[-1] == "flock/m@2"

    listed = flock_cli("models", "list")
    assert listed.exit_code == 0, listed.output
    assert listed.output == "flock/m\tlatest 2, primary 2 (1.1 KB)\n"
    versions = flock_cli("models", "versions", "flock/m")
    rows = [line.split("\t") for line in versions.output.splitlines()]
    assert [r[:3] for r in rows] == [["@2", "primary", ""], ["@1", "", "release"]]

    primary = flock_cli("models", "primary", "flock/m@release")
    assert primary.exit_code == 0, primary.output
    assert "flock/m@1 is the primary version" in primary.output
    assert flock_cli("models", "primary", "flock/m").exit_code == 2
    tagged = flock_cli("models", "tag", "flock/m@2", "best")
    assert tagged.output == "✓ Tagged flock/m@2 best.\n"
    taken = flock_cli("models", "tag", "flock/m@1", "best")
    assert taken.exit_code == 1 and "best is on version 2" in taken.output
    assert flock_cli("models", "tag", "flock/m@1", "best", "--move").exit_code == 0
    assert flock_cli("models", "untag", "flock/m", "best").exit_code == 0
    assert "best" not in flock_cli("models", "versions", "flock/m").output

    to = tmp_path / "pulled"
    pulled = flock_cli("models", "pull", "flock/m@2", "--to", str(to))
    assert pulled.exit_code == 0, pulled.output
    assert pulled.output.splitlines()[-1] == f"✓ flock/m@2: {to}"
    assert sorted(p.name for p in to.iterdir()) == [
        "README.md",
        "config.json",
        "flock.json",
        "model.safetensors",
    ]
    cached = flock_cli("models", "pull", "flock/m")
    assert str(cache_dir / "models" / "flock" / "m") in cached.output

    missing = flock_cli("models", "pull", "flock/nothing")
    assert missing.exit_code == 1 and "not found" in missing.output
    assert flock_cli("models", "pull", "nothing").exit_code == 2
    assert (
        flock_cli("models", "uploads", "list").output == "No interrupted uploads on this machine.\n"
    )


def test_cli_uploads(flock, signed_in, flock_cli, small_parts, tmp_path, cache_dir, monkeypatch):
    monkeypatch.setenv("FLOCK_MODELS_CONCURRENCY", "1")
    source = folder_model(tmp_path / "m")
    calls = Calls(monkeypatch)
    calls.crash_at("before-send.s3.UploadPart", 2)
    crashed = flock_cli("models", "push", str(source), "flock/m")
    assert crashed.exit_code == 1
    (ulid,) = uploads(cache_dir)
    listed = flock_cli("models", "uploads", "list")
    assert listed.exit_code == 0, listed.output
    cells = listed.output.rstrip("\n").split("\t")
    assert cells[:3] == [ulid, "flock/m", "open"]
    assert cells[3].startswith("3 of 4 files, ") and cells[4] == str(source)

    resumed = flock_cli("models", "uploads", "resume", ulid[:8])
    assert resumed.exit_code == 0, resumed.output
    assert resumed.output.splitlines()[-1] == "flock/m@1"
    assert flock_cli("models", "uploads", "resume", ulid).exit_code == 1  # gone now

    # The same push again after a crash resumes rather than opening another upload.
    calls.crash_at("before-send.s3.UploadPart", 1)
    assert flock_cli("models", "push", str(source), "flock/m").exit_code == 1
    (second,) = uploads(cache_dir)
    calls.reset()
    pushed = flock_cli("models", "push", str(source), "flock/m")
    assert pushed.output.splitlines()[-1] == "flock/m@2"
    (row,) = sql(
        flock,
        "SELECT u.ulid FROM model_versions v JOIN model_uploads u "
        "ON u.pk = v.upload_pk WHERE v.number = 2",
    )
    assert row["ulid"] == second

    # An upload started elsewhere is aborted by its model.
    opened = (
        flock.client(token=signed_in)
        .post(
            "/api/v1/orgs/flock/models/m/uploads",
            {
                "files": [
                    {"path": "config.json", "size": 2},
                    {"path": "flock.json", "size": 2},
                    {"path": "model.safetensors", "size": 8},
                ]
            },
        )
        .json()
    )
    assert flock_cli("models", "uploads", "abort", opened["ulid"]).exit_code == 1
    aborted = flock_cli("models", "uploads", "abort", opened["ulid"], "--model", "flock/m")
    assert aborted.exit_code == 0, aborted.output
    state = (
        flock.client(token=signed_in)
        .get(f"/api/v1/orgs/flock/models/m/uploads/{opened['ulid']}")
        .json()["state"]
    )
    assert state == "aborted"


def test_the_package_imports_no_heavy_library():
    for module in (models, cache, upload):
        source = Path(module.__file__).read_text()
        top_level = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
        assert all("torch" not in line and "boto" not in line for line in top_level)
