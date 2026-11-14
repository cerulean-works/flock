"""`flock.hub.datasets.convert` (D120, D121): a downloaded folder to Lance datasets.

These write to a local folder through the same code `convert` runs after signing in, so the
worker pool, ordering, schema checks and commits are tested without a flock. The trip through
the proxy to `s3://flock-datasets` is tested in `tests/cli/test_datasets.py`.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import re
from pathlib import Path

import lance
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from flock.hub.datasets import DataFile, DatasetError, Manifest
from flock.hub.datasets._convert import Target, Task, Written, default_workers, run, write_file


def die_on_second_file(task: Task) -> Written:
    """A worker that writes the first file's fragments, then dies on the next."""
    if task.index == 1:
        os._exit(1)
    return write_file(task)


def rows(start: int, stop: int) -> list[dict]:
    return [{"id": i, "text": f"row {i}"} for i in range(start, stop)]


def write_config(
    folder: Path, prefix: str, offset: int = 0, test_split: bool = True
) -> dict[str, list[DataFile]]:
    """One config's files under `prefix`: a train split in Parquet (two files, one with several
    row groups), JSON Lines (gzipped) and CSV, in that order, and a test split. Row ids start
    at `offset`."""
    data = folder / prefix
    data.mkdir(parents=True, exist_ok=True)

    def ids(start: int, stop: int) -> list[dict]:
        return rows(offset + start, offset + stop)

    pq.write_table(pa.Table.from_pylist(ids(0, 250)), data / "train-0.parquet", row_group_size=40)
    pq.write_table(pa.Table.from_pylist(ids(250, 300)), data / "train-1.parquet")
    with gzip.open(data / "train-2.jsonl.gz", "wt") as file:
        file.writelines(json.dumps(row) + "\n" for row in ids(300, 420))
    lines = [f"{row['id']},{row['text']}" for row in ids(420, 500)]
    (data / "train-3.csv").write_text("id,text\n" + "\n".join(lines) + "\n")
    splits = {
        "train": [
            DataFile(f"{prefix}/train-0.parquet", "parquet"),
            DataFile(f"{prefix}/train-1.parquet", "parquet"),
            DataFile(f"{prefix}/train-2.jsonl.gz", "json"),
            DataFile(f"{prefix}/train-3.csv", "csv"),
        ]
    }
    if test_split:
        pq.write_table(pa.Table.from_pylist(ids(1000, 1010)), data / "test-0.parquet")
        splits["test"] = [DataFile(f"{prefix}/test-0.parquet", "parquet")]
    return splits


def write_source(
    folder: Path,
    *,
    configs: tuple[str, ...] = ("default",),
    chosen: tuple[str, ...] = ("default",),
    test_split: bool = True,
) -> list[dict]:
    """A downloaded folder with each `chosen` config's files (see `write_config`): the first
    under `data`, the next under `data-{config}` with ids from 10,000, and so on. Returns the
    first config's train rows in order."""
    splits = {
        config: write_config(folder, "data" if i == 0 else f"data-{config}", 10_000 * i, test_split)
        for i, config in enumerate(chosen)
    }
    (folder / "README.md").write_text("# A dataset\n")
    manifest = Manifest(
        repo="acme/words",
        revision="0" * 40,
        configs=list(configs),
        splits=splits,
        card="README.md",
    )
    (folder / "flock-import.json").write_text(manifest.to_json())
    return rows(0, 500)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    folder = tmp_path / "download"
    write_source(folder)
    return folder


@pytest.fixture
def target(tmp_path: Path) -> Target:
    return Target.local(tmp_path / "flock-datasets")


def table(target: Target, path: str, version: int | None = None) -> list[dict]:
    return lance.dataset(target.uri(path), version=version).to_table().to_pylist()


@pytest.mark.parametrize("workers", [1, 4])
def test_converts_every_format_in_order(source: Path, target: Target, workers: int) -> None:
    converted = run(source, "words", target, workers=workers, stream=None)

    assert [(c.config, c.split, c.path, c.version, c.rows) for c in converted] == [
        ("default", "train", "words/train", 1, 500),
        ("default", "test", "words/test", 1, 10),
    ]
    assert table(target, "words/train") == rows(0, 500)
    assert table(target, "words/test") == rows(1000, 1010)
    dataset = lance.dataset(target.uri("words/train"))
    assert dataset.schema.field("id").type == pa.int64()
    transaction = dataset.read_transaction(1)
    assert transaction is not None and transaction.transaction_properties is not None
    properties = transaction.transaction_properties
    assert json.loads(properties["flock.source"]) == {
        "hub": "huggingface",
        "repo": "acme/words",
        "revision": "0" * 40,
        "config": "default",
        "split": "train",
    }
    root = Path(target.root)
    for card in ("words/README.md", "words/train/README.md", "words/test/README.md"):
        assert (root / card).read_text() == "# A dataset\n"


