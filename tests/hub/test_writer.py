"""Writes resume after a crash and commit exactly once; push, edits and the format pins."""

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import datasets
import lance
import lance.fragment
import pandas as pd
import pyarrow as pa
import pytest
from datasets.table import InMemoryTable
from lance.dataset import LanceDataset

import flock.hub.write
from flock.hub import CommitConflict, DatasetWriter, HubConfig, create_dataset, load_dataset, push
from tests.hub.utils import numbers, uri, write

SHARDS = 4
PER_SHARD = 5


class Crash(Exception):
    """Stands in for a process dying at an injected point."""


def shard(i: int) -> pa.Table:
    return numbers(PER_SHARD, start=i * PER_SHARD)


def run_upload(hub: HubConfig, staging: Path, message: str | None = None) -> int:
    """One run of an upload script: every shard, then the commit."""
    writer = DatasetWriter("flock/up", "append", staging, hub_config=hub)
    for i in range(SHARDS):
        writer.write_shard(f"{i:05d}", shard(i))
    return writer.commit(message, {"flock.source": "test"})


def versions(hub: HubConfig, repo_id: str = "flock/up") -> list[int]:
    return [v["version"] for v in lance.dataset(uri(hub, repo_id)).versions()]


def all_ids(hub: HubConfig, repo_id: str = "flock/up", version: int | None = None) -> list[int]:
    return [row["id"] for row in load_dataset(repo_id, version, hub_config=hub)]


EXPECTED = list(range(SHARDS * PER_SHARD))


def test_upload_commits_once_in_shard_order(hub: HubConfig, tmp_path: Path) -> None:
    writer = DatasetWriter("flock/up", "append", tmp_path / "staging", hub_config=hub)
    assert writer.read_version is None
    for i in reversed(range(SHARDS)):  # written in any order, committed by shard id
        assert writer.write_shard(f"{i:05d}", shard(i))
    assert writer.shard_ids() == ["00000", "00001", "00002", "00003"]
    version = writer.commit("first upload")
    assert version == 1 and writer.committed_version == 1
    assert all_ids(hub) == EXPECTED
    # Committing again is a no-op.
    assert writer.commit("again") == 1
    assert versions(hub) == [1]


def test_transaction_properties(hub: HubConfig, tmp_path: Path) -> None:
    writer = DatasetWriter("flock/up", staging_dir=tmp_path / "s", upload_id="u-42", hub_config=hub)
    writer.write_shard("a", shard(0))
    version = writer.commit("hello", {"flock.source": "hf:cais/mmlu@abc"})
    properties = lance.dataset(uri(hub, "flock/up")).read_transaction(version)
    assert properties is not None
    assert properties.transaction_properties == {
        "flock.upload_id": "u-42",
        "__lance_commit_message": "hello",
        "flock.source": "hf:cais/mmlu@abc",
    }
    with pytest.raises(ValueError, match="flock.upload_id"):
        DatasetWriter("flock/up", staging_dir=tmp_path / "t", hub_config=hub).commit(
            properties={"flock.upload_id": "x"}
        )


def test_format_pins(hub: HubConfig, tmp_path: Path) -> None:
    run_upload(hub, tmp_path / "s")
    ds = lance.dataset(uri(hub, "flock/up"))
    assert ds.data_storage_version == "2.2"
    assert ds.has_stable_row_ids
    assert not any(key.startswith("lance.auto_cleanup") for key in ds.config())
    manifests = list((Path(uri(hub, "flock/up")) / "_versions").glob("*.manifest"))
    # V2 names: u64::MAX - version, zero-padded to 20 digits.
    assert [m.stem for m in manifests] == [f"{2**64 - 1 - 1:020d}"]
    assert flock.hub.write.FORMAT_OPTIONS == {
        "data_storage_version": "2.2",
        "max_rows_per_file": 1_000_000,
        "max_bytes_per_file": 2 * 1024**3,
    }


