"""Tokenizing raw text during training: exact rows, ordering, resume and trainer integration."""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING

import lance
import numpy as np
import pyarrow as pa
import pytest
import torch

from flock.training.core.streaming import (
    BlockTokenizer,
    StreamPosition,
    TextStream,
    first_position,
    stream_rows,
)

if TYPE_CHECKING:
    from pathlib import Path

    from transformers import TokenizersBackend


WORDS = ["a", "b", "c", "d"]


def make_stream(tmp_path: Path) -> TextStream:
    """Write a small text dataset; lengths run from empty to longer than a chunk."""
    rng = np.random.default_rng(0)
    texts = [" ".join(rng.choice(WORDS, size=n)) for n in [0, 1, 2, 9, 3, 14, 5, 1, 7, 4, 11, 6]]
    path = tmp_path / "text.lance"
    lance.write_dataset(pa.table({"text": texts}), str(path))
    return TextStream(path=path, block_rows=3, chunk_length=5)


@pytest.fixture
def stream(tmp_path: Path) -> TextStream:
    return make_stream(tmp_path)


def rows(stream: TextStream, tokenizer: TokenizersBackend, **kwargs: object) -> list[dict]:
    options = {"start": first_position(0), "batch_tokens": 6, "num_workers": 0, **kwargs}
    return list(stream_rows(stream, tokenizer, **options))  # ty: ignore[invalid-argument-type]


def documents(stream: TextStream, tokenizer: TokenizersBackend) -> list[np.ndarray]:
    blocks = BlockTokenizer(stream, tokenizer)
    return [doc for block in range(stream.blocks) for doc in blocks[block]]


def test_rows_are_full_and_reproduce_every_target_in_order(
    stream: TextStream, tokenizer: TokenizersBackend
) -> None:
    packed = rows(stream, tokenizer)
    expected_targets = sum(max(len(d) - 1, 0) for d in documents(stream, tokenizer))
    for row in packed[:-1]:
        assert (row["labels"] != -100).sum() == 6
    assert sum(int((row["labels"] != -100).sum()) for row in packed) == expected_targets

    # Walk the pieces: each is (context, targets) from one document, in stream order.
    pieces = []
    for row in packed:
        bounds = row["cu_seqlens"].tolist()
        assert bounds[0] == 0 and bounds[-1] == 6
        for start, stop in itertools.pairwise(bounds):
            assert 1 <= stop - start <= stream.chunk_length - 1
            assert row["position_ids"][0, start:stop].tolist() == list(range(stop - start))
            labels = row["labels"][0, start:stop]
            if (labels != -100).any():
                pieces.append((row["input_ids"][0, start:stop].tolist(), labels.tolist()))
    rebuilt_inputs, rebuilt_labels = [], []
    for inputs, labels in pieces:
        rebuilt_inputs += inputs
        rebuilt_labels += labels
    want_inputs, want_labels = [], []
    for doc in documents(stream, tokenizer):
        want_inputs += doc[:-1].tolist()
        want_labels += doc[1:].tolist()
    assert rebuilt_inputs == want_inputs
    assert rebuilt_labels == want_labels


def test_documents_end_with_eos_and_bos_is_optional(
    stream: TextStream, tokenizer: TokenizersBackend
) -> None:
    eos = tokenizer.eos_token_id
    assert all(doc[-1] == eos for doc in documents(stream, tokenizer))
    tokenizer.bos_token = "[UNK]"  # The fixture has no BOS; borrow a token for the check.
    with_bos = TextStream(path=stream.path, block_rows=3, chunk_length=5, add_bos=True)
    assert all(doc[0] == tokenizer.bos_token_id for doc in documents(with_bos, tokenizer))
    with pytest.raises(ValueError, match="no BOS"):
        tokenizer.bos_token = None
        TextStream(path=stream.path, add_bos=True).special_ids(tokenizer)


def test_rows_do_not_depend_on_worker_count(
    stream: TextStream, tokenizer: TokenizersBackend
) -> None:
    serial = rows(stream, tokenizer)
    parallel = rows(stream, tokenizer, num_workers=2, prefetch_factor=2)
    assert len(serial) == len(parallel)
    for a, b in zip(serial, parallel, strict=True):
        for key in a:
            assert torch.equal(a[key], b[key]), key


def test_resuming_from_a_recorded_position_continues_the_stream(
    stream: TextStream, tokenizer: TokenizersBackend
) -> None:
    full = rows(stream, tokenizer)
    packer = stream_rows(stream, tokenizer, start=first_position(0), batch_tokens=6, num_workers=0)
    iterator = iter(packer)
    positions = []
    for _ in range(len(full) - 1):
        next(iterator)
        positions.append(packer.position)
    for done, position in enumerate(positions, start=1):
        resumed = rows(stream, tokenizer, start=position)
        assert len(resumed) == len(full) - done
        for a, b in zip(full[done:], resumed, strict=True):
            for key in a:
                assert torch.equal(a[key], b[key]), (done, key)


def test_ranks_read_disjoint_blocks(stream: TextStream, tokenizer: TokenizersBackend) -> None:
    targets = [
        sum(
            int((row["labels"] != -100).sum())
            for row in rows(stream, tokenizer, start=first_position(rank), rank=rank, world_size=2)
        )
        for rank in range(2)
    ]
    assert sum(targets) == sum(max(len(d) - 1, 0) for d in documents(stream, tokenizer))
    with pytest.raises(ValueError, match="does not belong"):
        rows(stream, tokenizer, start=StreamPosition(1), rank=0, world_size=2)
