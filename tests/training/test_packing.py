"""CPU checks for packed rows: best-fit decreasing, the packed dataset and packed collation."""

import hashlib
import json
import random
from itertools import pairwise
from pathlib import Path
from typing import Any

import lance
import numpy as np
import pyarrow as pa
import pytest
import torch
from lance.torch.data import SafeLanceDataset
from torch.utils.data import DataLoader
from transformers import TokenizersBackend

from flock.training import TokenChunkDataset
from flock.training.core.chunks import _best_fit_decreasing
from flock.training.core.data_utils import LanceBatchCollator, ResumableBatchDataSampler


def reference_fills(sizes: list[int], capacity: int) -> list[int]:
    """Independent quadratic best-fit decreasing: each row's filled size, sorted."""
    fills: list[int] = []
    for item in sorted(range(len(sizes)), key=lambda index: (-sizes[index], index)):
        fits = [row for row, used in enumerate(fills) if capacity - used >= sizes[item]]
        if fits:
            fills[min(fits, key=lambda row: capacity - fills[row])] += sizes[item]
        else:
            fills.append(sizes[item])
    return sorted(fills)


@pytest.mark.parametrize("capacity", [1, 7, 16, 100])
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_best_fit_decreasing_matches_a_reference_packing(capacity: int, seed: int) -> None:
    generator = random.Random(seed)
    sizes = [generator.randint(1, capacity) for _ in range(200)]
    offsets, items = _best_fit_decreasing(np.array(sizes), capacity=capacity)
    rows = [items[start:stop].tolist() for start, stop in pairwise(offsets.tolist())]
    # Rows hold the original item indices, each exactly once.
    assert sorted(items.tolist()) == list(range(len(sizes)))
    assert all(sum(sizes[i] for i in row) <= capacity for row in rows)
    # Rows with equal free space are interchangeable, so compare the fills.
    assert sorted(sum(sizes[i] for i in row) for row in rows) == reference_fills(sizes, capacity)
    again = _best_fit_decreasing(np.array(sizes), capacity=capacity)
    assert all(np.array_equal(a, b) for a, b in zip(again, (offsets, items), strict=True))


def prepare_cache(
    tmp_path: Path, tokenizer: TokenizersBackend, context: int
) -> tuple[TokenChunkDataset, list[list[int]]]:
    texts = ["", "a", "b [EOS]"]
    for length in [context - 1, context, context + 1, 2 * context - 1, 2 * context, 3]:
        texts.append("a " * (length - 1) + "[EOS]")
    texts.append("b c d [EOS]")
    path = str(tmp_path / "source.lance")
    lance.write_dataset(pa.table({"text": texts, "document_id": list(range(len(texts)))}), path)
    dataset = TokenChunkDataset.prepare(
        dataset=SafeLanceDataset(path, dataset_options={"version": 1}),
        tokenizer=tokenizer,
        input_column="text",
        cache_dir=tmp_path / "tokens",
        chunk_length=context,
    )
    return dataset, tokenizer(texts, truncation=False, padding=False)["input_ids"]


@pytest.mark.parametrize("context", [2, 4, 7])
def test_packed_rows_hold_every_chunk_once_within_capacity(
    tmp_path: Path, tokenizer: TokenizersBackend, context: int
) -> None:
    chunks, _ = prepare_cache(tmp_path, tokenizer, context)
    packed = TokenChunkDataset(cache_dir=tmp_path / "tokens", chunk_length=context, packing=True)
    expected = [chunks[index] for index in range(len(chunks))]

    seen = []
    for row in packed:
        # A chunk of L token IDs takes L - 1 positions of a row.
        assert sum(len(chunk["_flock_chunk_ids"]) - 1 for chunk in row["_flock_chunks"]) <= (
            context - 1
        )
        seen.extend(row["_flock_chunks"])

    def key(row: dict[str, Any]) -> tuple[int, int]:
        return row["_flock_document_index"], row["_flock_chunk_index"]

    # Every chunk, with its document metadata, lands in exactly one row.
    assert sorted(seen, key=key) == sorted(expected, key=key)
    # With two-token chunks every chunk fills a row, so nothing packs together.
    assert len(packed) < len(chunks) or context == 2

    # Unpacked caches keep their earlier fingerprint; packing changes it.
    earlier = json.dumps([chunks.manifest, context], sort_keys=True).encode()
    assert chunks.fingerprint == hashlib.sha256(earlier).hexdigest()
    assert packed.fingerprint != chunks.fingerprint
    reopened = TokenChunkDataset(cache_dir=tmp_path / "tokens", chunk_length=context, packing=True)
    assert reopened.fingerprint == packed.fingerprint
    rows = list(range(len(packed)))
    assert reopened.__getitems__(rows) == packed.__getitems__(rows)
    with pytest.raises(IndexError):
        packed[len(packed)]


