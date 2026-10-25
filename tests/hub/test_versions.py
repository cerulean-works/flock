"""Versions resolve once and stay pinned; tags, restore, the card per version, clear errors."""

from typing import Any

import lance
import pytest

from flock.hub import DatasetNotFound, HubConfig, VersionNotFound, load_dataset
from tests.hub.utils import numbers, uri, write

MODES = pytest.mark.parametrize("streaming", [True, False], ids=["streaming", "materialized"])


@pytest.fixture
def three_versions(hub: HubConfig) -> str:
    """v1: rows 0-9, v2: + 10-14, v3: + 15-19."""
    write(hub, "flock/data", numbers(10))
    write(hub, "flock/data", numbers(5, start=10), mode="append")
    write(hub, "flock/data", numbers(5, start=15), mode="append")
    return "flock/data"


def ids(ds) -> list[int]:
    return [row["id"] for row in ds]


@MODES
def test_int_version(hub: HubConfig, cache_dir: str, three_versions: str, streaming: bool) -> None:
    ds = load_dataset(three_versions, 2, streaming=streaming, hub_config=hub, cache_dir=cache_dir)
    assert ds.version == 2
    assert ids(ds) == list(range(15))
    assert ds.num_rows == 15


@MODES
def test_tag(hub: HubConfig, cache_dir: str, three_versions: str, streaming: bool) -> None:
    lance.dataset(uri(hub, three_versions)).tags.create("v1.0", 1)
    ds = load_dataset(
        three_versions, "v1.0", streaming=streaming, hub_config=hub, cache_dir=cache_dir
    )
    assert ds.version == 1
    assert isinstance(ds.version, int)
    assert ids(ds) == list(range(10))


@MODES
def test_latest_is_pinned_at_load(
    hub: HubConfig, cache_dir: str, three_versions: str, streaming: bool
) -> None:
    ds = load_dataset(three_versions, streaming=streaming, hub_config=hub, cache_dir=cache_dir)
    assert ds.version == 3
    # A commit after loading is invisible to iteration, random access and the row count.
    write(hub, three_versions, numbers(5, start=20), mode="append")
    assert ids(ds) == list(range(20))
    assert ds.num_rows == len(ds) == 20
    assert ds[-1]["id"] == 19
    with pytest.raises(IndexError):
        ds[20]
    assert ds.take_rows([19, 0])["id"] == [19, 0]
    assert ds.latest_version == 4
    assert ids(ds.checkout()) == list(range(25))


def test_append_between_load_and_iteration(hub: HubConfig, three_versions: str) -> None:
    ds = load_dataset(three_versions, hub_config=hub)
    iterator = iter(ds)
    first = next(iterator)
    write(hub, three_versions, numbers(5, start=20), mode="append")
    assert [first["id"], *[row["id"] for row in iterator]] == list(range(20))


@MODES
def test_restore(hub: HubConfig, cache_dir: str, three_versions: str, streaming: bool) -> None:
    v1 = load_dataset(three_versions, 1, streaming=streaming, hub_config=hub, cache_dir=cache_dir)
    restored = v1.restore()
    assert restored.version == 4
    assert ids(restored) == list(range(10))
    assert restored.streaming == streaming
    # Restoring adds a version; it never rewrites history.
    assert ids(load_dataset(three_versions, 3, hub_config=hub)) == list(range(20))
    transaction = lance.dataset(uri(hub, three_versions)).read_transaction(4)
    assert transaction is not None
    assert type(transaction.operation).__name__ == "Restore"


def test_unknown_version(hub: HubConfig, three_versions: str) -> None:
    with pytest.raises(VersionNotFound, match="no version 9; the latest is 3 and it has no tags"):
        load_dataset(three_versions, 9, hub_config=hub)
    with pytest.raises(VersionNotFound, match="no version 0"):
        load_dataset(three_versions, 0, hub_config=hub)


def test_unknown_tag(hub: HubConfig, three_versions: str) -> None:
    lance.dataset(uri(hub, three_versions)).tags.create("v1.0", 1)
    with pytest.raises(VersionNotFound, match=r"no version 'v2.0'.*tags are \['v1.0'\]"):
        load_dataset(three_versions, "v2.0", hub_config=hub)


def test_bad_version_types(hub: HubConfig, three_versions: str) -> None:
    versions: list[Any] = [True, 1.0, [1]]
    for version in versions:
        with pytest.raises(TypeError, match="version must be"):
            load_dataset(three_versions, version, hub_config=hub)


def test_unknown_dataset(hub: HubConfig) -> None:
    with pytest.raises(DatasetNotFound, match="flock/missing not found"):
        load_dataset("flock/missing", hub_config=hub)
    with pytest.raises(ValueError, match="org/name"):
        load_dataset("no-org", hub_config=hub)


def test_card_per_version(hub: HubConfig, three_versions: str) -> None:
    ds = load_dataset(three_versions, hub_config=hub)
    assert ds.card is None
    with_card = ds.set_card("# Data\n\nFirst card.")
    assert with_card.version == 4
    assert with_card.card == "# Data\n\nFirst card."
    second = with_card.set_card("# Data\n\nSecond card, ünïcode.")
    assert second.version == 5
    assert second.card == "# Data\n\nSecond card, ünïcode."
    # Each version keeps the card it pointed to.
    assert load_dataset(three_versions, 4, hub_config=hub).card == "# Data\n\nFirst card."
    assert load_dataset(three_versions, 3, hub_config=hub).card is None
    # A card version keeps the rows, and the card itself isn't copied into the metadata.
    assert ids(second) == list(range(20))
    metadata = lance.dataset(uri(hub, three_versions)).metadata
    assert set(metadata) == {"flock.card"} and len(metadata["flock.card"]) == 64


def test_tags(hub: HubConfig, three_versions: str) -> None:
    ds = load_dataset(three_versions, 2, hub_config=hub)
    assert ds.tag("stable") is ds
    ds.tag("first", version=1)
    assert ds.tags == {"stable": 2, "first": 1}
    assert load_dataset(three_versions, "stable", hub_config=hub).version == 2
    ds.untag("stable")
    assert ds.tags == {"first": 1}
    with pytest.raises(VersionNotFound):
        load_dataset(three_versions, "stable", hub_config=hub)


@MODES
def test_checkout_keeps_settings(
    hub: HubConfig, cache_dir: str, three_versions: str, streaming: bool
) -> None:
    ds = load_dataset(
        three_versions,
        3,
        columns=["id"],
        filter="id % 2 = 0",
        streaming=streaming,
        hub_config=hub,
        cache_dir=cache_dir,
    )
    v1 = ds.checkout(1)
    assert (v1.version, v1.columns, v1.lance_filter, v1.streaming) == (
        1,
        ["id"],
        "id % 2 = 0",
        streaming,
    )
    assert list(v1) == [{"id": i} for i in range(0, 10, 2)]
