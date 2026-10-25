"""The `Dataset` surface: return types, random access, filters and `load_dataset`'s arguments."""

from collections.abc import Callable
from typing import Any

import datasets
import pytest

import flock.hub
from flock.hub import Dataset, HubConfig, load_dataset, split_dataset_by_node
from tests.hub.utils import numbers, write

MODES = pytest.mark.parametrize("streaming", [True, False], ids=["streaming", "materialized"])
ROWS = 20


@pytest.fixture
def repo(hub: HubConfig) -> str:
    """20 rows over 3 fragments, with rows 4 and 15 deleted, then rows 20-24 appended (v3)."""
    lance_ds = write(hub, "flock/api", numbers(ROWS), max_rows_per_file=7)
    lance_ds.delete("id = 4 OR id = 15")
    write(hub, "flock/api", numbers(5, start=ROWS), mode="append")
    return "flock/api"


EXPECTED_IDS = [i for i in range(25) if i not in (4, 15)]

OPERATIONS: dict[str, Callable[[Dataset], Dataset]] = {
    "map": lambda ds: ds.map(lambda row: {"double": row["id"] * 2}),
    "map_batched": lambda ds: ds.map(lambda b: {"n": [len(b["id"])] * len(b["id"])}, batched=True),
    "filter": lambda ds: ds.filter(lambda row: row["id"] % 2 == 0),
    "shuffle": lambda ds: ds.shuffle(seed=0),
    "batch": lambda ds: ds.batch(4),
    "select_columns": lambda ds: ds.select_columns(["id"]),
    "remove_columns": lambda ds: ds.remove_columns("text"),
    "rename_column": lambda ds: ds.rename_column("text", "body"),
    "rename_columns": lambda ds: ds.rename_columns({"text": "body", "score": "weight"}),
    "cast_column": lambda ds: ds.cast_column("id", datasets.Value("int32")),
    "with_format": lambda ds: ds.with_format("arrow"),
    "skip": lambda ds: ds.skip(2),
    "take": lambda ds: ds.take(3),
    "shard": lambda ds: ds.shard(num_shards=2, index=0),
    "split_dataset_by_node": lambda ds: split_dataset_by_node(ds, 0, 2),
    "chained": lambda ds: ds.map(lambda row: row).filter(lambda row: True).take(4),
}


@MODES
@pytest.mark.parametrize("name", OPERATIONS)
def test_operations_return_hub_datasets(
    hub: HubConfig, cache_dir: str, repo: str, name: str, streaming: bool
) -> None:
    ds = load_dataset(repo, streaming=streaming, hub_config=hub, cache_dir=cache_dir)
    result = OPERATIONS[name](ds)
    assert type(result) is type(ds)
    assert isinstance(result, Dataset)
    assert (result.repo_id, result.version, result.streaming) == (repo, 3, streaming)
    hf_class = datasets.IterableDataset if streaming else datasets.Dataset
    assert type(result.to_hf()) is hf_class
    list(result)  # and it iterates


@MODES
def test_to_hf(hub: HubConfig, cache_dir: str, repo: str, streaming: bool) -> None:
    ds = load_dataset(repo, streaming=streaming, hub_config=hub, cache_dir=cache_dir)
    hf = ds.to_hf()
    assert type(hf) is (datasets.IterableDataset if streaming else datasets.Dataset)
    assert [row["id"] for row in hf] == EXPECTED_IDS


@MODES
def test_known_before_iterating(hub: HubConfig, cache_dir: str, repo: str, streaming: bool) -> None:
    ds = load_dataset(repo, streaming=streaming, hub_config=hub, cache_dir=cache_dir)
    assert ds.num_rows == len(ds) == len(EXPECTED_IDS)
    assert list(ds.features or {}) == ["id", "text", "score"]
    assert ds.column_names == ["id", "text", "score"]
    assert "flock/api" in repr(ds) and "version=3" in repr(ds)


def test_streaming_row_count_after_transforms(hub: HubConfig, repo: str) -> None:
    ds = load_dataset(repo, hub_config=hub)
    n = len(EXPECTED_IDS)
    assert ds.map(lambda row: row).num_rows == n
    assert ds.rename_column("text", "body").num_rows == n
    assert ds.shuffle(seed=0).num_rows == n
    assert ds.filter(lambda row: True).num_rows is None
    assert ds.map(lambda b: b, batched=True).num_rows is None
    with pytest.raises(TypeError, match="length"):
        len(ds.batch(2))


# ---- random access ----


@MODES
@pytest.mark.parametrize("load_filter", [None, "id % 3 != 0"])
def test_random_access_agrees_with_iteration(
    hub: HubConfig, cache_dir: str, repo: str, streaming: bool, load_filter: str | None
) -> None:
    ds = load_dataset(
        repo, streaming=streaming, filter=load_filter, hub_config=hub, cache_dir=cache_dir
    )
    rows = list(ds)
    n = len(rows)
    assert n == ds.num_rows

    assert [ds[i] for i in range(n)] == rows
    assert [ds[-i] for i in range(1, n + 1)] == rows[::-1]

    def as_rows(batch: dict[str, list]) -> list[dict[str, Any]]:
        return [{k: batch[k][i] for k in batch} for i in range(len(batch["id"]))]

    for key in [slice(2, 9), slice(None, 4), slice(-3, None), slice(1, 12, 3), slice(9, 2, -2)]:
        assert as_rows(ds[key]) == rows[key]
    assert as_rows(ds[5:1000]) == rows[5:]
    assert as_rows(ds[7:3]) == []

    order = [n - 1, 0, 5, 5, 2, -1]
    assert as_rows(ds.take_rows(order)) == [rows[i] for i in order]
    assert as_rows(ds[order]) == [rows[i] for i in order]
    assert ds.__getitems__([3, 1]) == [rows[3], rows[1]]