def test_rerun_skips_written_shards(
    hub: HubConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = DatasetWriter("flock/up", staging_dir=tmp_path / "s", hub_config=hub)
    assert writer.write_shard("00000", shard(0))
    calls = []
    original = lance.fragment.write_fragments
    monkeypatch.setattr(
        lance.fragment, "write_fragments", lambda *a, **k: calls.append(1) or original(*a, **k)
    )
    again = DatasetWriter("flock/up", staging_dir=tmp_path / "s", hub_config=hub)
    assert again.upload_id == writer.upload_id
    assert not again.write_shard("00000", shard(0))
    assert calls == []


@pytest.mark.parametrize("crash_after", range(SHARDS))
@pytest.mark.parametrize("where", ["between-shards", "mid-shard"])
def test_crash_during_shards(
    hub: HubConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crash_after: int, where: str
) -> None:
    write(hub, "flock/up", numbers(3, start=-3))  # an existing base version
    staging = tmp_path / "staging"
    saved = flock.hub.write._write_json
    shard_files = 0

    def crashing_save(path: Path, value: Any) -> None:
        nonlocal shard_files
        if path.parent.name == "shards":
            if where == "mid-shard" and shard_files == crash_after:
                raise Crash  # the fragments are written, the shard file isn't
            saved(path, value)
            shard_files += 1
            if where == "between-shards" and shard_files == crash_after + 1:
                raise Crash
            return
        saved(path, value)

    monkeypatch.setattr(flock.hub.write, "_write_json", crashing_save)
    with pytest.raises(Crash):
        run_upload(hub, staging)
    monkeypatch.setattr(flock.hub.write, "_write_json", saved)

    version = run_upload(hub, staging)
    assert version == 2 and versions(hub) == [1, 2]
    assert all_ids(hub) == [-3, -2, -1, *EXPECTED]


@pytest.mark.parametrize("point", ["before-lance-commit", "inside-lance-commit"])
def test_crash_inside_commit(
    hub: HubConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    staging = tmp_path / "staging"
    original = LanceDataset.commit

    def crashing_commit(*args: Any, **kwargs: Any) -> LanceDataset:
        if point == "inside-lance-commit":
            original(*args, **kwargs)  # the version lands, then the process dies
        raise Crash

    monkeypatch.setattr(LanceDataset, "commit", staticmethod(crashing_commit))
    with pytest.raises(Crash):
        run_upload(hub, staging)
    monkeypatch.setattr(LanceDataset, "commit", original)

    assert run_upload(hub, staging) == 1
    assert versions(hub) == [1]
    assert all_ids(hub) == EXPECTED


def test_crash_between_commit_and_record(
    hub: HubConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(hub, "flock/up", numbers(3, start=-3))
    staging = tmp_path / "staging"

    def crash(*args: Any) -> None:
        raise Crash

    monkeypatch.setattr(flock.hub.write, "_mark_committed", crash)
    with pytest.raises(Crash):
        run_upload(hub, staging, message="once")
    monkeypatch.undo()
    assert not (staging / "committed.json").exists()

    # Someone else appends meanwhile; the rerun still finds its own commit.
    write(hub, "flock/up", numbers(1, start=100), mode="append")
    assert run_upload(hub, staging, message="once") == 2
    assert json.loads((staging / "committed.json").read_text()) == {"version": 2}
    assert versions(hub) == [1, 2, 3]
    assert sorted(all_ids(hub)) == sorted([-3, -2, -1, *EXPECTED, 100])


def test_two_writers_both_land(hub: HubConfig, tmp_path: Path) -> None:
    write(hub, "flock/up", numbers(2, start=-2))
    a = DatasetWriter("flock/up", staging_dir=tmp_path / "a", hub_config=hub)
    b = DatasetWriter("flock/up", staging_dir=tmp_path / "b", hub_config=hub)
    assert a.read_version == b.read_version == 1
    a.write_shard("0", numbers(3, start=0))
    b.write_shard("0", numbers(3, start=10))
    assert a.commit() == 2
    assert b.commit() == 3  # rebased onto a's append by Lance
    assert sorted(all_ids(hub)) == [-2, -1, 0, 1, 2, 10, 11, 12]


def test_concurrent_writers_all_land(hub: HubConfig, tmp_path: Path) -> None:
    """On a local URI, Lance's commit is an atomic rename, so concurrent commits all land."""
    write(hub, "flock/up", numbers(1, start=-1))
    writers = []
    for i in range(6):
        writer = DatasetWriter("flock/up", staging_dir=tmp_path / str(i), hub_config=hub)
        writer.write_shard("0", numbers(2, start=10 * i))
        writers.append(writer)
    barrier = threading.Barrier(len(writers))
    results: list[int] = []

    def commit(writer: DatasetWriter) -> None:
        barrier.wait()
        results.append(writer.commit())

    threads = [threading.Thread(target=commit, args=(w,)) for w in writers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [2, 3, 4, 5, 6, 7]
    assert sorted(all_ids(hub)) == sorted([-1, *[10 * i + j for i in range(6) for j in (0, 1)]])


@pytest.mark.parametrize("between", ["overwrite", "restore"])
def test_overwrite_or_restore_racing_an_append(
    hub: HubConfig, tmp_path: Path, between: str
) -> None:
    write(hub, "flock/up", numbers(3))
    write(hub, "flock/up", numbers(3, start=3), mode="append")
    writer = DatasetWriter("flock/up", staging_dir=tmp_path / "s", hub_config=hub)
    writer.write_shard("0", numbers(2, start=50))
    if between == "overwrite":
        write(hub, "flock/up", numbers(1, start=99), mode="overwrite")
    else:
        lance.dataset(uri(hub, "flock/up"), version=1).restore()

    with pytest.raises(CommitConflict, match="mode='overwrite'"):
        writer.commit()
    assert versions(hub) == [1, 2, 3]


def test_lance_conflict_becomes_commit_conflict(
    hub: HubConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A conflict our pre-check misses (it lands after the check) still raises CommitConflict."""
    write(hub, "flock/up", numbers(3))
    writer = DatasetWriter("flock/up", staging_dir=tmp_path / "s", hub_config=hub)
    writer.write_shard("0", numbers(2, start=50))
    original = DatasetWriter._check_rebase

    def check_then_overwrite(self: DatasetWriter, latest: int | None) -> None:
        original(self, latest)
        write(hub, "flock/up", numbers(1, start=99), mode="overwrite")

    monkeypatch.setattr(DatasetWriter, "_check_rebase", check_then_overwrite)
    with pytest.raises(CommitConflict, match="incompatible"):
        writer.commit()


def test_appends_and_deletes_in_between_rebase(hub: HubConfig, tmp_path: Path) -> None:
    write(hub, "flock/up", numbers(4))
    writer = DatasetWriter("flock/up", staging_dir=tmp_path / "s", hub_config=hub)
    writer.write_shard("0", numbers(2, start=50))
    write(hub, "flock/up", numbers(1, start=70), mode="append")
    lance.dataset(uri(hub, "flock/up")).delete("id = 0")
    assert writer.commit() == 4
    assert sorted(all_ids(hub)) == [1, 2, 3, 50, 51, 70]


def test_overwrite_replaces_rows_and_schema(hub: HubConfig, tmp_path: Path) -> None:
    write(hub, "flock/up", numbers(4))
    writer = DatasetWriter("flock/up", "overwrite", tmp_path / "s", hub_config=hub)
    writer.write_shard("0", pa.table({"name": ["a", "b"]}))
    writer.write_shard("1", pa.table({"name": ["c"]}))
    assert writer.commit("replace") == 2
    assert list(load_dataset("flock/up", hub_config=hub)) == [
        {"name": "a"},
        {"name": "b"},
        {"name": "c"},
    ]
    assert all_ids(hub, version=1) == [0, 1, 2, 3]


def test_staging_dir_mismatches(hub: HubConfig, tmp_path: Path) -> None:
    DatasetWriter("flock/up", "append", tmp_path / "s", upload_id="a", hub_config=hub)
    with pytest.raises(ValueError, match="mode='append', not 'overwrite'"):
        DatasetWriter("flock/up", "overwrite", tmp_path / "s", hub_config=hub)
    with pytest.raises(ValueError, match="repo_id"):
        DatasetWriter("flock/other", "append", tmp_path / "s", hub_config=hub)
    with pytest.raises(ValueError, match="upload_id"):
        DatasetWriter("flock/up", "append", tmp_path / "s", upload_id="b", hub_config=hub)
    merge: Any = "merge"
    with pytest.raises(ValueError, match="mode"):
        DatasetWriter("flock/up", merge, tmp_path / "t", hub_config=hub)


def test_shard_checks(hub: HubConfig, tmp_path: Path) -> None:
    writer = DatasetWriter("flock/up", staging_dir=tmp_path / "s", hub_config=hub)
    with pytest.raises(ValueError, match="nothing to commit"):
        writer.commit()
    with pytest.raises(ValueError, match="shard ids"):
        writer.write_shard("../escape", shard(0))
    writer.write_shard("0", shard(0))
    with pytest.raises(ValueError, match="schema"):
        writer.write_shard("1", pa.table({"other": [1]}))
    writer.commit()
    with pytest.raises(ValueError, match="committed"):
        writer.write_shard("2", shard(2))


# ---- push and create_dataset ----

PUSH_INPUTS: dict[str, Callable[[pa.Table], Any]] = {
    "arrow-table": lambda t: t,
    "record-batch": lambda t: t.to_batches()[0],
    "reader": lambda t: t.to_reader(),
    "hf-dataset": lambda t: datasets.Dataset(InMemoryTable(t)),
    "hf-iterable": lambda t: datasets.Dataset(InMemoryTable(t)).to_iterable_dataset(num_shards=2),
    "pandas": lambda t: t.to_pandas(),
    "rows": lambda t: t.to_pylist(),
    "columns": lambda t: t.to_pydict(),
}


@pytest.mark.parametrize("kind", [*PUSH_INPUTS, "hub-dataset"])
def test_push_inputs(hub: HubConfig, kind: str) -> None:
    table = numbers(6)
    if kind == "hub-dataset":
        write(hub, "flock/source", table)
        data: Any = load_dataset("flock/source", hub_config=hub)
    else:
        data = PUSH_INPUTS[kind](table)
    pushed = push(data, "flock/pushed", message=f"from {kind}", hub_config=hub)
    assert pushed.version == 1
    assert list(pushed) == table.to_pylist()
    transaction = lance.dataset(uri(hub, "flock/pushed")).read_transaction(1)
    assert transaction is not None
    assert (transaction.transaction_properties or {})["__lance_commit_message"] == f"from {kind}"


def test_push_modes(hub: HubConfig) -> None:
    push(numbers(3), "flock/pushed", hub_config=hub)
    appended = push(
        pd.DataFrame({"id": [3], "text": ["row 3"], "score": [0.75]}),
        "flock/pushed",
        hub_config=hub,
    )
    assert appended.version == 2 and [r["id"] for r in appended] == [0, 1, 2, 3]
    replaced = push({"x": [1]}, "flock/pushed", mode="overwrite", hub_config=hub)
    assert replaced.version == 3 and list(replaced) == [{"x": 1}]
    with pytest.raises(TypeError, match="can't write"):
        push(42, "flock/pushed", hub_config=hub)


def test_create_dataset_in_direct_mode(hub: HubConfig) -> None:
    info = create_dataset("flock/new", hub_config=hub)
    assert info == {"org": "flock", "name": "new", "uri": uri(hub, "flock/new")}


# ---- edits ----


def test_edits_make_versions(hub: HubConfig) -> None:
    write(hub, "flock/edit", numbers(6))
    ds = load_dataset("flock/edit", hub_config=hub)

    deleted = ds.delete("id >= 4", message="drop the tail")
    assert deleted.version == 2 and [r["id"] for r in deleted] == [0, 1, 2, 3]

    updated = deleted.update({"text": "'changed'"}, where="id = 1")
    # Lance rewrites updated rows into a new fragment, so they move to the end.
    by_id = {row["id"]: row["text"] for row in updated}
    assert updated.version == 3 and by_id == {0: "row 0", 1: "changed", 2: "row 2", 3: "row 3"}

    widened = updated.add_columns({"double": "id * 2"})
    assert widened.version == 4 and widened[-1] == {
        "id": 1,
        "text": "changed",
        "score": 0.25,
        "double": 2,
    }

    carded = widened.set_card("# Edited")
    assert carded.version == 5 and carded.card == "# Edited"
    assert carded.tag("edited") is carded and carded.tags == {"edited": 5}
    assert carded.untag("edited").tags == {}

    # Edits act on the latest version, whatever version the Dataset is pinned to.
    old = load_dataset("flock/edit", 1, hub_config=hub)
    again = old.delete("id = 0")
    assert again.version == 6 and sorted(r["id"] for r in again) == [1, 2, 3]
    # They keep the streaming mode and columns.
    narrow = load_dataset("flock/edit", columns=["id"], streaming=False, hub_config=hub)
    after = narrow.update({"id": "id + 100"}, where="id = 3")
    assert (after.streaming, after.columns) == (False, ["id"])
    assert sorted(r["id"] for r in after) == [1, 2, 103]
