"""Streaming behaves like HF's: resumable, sharded across workers and nodes, shuffled the same way."""

import pickle
from collections import Counter

import pytest

from flock.hub import HubConfig, load_dataset, split_dataset_by_node
from tests.hub.utils import numbers, write

ROWS = 23  # over 4 fragments of 6, 6, 6 and 5 rows


@pytest.fixture
def repo(hub: HubConfig) -> str:
    write(hub, "flock/stream", numbers(ROWS), max_rows_per_file=6)
    return "flock/stream"


def ids(rows) -> list[int]:
    return [row["id"] for row in rows]


@pytest.mark.parametrize("stop", range(ROWS + 1))
def test_resume_after_every_row(hub: HubConfig, repo: str, stop: int) -> None:
    """Covers every shard boundary (6, 12, 18) and every position inside a shard."""
    ds = load_dataset(repo, batch_size=4, hub_config=hub)
    iterator = iter(ds)
    seen = [next(iterator)["id"] for _ in range(stop)]
    state = ds.state_dict()

    resumed = load_dataset(repo, batch_size=4, hub_config=hub)
    resumed.load_state_dict(state)
    assert seen + ids(resumed) == list(range(ROWS))


def test_resume_state_pickles(hub: HubConfig, repo: str) -> None:
    ds = load_dataset(repo, hub_config=hub)
    iterator = iter(ds)
    for _ in range(8):
        next(iterator)
    resumed = load_dataset(repo, hub_config=hub)
    resumed.load_state_dict(pickle.loads(pickle.dumps(ds.state_dict())))
    assert ids(resumed) == list(range(8, ROWS))


def test_resume_after_map(hub: HubConfig, repo: str) -> None:
    ds = load_dataset(repo, hub_config=hub).map(lambda row: {"double": row["id"] * 2})
    iterator = iter(ds)
    seen = [next(iterator)["double"] for _ in range(10)]
    resumed = load_dataset(repo, hub_config=hub).map(lambda row: {"double": row["id"] * 2})
    resumed.load_state_dict(ds.state_dict())
    assert seen + [row["double"] for row in resumed] == [2 * i for i in range(ROWS)]


@pytest.mark.parametrize("num_workers", [0, 1, 2, 3, 4])
def test_dataloader_workers(hub: HubConfig, repo: str, num_workers: int) -> None:
    torch = pytest.importorskip("torch")
    ds = load_dataset(repo, hub_config=hub)
    loader = torch.utils.data.DataLoader(ds, batch_size=5, num_workers=num_workers)
    seen = [int(i) for batch in loader for i in batch["id"]]
    assert Counter(seen) == Counter(range(ROWS))


def test_is_a_torch_iterable_dataset(hub: HubConfig, repo: str) -> None:
    torch = pytest.importorskip("torch")
    ds = load_dataset(repo, hub_config=hub)
    assert isinstance(ds, torch.utils.data.IterableDataset)
    assert isinstance(ds.map(lambda row: row), torch.utils.data.IterableDataset)
    materialized = load_dataset(repo, streaming=False, hub_config=hub)
    assert not isinstance(materialized, torch.utils.data.IterableDataset)


@pytest.mark.parametrize("world_size", [1, 2, 4, 3, 5, 7])
def test_split_dataset_by_node(hub: HubConfig, repo: str, world_size: int) -> None:
    ds = load_dataset(repo, hub_config=hub)
    parts = [split_dataset_by_node(ds, rank, world_size) for rank in range(world_size)]
    seen = [i for part in parts for i in ids(part)]
    assert Counter(seen) == Counter(range(ROWS))
    if 4 % world_size == 0:
        # A world size that divides the fragment count gets whole fragments.
        assert all(part.num_shards == 4 // world_size for part in parts)
    assert all(part.version == ds.version for part in parts)


def test_split_dataset_by_node_materialized(hub: HubConfig, repo: str, cache_dir: str) -> None:
    ds = load_dataset(repo, streaming=False, hub_config=hub, cache_dir=cache_dir)
    parts = [split_dataset_by_node(ds, rank, 3) for rank in range(3)]
    assert [i for part in parts for i in ids(part)] == list(range(ROWS))


def test_shuffle_is_deterministic_and_a_permutation(hub: HubConfig, repo: str) -> None:
    ds = load_dataset(repo, hub_config=hub)
    first = ids(ds.shuffle(seed=7, buffer_size=5))
    assert first == ids(ds.shuffle(seed=7, buffer_size=5))
    assert sorted(first) == list(range(ROWS))
    assert first != list(range(ROWS))
    assert ids(ds.shuffle(seed=8, buffer_size=5)) != first


def test_shuffle_with_epochs(hub: HubConfig, repo: str) -> None:
    shuffled = load_dataset(repo, hub_config=hub).shuffle(seed=1, buffer_size=10)
    shuffled.set_epoch(0)
    epoch0 = ids(shuffled)
    shuffled.set_epoch(1)
    epoch1 = ids(shuffled)
    assert sorted(epoch0) == sorted(epoch1) == list(range(ROWS))
    assert epoch0 != epoch1


def test_skip_and_take(hub: HubConfig, repo: str) -> None:
    ds = load_dataset(repo, hub_config=hub)
    assert ids(ds.skip(3)) == list(range(3, ROWS))
    assert ids(ds.take(5)) == list(range(5))
    assert ids(ds.skip(4).take(7)) == list(range(4, 11))
    assert ids(ds.take(100)) == list(range(ROWS))
    assert ids(ds.skip(100)) == []
    assert ds.skip(3).num_rows == ROWS - 3
    assert ds.take(5).num_rows == 5
    assert ds.skip(4).take(7).num_rows == 7


def test_pickled_dataset_reads_the_same(hub: HubConfig, repo: str) -> None:
    ds = load_dataset(repo, hub_config=hub)
    clone = pickle.loads(pickle.dumps(ds))
    assert ids(clone) == ids(ds) == list(range(ROWS))
    assert clone[5] == ds[5]