def chunk(*ids: int) -> dict[str, list[int]]:
    return {"_flock_chunk_ids": list(ids)}


def test_packed_collation_isolates_documents_and_pads_to_the_longest_row(
    tokenizer: TokenizersBackend,
) -> None:
    pad = tokenizer.pad_token_id
    collator = LanceBatchCollator(tokenizer, "text", token_chunks=True)
    batch = collator.collate_fn(
        [
            {"_flock_chunks": [chunk(5, 6, 7), chunk(8, 9)]},
            {"_flock_chunks": [chunk(2, 3, 4, 5, 6)]},
        ]
    )
    # The last input of a document predicts that document's last token, never the next one.
    assert batch["input_ids"].tolist() == [[5, 6, 8, pad], [2, 3, 4, 5]]
    assert batch["labels"].tolist() == [[6, 7, 9, -100], [3, 4, 5, 6]]
    assert batch["attention_mask"].tolist() == [[True, True, True, False], [True] * 4]
    assert batch["position_ids"].tolist() == [[0, 1, 0, 0], [0, 1, 2, 3]]
    assert batch["cu_seq_lens"].dtype == torch.int32
    assert batch["cu_seq_lens"].tolist() == [0, 2, 3, 4, 8]

    # The longest row sets the width even when it is shorter than a full row.
    narrow = collator.collate_fn(
        [{"_flock_chunks": [chunk(2, 3, 4)]}, {"_flock_chunks": [chunk(5, 2)]}]
    )
    assert narrow["input_ids"].tolist() == [[2, 3], [5, pad]]
    assert narrow["labels"].tolist() == [[3, 4], [2, -100]]
    assert narrow["cu_seq_lens"].tolist() == [0, 2, 3, 4]

    # A rank past the end of the data collates its empty batch as an unpacked one.
    assert "cu_seq_lens" not in collator.collate_fn([])


@pytest.mark.parametrize("context", [2, 4, 7])
@pytest.mark.parametrize("workers", [0, 1])
def test_packed_pass_trains_every_original_target_once(
    tmp_path: Path, tokenizer: TokenizersBackend, context: int, workers: int
) -> None:
    _, expected = prepare_cache(tmp_path, tokenizer, context)
    packed = TokenChunkDataset(cache_dir=tmp_path / "tokens", chunk_length=context, packing=True)
    collator = LanceBatchCollator(tokenizer, "text", token_chunks=True)
    pairs = []
    outputs = []
    for skip in [0, 1]:
        loader = DataLoader(
            packed,
            batch_sampler=ResumableBatchDataSampler(3, len(packed), seed=5, batch_cursor=skip),
            collate_fn=collator.collate_fn,
            num_workers=workers,
            multiprocessing_context="spawn" if workers else None,
        )
        outputs.append(list(loader))
    for batch in outputs[0]:
        rows, width = batch["input_ids"].shape
        assert width <= context - 1
        cu = batch["cu_seq_lens"].tolist()
        assert cu[0] == 0 and cu[-1] == rows * width
        flat = {key: batch[key].flatten() for key in ("input_ids", "labels", "attention_mask")}
        positions = batch["position_ids"].flatten()
        for start, stop in pairwise(cu):
            assert stop > start
            # Documents never span rows, and positions restart in each one.
            assert start // width == (stop - 1) // width
            assert positions[start:stop].tolist() == list(range(stop - start))
            mask = flat["attention_mask"][start:stop]
            labels = flat["labels"][start:stop]
            if not mask.any():
                assert (labels == -100).all()
                continue
            assert mask.all()
            inputs = flat["input_ids"][start:stop]
            # Within a document, each target is the next input.
            assert labels[:-1].tolist() == inputs[1:].tolist()
            pairs.extend(zip(inputs.tolist(), labels.tolist(), strict=True))
    assert sorted(pairs) == sorted(pair for ids in expected for pair in pairwise(ids))
    # Resuming after one batch replays the same suffix.
    for before, after in zip(outputs[0][1:], outputs[1], strict=True):
        assert before.keys() == after.keys()
        assert all(torch.equal(before[key], after[key]) for key in before)
