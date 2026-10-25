"""The HF cache of non-streaming datasets: hits read nothing, other keys miss, damage is rebuilt."""

import shutil
from pathlib import Path
from typing import Any

import pytest
from lance.fragment import LanceFragment

from flock.hub import HubConfig, load_dataset
from tests.hub.utils import numbers, write


@pytest.fixture
def scans(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The fragment ids scanned (Lance data reads) so far."""
    calls: list[int] = []
    original = LanceFragment.to_batches

    def spy(self: LanceFragment, **kwargs: Any) -> Any:
        calls.append(self.fragment_id)
        return original(self, **kwargs)

    monkeypatch.setattr(LanceFragment, "to_batches", spy)
    return calls


@pytest.fixture
def repo(hub: HubConfig) -> str:
    write(hub, "flock/cached", numbers(12), max_rows_per_file=5)
    write(hub, "flock/cached", numbers(3, start=12), mode="append")
    return "flock/cached"


def load(hub: HubConfig, repo: str, cache_dir: str, **kwargs: Any):
    return load_dataset(repo, streaming=False, hub_config=hub, cache_dir=cache_dir, **kwargs)


def ids(ds) -> list[int]:
    return [row["id"] for row in ds]


def test_second_load_reads_nothing(
    hub: HubConfig, cache_dir: str, repo: str, scans: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    first = load(hub, repo, cache_dir)
    assert ids(first) == list(range(15))
    assert scans == [0, 1, 2, 3]

    def refuse(self: LanceFragment, **kwargs: Any) -> Any:
        raise AssertionError("read Lance data on a cache hit")

    monkeypatch.setattr(LanceFragment, "to_batches", refuse)
    second = load(hub, repo, cache_dir)
    assert ids(second) == list(range(15))
    assert second.to_hf().cache_files == first.to_hf().cache_files


@pytest.mark.parametrize(
    "change",
    [
        {"version": 1},
        {"columns": ["id"]},
        {"columns": ["id", "text"]},
        {"filter": "id > 3"},
        {"filter": "id > 4"},
    ],
    ids=["version", "columns", "more-columns", "filter", "other-filter"],
)
def test_other_keys_miss(
    hub: HubConfig, cache_dir: str, repo: str, scans: list[int], change: dict[str, Any]
) -> None:
    load(hub, repo, cache_dir)
    scans.clear()
    other = load(hub, repo, cache_dir, **change)
    assert scans  # read again from Lance
    streamed = load_dataset(repo, hub_config=hub, **change)
    assert list(other) == list(streamed)


def test_streaming_never_caches(hub: HubConfig, cache_dir: str, repo: str) -> None:
    ds = load_dataset(repo, hub_config=hub, cache_dir=cache_dir)
    assert ids(ds) == list(range(15))
    assert not Path(cache_dir).exists() or not list(Path(cache_dir).rglob("*.arrow"))


def test_recreated_dataset_misses(
    hub: HubConfig, cache_dir: str, tmp_path: Path, scans: list[int]
) -> None:
    """A new dataset at the same path and version has new data files, so a new key."""
    write(hub, "flock/again", numbers(4))
    assert ids(load(hub, "flock/again", cache_dir)) == [0, 1, 2, 3]
    shutil.rmtree(tmp_path / "hub" / "flock" / "again")
    write(hub, "flock/again", numbers(4, start=100))
    scans.clear()
    assert ids(load(hub, "flock/again", cache_dir)) == [100, 101, 102, 103]
    assert scans


@pytest.mark.parametrize("damage", ["truncate", "garbage", "delete", "drop-info"])
def test_damaged_cache_is_rebuilt(
    hub: HubConfig, cache_dir: str, repo: str, scans: list[int], damage: str
) -> None:
    first = load(hub, repo, cache_dir)
    arrow_files = [Path(f["filename"]) for f in first.to_hf().cache_files]
    assert arrow_files
    del first
    for path in arrow_files:
        if damage == "truncate":
            path.write_bytes(path.read_bytes()[:100])
        elif damage == "garbage":
            path.write_bytes(b"not an arrow file" * 10)
        elif damage == "delete":
            path.unlink()
        else:
            (path.parent / "dataset_info.json").unlink()

    scans.clear()
    rebuilt = load(hub, repo, cache_dir)
    assert ids(rebuilt) == list(range(15))
    assert scans
    # And the rebuilt copy is a cache hit again.
    scans.clear()
    assert ids(load(hub, repo, cache_dir)) == list(range(15))
    assert not scans
