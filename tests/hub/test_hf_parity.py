"""Our reads against HF's packaged Lance builder: same rows, order, features and shards."""

from collections.abc import Callable
from typing import Any

import datasets
import lance
import pyarrow as pa
import pytest
from lance.fragment import LanceFragment

from flock.hub import HubConfig, load_dataset
from tests.hub.utils import all_types, hf_lance, numbers, uri, without, write

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def one_fragment(hub: HubConfig) -> str:
    write(hub, "flock/one", numbers(10))
    return "flock/one"


def uneven_fragments(hub: HubConfig) -> str:
    write(hub, "flock/uneven", numbers(20), max_rows_per_file=7)
    return "flock/uneven"


def many_tiny_fragments(hub: HubConfig) -> str:
    write(hub, "flock/tiny", numbers(1))
    for i in range(1, 25):
        write(hub, "flock/tiny", numbers(1, start=i), mode="append")
    return "flock/tiny"


def with_deletions(hub: HubConfig) -> str:
    ds = write(hub, "flock/deleted", numbers(30), max_rows_per_file=10)
    ds.delete("id % 3 = 0 OR (id >= 10 AND id < 20)")
    return "flock/deleted"


def every_type(hub: HubConfig) -> str:
    # HF's builder can't infer features for blob v2 columns (see test_blob_columns).
    write(hub, "flock/types", without(all_types(), "blob"), max_rows_per_file=2)
    return "flock/types"


def magic_bytes(hub: HubConfig) -> str:
    table = pa.table({"id": [0, 1], "image": [PNG, PNG], "raw": [b"plain", b"bytes"]})
    write(hub, "flock/magic", table)
    return "flock/magic"


FIXTURES = [
    one_fragment,
    uneven_fragments,
    many_tiny_fragments,
    with_deletions,
    every_type,
    magic_bytes,
]


def _comparable(ds: Any) -> Any:
    # Decoding images needs Pillow, which neither side needs for the comparison.
    if "image" in (ds.features or {}):
        ds = ds.cast_column("image", datasets.Image(decode=False))
    return ds


@pytest.mark.parametrize("streaming", [True, False], ids=["streaming", "materialized"])
@pytest.mark.parametrize("make", FIXTURES, ids=[f.__name__ for f in FIXTURES])
def test_same_rows_features_and_shards(
    hub: HubConfig, cache_dir: str, make: Callable[[HubConfig], str], streaming: bool
) -> None:
    repo_id = make(hub)
    ours = load_dataset(repo_id, streaming=streaming, hub_config=hub, cache_dir=cache_dir)
    theirs = hf_lance(uri(hub, repo_id), streaming, cache_dir + "-hf")

    assert ours.features == theirs.features
    assert list(_comparable(ours)) == list(_comparable(theirs))
    fragments = len(lance.dataset(uri(hub, repo_id)).get_fragments())
    if streaming:
        assert isinstance(theirs, datasets.IterableDataset)
        assert ours.num_shards == theirs.num_shards == fragments
    else:
        assert isinstance(theirs, datasets.Dataset)
        assert ours.num_rows == theirs.num_rows


@pytest.mark.parametrize("streaming", [True, False], ids=["streaming", "materialized"])
@pytest.mark.parametrize("columns", [["text"], ["score", "id"], ["id", "text", "score"]])
def test_same_columns(hub: HubConfig, cache_dir: str, columns: list[str], streaming: bool) -> None:
    repo_id = uneven_fragments(hub)
    ours = load_dataset(
        repo_id, streaming=streaming, columns=columns, hub_config=hub, cache_dir=cache_dir
    )
    theirs = hf_lance(uri(hub, repo_id), streaming, cache_dir + "-hf", columns=columns)

    assert list(ours.features or {}) == columns
    assert ours.features == theirs.features
    assert list(ours) == list(theirs)


@pytest.mark.parametrize("batch_size", [1, 3, 256])
def test_same_lance_reads(
    hub: HubConfig, cache_dir: str, monkeypatch: pytest.MonkeyPatch, batch_size: int
) -> None:
    """Both read each fragment with the same `to_batches` arguments, in the same order."""
    repo_id = uneven_fragments(hub)
    calls: list[tuple[int, dict[str, Any]]] = []
    original = LanceFragment.to_batches

    def spy(self: LanceFragment, **kwargs: Any) -> Any:
        calls.append((self.fragment_id, kwargs))
        return original(self, **kwargs)

    monkeypatch.setattr(LanceFragment, "to_batches", spy)
    ours = load_dataset(repo_id, columns=["id"], batch_size=batch_size, hub_config=hub)
    ours_rows = list(ours)
    ours_calls, calls[:] = list(calls), []
    theirs_rows = list(
        hf_lance(uri(hub, repo_id), True, cache_dir, columns=["id"], batch_size=batch_size)
    )

    assert ours_rows == theirs_rows
    keys = ("columns", "batch_size", "blob_handling")
    assert [(f, {k: kw.get(k) for k in keys}) for f, kw in ours_calls] == [
        (f, {k: kw.get(k) for k in keys}) for f, kw in calls
    ]
    assert ours_calls[0][1]["blob_handling"] == "all_binary"


def test_default_batch_size_matches(hub: HubConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    from datasets.packaged_modules.lance.lance import LanceConfig

    from flock.hub.dataset import DEFAULT_BATCH_SIZE

    assert LanceConfig.batch_size == DEFAULT_BATCH_SIZE


@pytest.mark.parametrize("streaming", [True, False], ids=["streaming", "materialized"])
def test_blob_columns(hub: HubConfig, cache_dir: str, streaming: bool) -> None:
    """HF's builder fails on blob v2 columns; we read them as `large_binary`, as its rows are."""
    write(hub, "flock/blobs", all_types().select(["i8", "blob"]), max_rows_per_file=2)
    with pytest.raises(ValueError, match="lance.blob"):
        hf_lance(uri(hub, "flock/blobs"), streaming, cache_dir + "-hf")

    ours = load_dataset("flock/blobs", streaming=streaming, hub_config=hub, cache_dir=cache_dir)
    assert ours.features == datasets.Features(
        {"i8": datasets.Value("int8"), "blob": datasets.Value("large_binary")}
    )
    assert [row["blob"] for row in ours] == [b"blob bytes", None, b""]


def test_magic_bytes_make_media_features(hub: HubConfig) -> None:
    ds = load_dataset(magic_bytes(hub), hub_config=hub)
    assert ds.features == datasets.Features(
        {
            "id": datasets.Value("int64"),
            "image": datasets.Image(),
            "raw": datasets.Value("binary"),
        }
    )