@MODES
def test_out_of_range(hub: HubConfig, cache_dir: str, repo: str, streaming: bool) -> None:
    ds = load_dataset(repo, streaming=streaming, hub_config=hub, cache_dir=cache_dir)
    n = len(EXPECTED_IDS)
    for index in (n, -n - 1, 10**9):
        with pytest.raises(IndexError, match="out of range"):
            ds[index]
    with pytest.raises(IndexError):
        ds.take_rows([0, n])
    with pytest.raises(TypeError, match="ints"):
        not_ints: Any = [1.5]
        ds.take_rows(not_ints)


def test_random_access_after_transform(hub: HubConfig, repo: str, cache_dir: str) -> None:
    streaming = load_dataset(repo, hub_config=hub).map(lambda row: row)
    with pytest.raises(TypeError, match="only works before any transform"):
        streaming[0]
    with pytest.raises(TypeError):
        streaming.take_rows([0])
    with pytest.raises(TypeError):
        load_dataset(repo, hub_config=hub)["id"]

    # Not streaming, HF's own random access takes over.
    materialized = load_dataset(repo, streaming=False, hub_config=hub, cache_dir=cache_dir)
    doubled = materialized.map(lambda row: {"double": row["id"] * 2})
    assert doubled[2] == {"id": 2, "text": "row 2", "score": 0.5, "double": 4}
    assert doubled[-1]["double"] == 48
    assert materialized["id"][:3] == [0, 1, 2]
    assert materialized.shuffle(seed=1).__getitems__([0, 1]) == [
        materialized.shuffle(seed=1)[0],
        materialized.shuffle(seed=1)[1],
    ]


def test_map_style_dataloader(hub: HubConfig, repo: str, cache_dir: str) -> None:
    torch = pytest.importorskip("torch")
    ds = load_dataset(repo, streaming=False, hub_config=hub, cache_dir=cache_dir)
    loader = torch.utils.data.DataLoader(ds, batch_size=4)  # a sampler over __getitems__
    assert [int(i) for batch in loader for i in batch["id"]] == EXPECTED_IDS


# ---- load_dataset's arguments ----


def test_split_is_refused(hub: HubConfig, repo: str) -> None:
    with pytest.raises(TypeError, match="no splits.*filter=\"split = 'test'\""):
        load_dataset(repo, split="train", hub_config=hub)


def test_unknown_arguments_are_refused(hub: HubConfig, repo: str) -> None:
    with pytest.raises(TypeError, match=r"unexpected keyword arguments \['num_proc', 'revision'\]"):
        load_dataset(repo, revision="main", num_proc=4, hub_config=hub)
    positional: Any = load_dataset
    with pytest.raises(TypeError):
        positional(repo, 3, ["id"], hub_config=hub)


def test_bad_batch_size(hub: HubConfig, repo: str) -> None:
    with pytest.raises(ValueError, match="batch_size"):
        load_dataset(repo, batch_size=0, hub_config=hub)


@MODES
@pytest.mark.parametrize(
    "where, keep",
    [
        ("id >= 10", lambda row: row["id"] >= 10),
        ("score > 2 AND text LIKE 'row 1%'", lambda r: r["score"] > 2 and r["text"][:5] == "row 1"),
        ("id IN (1, 3, 22)", lambda row: row["id"] in (1, 3, 22)),
        ("id < 0", lambda row: False),
    ],
)
def test_filter_matches_python(
    hub: HubConfig,
    cache_dir: str,
    repo: str,
    streaming: bool,
    where: str,
    keep: Callable[[dict], bool],
) -> None:
    everything = list(load_dataset(repo, hub_config=hub))
    ds = load_dataset(repo, streaming=streaming, filter=where, hub_config=hub, cache_dir=cache_dir)
    expected = [row for row in everything if keep(row)]
    assert list(ds) == expected
    assert ds.num_rows == len(expected)
    assert ds.lance_filter == where


@MODES
def test_columns(hub: HubConfig, cache_dir: str, repo: str, streaming: bool) -> None:
    ds = load_dataset(
        repo, streaming=streaming, columns=["score", "id"], hub_config=hub, cache_dir=cache_dir
    )
    assert next(iter(ds)) == {"score": 0.0, "id": 0}
    assert ds[1] == {"score": 0.25, "id": 1}
    with pytest.raises(ValueError, match=r"unknown columns \['nope'\]"):
        load_dataset(repo, columns=["id", "nope"], hub_config=hub)


def test_public_names() -> None:
    assert set(flock.hub.__all__) >= {
        "load_dataset",
        "Dataset",
        "push",
        "create_dataset",
        "DatasetWriter",
        "CommitConflict",
    }
