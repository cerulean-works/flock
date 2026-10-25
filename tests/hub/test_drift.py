"""
Guards the assumptions we copied from HF's Lance builder against the installed `datasets`.

If one of these fails after upgrading `datasets`, the builder changed: read the new
`datasets/packaged_modules/lance/lance.py` and bring `flock/hub/dataset.py` in line.
"""

from typing import Any

import datasets
import lance
import pyarrow as pa
import pytest
from datasets.packaged_modules.lance import lance as hf_lance_module
from lance.fragment import LanceFragment

from flock.hub import HubConfig
from flock.hub.dataset import _MAGIC_BYTES, DEFAULT_BATCH_SIZE, infer_features
from tests.hub.utils import all_types, hf_lance, numbers, uri, without, write


def test_builder_is_still_arrow_based() -> None:
    assert issubclass(hf_lance_module.Lance, datasets.ArrowBasedBuilder)
    assert hf_lance_module.LanceConfig.batch_size == DEFAULT_BATCH_SIZE


def test_one_shard_per_fragment(hub: HubConfig, cache_dir: str) -> None:
    write(hub, "flock/frags", numbers(17), max_rows_per_file=4)
    fragments = lance.dataset(uri(hub, "flock/frags")).get_fragments()
    hf = hf_lance(uri(hub, "flock/frags"), True, cache_dir)
    assert isinstance(hf, datasets.IterableDataset)
    assert len(fragments) == hf.num_shards == 5


def test_reads_fragments_with_all_binary(
    hub: HubConfig, cache_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(hub, "flock/frags", numbers(10), max_rows_per_file=4)
    calls: list[tuple[int, dict[str, Any]]] = []
    original = LanceFragment.to_batches

    def spy(self: LanceFragment, **kwargs: Any) -> Any:
        calls.append((self.fragment_id, kwargs))
        return original(self, **kwargs)

    monkeypatch.setattr(LanceFragment, "to_batches", spy)
    list(hf_lance(uri(hub, "flock/frags"), True, cache_dir))
    assert [fragment for fragment, _ in calls] == [0, 1, 2]
    assert all(kwargs["blob_handling"] == "all_binary" for _, kwargs in calls)
    assert all(kwargs["batch_size"] == DEFAULT_BATCH_SIZE for _, kwargs in calls)
    # It still has no filter or version of its own, which is why we don't call it.
    assert all(kwargs.get("filter") is None for _, kwargs in calls)


def test_same_magic_bytes() -> None:
    theirs = [
        (magic, type(feature))
        for magic, _, feature in hf_lance_module.MAGIC_BYTES_EXTENSION_AND_FEATURE_TYPES
    ]
    assert [(magic, feature) for magic, feature in _MAGIC_BYTES] == theirs


def test_same_features(hub: HubConfig, cache_dir: str) -> None:
    png = b"\x89PNG\r\n\x1a\n" + bytes(8)
    table = without(all_types(), "blob").append_column("image", pa.array([png, None, png]))
    write(hub, "flock/features", table)
    lance_ds = lance.dataset(uri(hub, "flock/features"))
    theirs = hf_lance(uri(hub, "flock/features"), True, cache_dir).features
    assert infer_features(lance_ds, None) == theirs
    assert (
        infer_features(lance_ds, ["image", "i8"])
        == hf_lance(uri(hub, "flock/features"), True, cache_dir, columns=["image", "i8"]).features
    )


def test_still_fails_on_blob_v2(hub: HubConfig, cache_dir: str) -> None:
    """Our one deviation in feature inference: blob v2 columns read as `large_binary`."""
    write(hub, "flock/blob", all_types().select(["blob"]))
    with pytest.raises(ValueError, match="lance.blob.v2"):
        hf_lance(uri(hub, "flock/blob"), True, cache_dir)