def test_progress_is_a_line_per_file_off_a_terminal(
    source: Path, target: Target, tmp_path: Path
) -> None:
    log = tmp_path / "log.txt"
    with log.open("w") as stream:
        run(source, "words", target, workers=1, stream=stream)
    lines = log.read_text().splitlines()
    assert len(lines) == 5
    assert re.fullmatch(
        r"default/train 1/4: data/train-0\.parquet \(250 rows, [0-9.]+ kB\)", lines[0]
    )
    assert lines[-1].startswith("default/test 1/1: data/test-0.parquet (10 rows, ")


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize("workers", [1, 2])
def test_progress_on_a_terminal_counts_rows_and_bytes(
    source: Path, target: Target, workers: int
) -> None:
    stream = Terminal()
    run(source, "words", target, workers=workers, stream=stream)
    text = stream.getvalue()
    assert text.startswith("\r") and text.endswith("\x1b[K\n")
    last = text.removesuffix("\x1b[K\n").rpartition("\r")[2]
    assert re.fullmatch(
        r"default/train: 500 rows, [0-9.]+ kB, 4/4 files  "
        r"default/test: 10 rows, [0-9]+ B, 1/1 files",
        last,
    ), last


def test_a_schema_mismatch_is_refused_before_writing(source: Path, target: Target) -> None:
    # The CSV's text column becomes a struct in a JSON file: no type holds both.
    bad = source / "data" / "train-4.jsonl"
    bad.write_text(json.dumps({"id": 1, "text": {"nested": True}}) + "\n")
    manifest = json.loads((source / "flock-import.json").read_text())
    manifest["splits"]["default"]["train"].append({"path": "data/train-4.jsonl", "format": "json"})
    (source / "flock-import.json").write_text(json.dumps(manifest))

    with pytest.raises(DatasetError, match=r"data/train-4\.jsonl doesn't match the schema"):
        run(source, "words", target, workers=1, stream=None)
    assert not Path(target.root).exists()


def test_a_worker_that_dies_leaves_no_new_version(source: Path, target: Target) -> None:
    run(source, "words", target, workers=1, stream=None)
    with pytest.raises(DatasetError, match="worker stopped unexpectedly"):
        run(
            source,
            "words",
            target,
            workers=2,
            mode="overwrite",
            stream=None,
            worker=die_on_second_file,
        )
    for split in ("train", "test"):
        assert lance.dataset(target.uri(f"words/{split}")).version == 1
    assert table(target, "words/train") == rows(0, 500)


def test_a_failing_file_names_itself_and_commits_nothing(source: Path, target: Target) -> None:
    # Garble a row group in the middle of the first file: its footer still reads.
    path = source / "data" / "train-0.parquet"
    offset = pq.ParquetFile(path).metadata.row_group(3).column(0).data_page_offset
    with path.open("r+b") as file:
        file.seek(offset)
        file.write(b"\xff" * 64)
    with pytest.raises(DatasetError, match=r"Couldn't convert .*train-0\.parquet"):
        run(source, "words", target, workers=2, stream=None)
    with pytest.raises(ValueError, match="not found"):
        lance.dataset(target.uri("words/train"))


def test_modes(source: Path, target: Target) -> None:
    run(source, "words", target, workers=2, stream=None)
    with pytest.raises(DatasetError, match="already has a dataset at words/train, words/test"):
        run(source, "words", target, workers=2, stream=None)

    appended = run(source, "words", target, workers=2, mode="append", stream=None)
    assert [c.version for c in appended] == [2, 2]
    assert table(target, "words/train") == rows(0, 500) * 2
    assert table(target, "words/train", version=1) == rows(0, 500)

    overwritten = run(source, "words", target, workers=1, mode="overwrite", stream=None)
    assert [c.version for c in overwritten] == [3, 3]
    assert table(target, "words/train") == rows(0, 500)
    transaction = lance.dataset(target.uri("words/train")).read_transaction(3)
    assert transaction is not None
    assert type(transaction.operation).__name__ == "Overwrite"

    # Appending files of another schema is refused before anything is written.
    other = source.parent / "other"
    other.mkdir()
    pq.write_table(pa.table({"label": [1, 2]}), other / "train.parquet")
    manifest = Manifest(
        repo="acme/labels",
        revision="1" * 40,
        configs=["default"],
        splits={"default": {"train": [DataFile("train.parquet", "parquet")]}},
    )
    (other / "flock-import.json").write_text(manifest.to_json())
    with pytest.raises(DatasetError, match="doesn't match the dataset at words/train"):
        run(other, "words", target, workers=1, mode="append", stream=None)
    assert lance.dataset(target.uri("words/train")).version == 3

    # Appending or overwriting where there's nothing yet creates the dataset.
    created = run(other, "labels", target, workers=1, mode="append", stream=None)
    assert [(c.path, c.version) for c in created] == [("labels/train", 1)]


