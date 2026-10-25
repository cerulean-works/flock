"""Datasets of unusual shapes: empty, tiny, fragmented, deleted from, compacted, altered."""

import datasets
import lance
import pyarrow as pa
import pytest

from flock.hub import HubConfig, load_dataset
from tests.hub.utils import numbers, uri, write

MODES = pytest.mark.parametrize("streaming", [True, False], ids=["streaming", "materialized"])


@MODES
def test_schema_only(hub: HubConfig, cache_dir: str, streaming: bool) -> None:
    write(hub, "flock/empty", numbers(0))
    ds = load_dataset("flock/empty", streaming=streaming, hub_config=hub, cache_dir=cache_dir)

    assert ds.num_rows == 0 and len(ds) == 0
    assert list(ds) == []
    assert list(ds.features or {}) == ["id", "text", "score"]
    with pytest.raises(IndexError):
        ds[0]
    assert ds[0:5] == {"id": [], "text": [], "score": []}


@MODES
def test_one_row(hub: HubConfig, cache_dir: str, streaming: bool) -> None:
    write(hub, "flock/one", numbers(1))
    ds = load_dataset("flock/one", streaming=streaming, hub_config=hub, cache_dir=cache_dir)
    row = {"id": 0, "text": "row 0", "score": 0.0}
    assert list(ds) == [row]
    assert ds[0] == ds[-1] == row
    assert ds.num_rows == 1


@MODES
def test_many_tiny_fragments(hub: HubConfig, cache_dir: str, streaming: bool) -> None:
    write(hub, "flock/tiny", numbers(1))
    for i in range(1, 60):
        write(hub, "flock/tiny", numbers(1, start=i), mode="append")
    ds = load_dataset("flock/tiny", streaming=streaming, hub_config=hub, cache_dir=cache_dir)

    assert [row["id"] for row in ds] == list(range(60))
    assert ds.take_rows([59, 0, 30])["id"] == [59, 0, 30]
    if streaming:
        assert ds.num_shards == 60


@MODES
def test_one_large_fragment(hub: HubConfig, cache_dir: str, streaming: bool) -> None:
    write(hub, "flock/large", numbers(20_000))
    ds = load_dataset(
        "flock/large", streaming=streaming, batch_size=1000, hub_config=hub, cache_dir=cache_dir
    )
    assert len(lance.dataset(uri(hub, "flock/large")).get_fragments()) == 1
    assert [row["id"] for row in ds] == list(range(20_000))
    assert ds[12_345]["text"] == "row 12345"


@MODES
def test_deleted_rows_never_appear(hub: HubConfig, cache_dir: str, streaming: bool) -> None:
    lance_ds = write(hub, "flock/deleted", numbers(30), max_rows_per_file=10)
    # Every third row, and the whole middle fragment.
    lance_ds.delete("id % 3 = 0 OR (id >= 10 AND id < 20)")
    kept = [i for i in range(30) if i % 3 != 0 and not 10 <= i < 20]

    ds = load_dataset("flock/deleted", streaming=streaming, hub_config=hub, cache_dir=cache_dir)
    assert [row["id"] for row in ds] == kept
    assert ds.num_rows == len(kept)
    assert [ds[i]["id"] for i in range(len(kept))] == kept
    assert ds[-1]["id"] == kept[-1]
    # The version before the delete still has every row.
    before = load_dataset(
        "flock/deleted", 1, streaming=streaming, hub_config=hub, cache_dir=cache_dir
    )
    assert [row["id"] for row in before] == list(range(30))


@MODES
def test_after_compaction(hub: HubConfig, cache_dir: str, streaming: bool) -> None:
    write(hub, "flock/compact", numbers(5))
    for i in range(1, 6):
        write(hub, "flock/compact", numbers(5, start=5 * i), mode="append")
    lance_ds = lance.dataset(uri(hub, "flock/compact"))
    lance_ds.delete("id = 7")
    lance_ds.optimize.compact_files()
    compacted = lance.dataset(uri(hub, "flock/compact"))
    assert len(compacted.get_fragments()) < 6

    ds = load_dataset("flock/compact", streaming=streaming, hub_config=hub, cache_dir=cache_dir)
    expected = [i for i in range(30) if i != 7]
    assert sorted(row["id"] for row in ds) == expected
    assert sorted(ds.take_rows(range(29))["id"]) == expected
    if streaming:
        assert ds.num_shards == len(compacted.get_fragments())


@MODES
def test_schema_at_each_version(hub: HubConfig, cache_dir: str, streaming: bool) -> None:
    write(hub, "flock/altered", numbers(4))  # v1: id, text, score
    lance_ds = lance.dataset(uri(hub, "flock/altered"))
    lance_ds.add_columns({"double": "id * 2"})  # v2
    lance_ds.drop_columns(["text"])  # v3
    lance_ds.add_columns(pa.field("note", pa.string()))  # v4, all null

    def load(version: int) -> list[dict]:
        ds = load_dataset(
            "flock/altered", version, streaming=streaming, hub_config=hub, cache_dir=cache_dir
        )
        return list(ds)

    assert load(1)[3] == {"id": 3, "text": "row 3", "score": 0.75}
    assert load(2)[3] == {"id": 3, "text": "row 3", "score": 0.75, "double": 6}
    assert load(3)[3] == {"id": 3, "score": 0.75, "double": 6}
    assert load(4)[3] == {"id": 3, "score": 0.75, "double": 6, "note": None}
    v4 = load_dataset("flock/altered", 4, streaming=streaming, hub_config=hub, cache_dir=cache_dir)
    assert v4.features == datasets.Features(
        {
            "id": datasets.Value("int64"),
            "score": datasets.Value("float64"),
            "double": datasets.Value("int64"),
            "note": datasets.Value("string"),
        }
    )