def test_one_config_of_several_lands_in_its_folder(tmp_path: Path, target: Target) -> None:
    folder = tmp_path / "download"
    write_source(folder, configs=("en", "fr"), chosen=("fr",), test_split=False)
    converted = run(folder, "/words/", target, workers=2, stream=None)
    assert [c.path for c in converted] == ["words/fr/train"]
    assert table(target, "words/fr/train") == rows(0, 500)
    assert (Path(target.root) / "words/README.md").is_file()
    assert (Path(target.root) / "words/fr/train/README.md").is_file()


def source_of(target: Target, path: str) -> dict:
    dataset = lance.dataset(target.uri(path))
    transaction = dataset.read_transaction(dataset.version)
    assert transaction is not None and transaction.transaction_properties is not None
    return json.loads(transaction.transaction_properties["flock.source"])


@pytest.mark.parametrize("workers", [1, 4])
def test_several_configs_convert_in_one_pool(tmp_path: Path, target: Target, workers: int) -> None:
    folder = tmp_path / "download"
    write_source(folder, configs=("en", "fr", "de"), chosen=("en", "fr"))
    log = tmp_path / "log.txt"
    with log.open("w") as stream:
        converted = run(folder, "words", target, workers=workers, stream=stream)

    assert [(c.config, c.split, c.path, c.version, c.rows) for c in converted] == [
        ("en", "train", "words/en/train", 1, 500),
        ("en", "test", "words/en/test", 1, 10),
        ("fr", "train", "words/fr/train", 1, 500),
        ("fr", "test", "words/fr/test", 1, 10),
    ]
    assert table(target, "words/en/train") == rows(0, 500)
    assert table(target, "words/en/test") == rows(1000, 1010)
    assert table(target, "words/fr/train") == rows(10_000, 10_500)
    assert table(target, "words/fr/test") == rows(11_000, 11_010)
    for config in ("en", "fr"):
        for split in ("train", "test"):
            assert source_of(target, f"words/{config}/{split}") == {
                "hub": "huggingface",
                "repo": "acme/words",
                "revision": "0" * 40,
                "config": config,
                "split": split,
            }
            assert (Path(target.root) / f"words/{config}/{split}/README.md").is_file()
    assert (Path(target.root) / "words/README.md").is_file()
    assert not (Path(target.root) / "words/de").exists()
    lines = log.read_text().splitlines()
    assert len(lines) == 10
    assert {line.partition(" ")[0] for line in lines} == {
        "en/train",
        "en/test",
        "fr/train",
        "fr/test",
    }


def test_several_configs_refuse_an_existing_target_before_writing(
    tmp_path: Path, target: Target
) -> None:
    folder = tmp_path / "download"
    write_source(folder, configs=("en", "fr"), chosen=("en", "fr"))
    only_fr = tmp_path / "fr"
    write_source(only_fr, configs=("en", "fr"), chosen=("fr",), test_split=False)
    run(only_fr, "words", target, workers=1, stream=None)

    with pytest.raises(DatasetError, match="already has a dataset at words/fr/train"):
        run(folder, "words", target, workers=2, stream=None)
    root = Path(target.root) / "words"
    assert not (root / "en").exists()
    assert not (root / "fr/test").exists()
    assert lance.dataset(target.uri("words/fr/train")).version == 1


def test_bad_folders_and_destinations(tmp_path: Path, source: Path, target: Target) -> None:
    with pytest.raises(DatasetError, match="has no flock-import.json"):
        run(tmp_path, "words", target, stream=None)
    for dest in ("", "/", "a//b", "../up", "a/./b"):
        with pytest.raises(DatasetError, match="isn't a folder of flock-datasets"):
            run(source, dest, target, stream=None)
    (source / "data" / "train-1.parquet").unlink()
    with pytest.raises(DatasetError, match=r"is missing data/train-1\.parquet"):
        run(source, "words", target, stream=None)
    with pytest.raises(ValueError, match="mode must be one of"):
        run(source, "words", target, mode="replace", stream=None)  # ty: ignore[invalid-argument-type]


def test_default_workers_respect_the_allowed_cpus(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: {0, 1, 2}, raising=False)
    assert default_workers() == 3
    monkeypatch.delattr(os, "sched_getaffinity", raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 12)
    assert default_workers() == 12
