"""Tokenize a raw-text Lance dataset during training and pack it into fixed token-budget rows.

DataLoader workers tokenize fixed blocks of consecutive rows ahead of time; the training process
cuts the resulting token stream into rows of exactly `batch_tokens` next-token targets. Every
update therefore trains on a known number of tokens, so token targets map to updates before
training starts, and a rank's position in the stream is three integers that resume it exactly.
"""

import hashlib
import json
import math
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import lance
import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset, Sampler
from transformers import TokenizersBackend

from flock.training.core.chunks import TokenChunkDataset
from flock.training.core.data_utils import Batch


@dataclass(frozen=True)
class StreamPosition:
    """Where a rank's next row starts: `token` within `document` of global `block`.

    The next row's first target predicts token `token + 1` of that document.
    """

    block: int
    document: int = 0
    token: int = 0

    def as_dict(self) -> dict[str, int]:
        """Return the position as checkpoint integers."""
        return {"block": self.block, "document": self.document, "token": self.token}


class TextStream:
    """A raw-text Lance dataset, read in stored order and tokenized during training.

    Rows are grouped into blocks of `block_rows` consecutive rows; rank `r` of `w` trains
    on blocks `r, r + w, r + 2w, ...`. Read order is the stored order, so shuffle the
    dataset when writing it. Each document becomes `[BOS] + tokens + [EOS]` as configured,
    and is cut into packed documents of at most `chunk_length - 1` targets.

    Args:
        path (`str`): Local Lance dataset directory.
        input_column (`str`, *optional*, defaults to `"text"`): Text column.
        version (`int`, *optional*): Lance version; defaults to the latest, pinned at construction.
        block_rows (`int`, *optional*, defaults to `2048`): Rows each worker tokenizes at once.
        chunk_length (`int`, *optional*, defaults to `4096`): Token IDs per packed document,
            including one context token, as in `TokenChunkDataset`.
        add_bos (`bool`, *optional*, defaults to `False`): Start each document with BOS.
        add_eos (`bool`, *optional*, defaults to `True`): End each document with EOS.
    """

    def __init__(
        self,
        *,
        path: str | Path,
        input_column: str = "text",
        version: int | None = None,
        block_rows: int = 2048,
        chunk_length: int = 4096,
        add_bos: bool = False,
        add_eos: bool = True,
    ) -> None:
        """Open the dataset to pin its version and count its rows."""
        if block_rows < 1 or chunk_length < 2:
            raise ValueError("block_rows must be positive and chunk_length at least 2")
        self.path = str(Path(path).resolve())
        dataset = lance.dataset(self.path, version=version)
        if input_column not in dataset.schema.names:
            raise ValueError(f"{self.path} has no column {input_column!r}")
        self.version = dataset.version
        self.rows = dataset.count_rows()
        self.input_column = input_column
        self.block_rows = block_rows
        self.chunk_length = chunk_length
        self.add_bos = add_bos
        self.add_eos = add_eos

    def __len__(self) -> int:
        """Return the number of source rows."""
        return self.rows

    @property
    def blocks(self) -> int:
        """Return the number of row blocks."""
        return math.ceil(self.rows / self.block_rows)

    def fingerprint(self, tokenizer: TokenizersBackend) -> str:
        """Hash everything that decides the token stream, for the checkpoint contract."""
        settings = {
            "path": self.path,
            "version": self.version,
            "rows": self.rows,
            "input_column": self.input_column,
            "block_rows": self.block_rows,
            "chunk_length": self.chunk_length,
            "add_bos": self.add_bos,
            "add_eos": self.add_eos,
            "tokenizer": TokenChunkDataset.tokenizer_fingerprint(tokenizer),
        }
        return hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()

    def special_ids(self, tokenizer: TokenizersBackend) -> tuple[int | None, int | None]:
        """Return the BOS and EOS IDs to add, checking the tokenizer has them."""
        bos = tokenizer.bos_token_id if self.add_bos else None
        eos = tokenizer.eos_token_id if self.add_eos else None
        if self.add_bos and bos is None:
            raise ValueError("add_bos is set but the tokenizer has no BOS token")
        if self.add_eos and eos is None:
            raise ValueError("add_eos is set but the tokenizer has no EOS token")
        return bos, eos


class BlockTokenizer(Dataset[list[np.ndarray]]):
    """Map a block index to the token IDs of its documents; runs in DataLoader workers.

    Pickles without the open Lance handle, so spawned workers reopen the dataset.
    """

    def __init__(self, stream: TextStream, tokenizer: TokenizersBackend) -> None:
        """Store what workers need to read and tokenize blocks."""
        self.stream = stream
        self.tokenizer = tokenizer
        self.bos, self.eos = stream.special_ids(tokenizer)
        self._dataset: Any = None

    def __getstate__(self) -> dict[str, Any]:
        """Drop the Lance handle when sending this object to a worker."""
        return {**self.__dict__, "_dataset": None}

    def __len__(self) -> int:
        """Return the number of blocks."""
        return self.stream.blocks

    def __getitem__(self, index: int) -> list[np.ndarray]:
        """Read and tokenize one block, returning each document's int64 token IDs."""
        if self._dataset is None:
            self._dataset = lance.dataset(self.stream.path, version=self.stream.version)
        start = index * self.stream.block_rows
        stop = min(start + self.stream.block_rows, self.stream.rows)
        if not 0 <= start < stop:
            raise IndexError(f"Block {index} is outside the stream")
        texts = self._dataset.take(list(range(start, stop)), columns=[self.stream.input_column])
        encoded = self.tokenizer(
            texts[self.stream.input_column].to_pylist(),
            add_special_tokens=False,
            padding=False,
            truncation=False,
            verbose=False,
        )["input_ids"]
        prefix = [self.bos] if self.bos is not None else []
        suffix = [self.eos] if self.eos is not None else []
        return [np.asarray(prefix + ids + suffix, dtype=np.int64) for ids in encoded]


class RankBlocks(Sampler[int]):
    """Yield this rank's block indices in order, starting at a given global block."""

    def __init__(self, blocks: int, *, start: int, rank: int, world_size: int) -> None:
        """Validate that `start` belongs to `rank`."""
        super().__init__()
        if start % world_size != rank:
            raise ValueError(f"Block {start} does not belong to rank {rank} of {world_size}")
        self.blocks, self.start, self.world_size = blocks, start, world_size

    def __iter__(self) -> Iterator[int]:
        """Yield `start, start + world_size, ...` while blocks remain."""
        return iter(range(self.start, self.blocks, self.world_size))

    def __len__(self) -> int:
        """Return the number of blocks left to read."""
        return len(range(self.start, self.blocks, self.world_size))


class StreamPacker:
    """Cut tokenized blocks into rows of exactly `batch_tokens` targets, in `Batch` format.

    Rows match `PackedBatchCollator`: each piece of a document is its own packed
    document with positions from zero and at most `chunk_length - 1` targets. A document
    that does not fit the rest of a row continues in the next one, keeping its last
    token as context, so rows never hold padding. Only a final row at the end of the
    stream is padded. `position` is where the next row starts; read it between rows.

    Args:
        blocks (`Iterator[tuple[int, list[numpy.ndarray]]]`): This rank's `(block index,
            documents)` in order, starting at `start.block`.
        start (`StreamPosition`): Where this rank's first row starts.
        batch_tokens (`int`): Targets per row.
        chunk_length (`int`): Longest packed document, in token IDs.
        pad_token_id (`int`): Token ID for the padding in a final partial row.
        pin_memory (`bool`, *optional*, defaults to `False`): Pin rows for fast copies.
    """

    def __init__(
        self,
        blocks: Iterator[tuple[int, list[np.ndarray]]],
        *,
        start: StreamPosition,
        batch_tokens: int,
        chunk_length: int,
        pad_token_id: int,
        pin_memory: bool = False,
    ) -> None:
        """Store the stream and row settings; nothing is read until iteration."""
        if batch_tokens < 1 or chunk_length < 2:
            raise ValueError("batch_tokens must be positive and chunk_length at least 2")
        self.blocks = blocks
        self.position = start
        self.batch_tokens = batch_tokens
        self.max_targets = chunk_length - 1
        self.pad_token_id = pad_token_id
        self.pin_memory = pin_memory

    def __iter__(self) -> Iterator[Batch]:
        """Yield rows until the stream ends."""
        documents: list[np.ndarray] | None = None
        block, document, token = self.position.block, self.position.document, self.position.token
        size = self.batch_tokens
        while True:
            input_ids = np.full(size, self.pad_token_id, dtype=np.int64)
            labels = np.full(size, -100, dtype=np.int64)
            positions = np.zeros(size, dtype=np.int64)
            lengths: list[int] = []
            fill = 0
            while fill < size:
                if documents is None or document >= len(documents):
                    entry = next(self.blocks, None)
                    if entry is None:
                        break
                    received, documents = entry
                    if received != block:
                        # Moving on: this rank's next block, from its first document.
                        block, document, token = received, 0, 0
                    continue
                ids = documents[document]
                remaining = len(ids) - 1 - token
                if remaining <= 0:
                    document, token = document + 1, 0
                    continue
                take = min(remaining, self.max_targets, size - fill)
                piece = ids[token : token + take + 1]
                input_ids[fill : fill + take] = piece[:-1]
                labels[fill : fill + take] = piece[1:]
                positions[fill : fill + take] = np.arange(take)
                lengths.append(take)
                fill += take
                token += take
            if fill == 0:
                return
            if fill < size:
                # The stream ended mid-row: pad the tail as its own documents, without targets.
                for start in range(fill, size, self.max_targets):
                    stop = min(start + self.max_targets, size)
                    positions[start:stop] = np.arange(stop - start)
                    lengths.append(stop - start)
            self.position = StreamPosition(block, document, token)
            boundaries = np.zeros(len(lengths) + 1, dtype=np.int32)
            np.cumsum(lengths, out=boundaries[1:])
            row: Batch = {
                "input_ids": torch.from_numpy(input_ids)[None],
                "attention_mask": torch.ones((1, size), dtype=torch.bool),
                "labels": torch.from_numpy(labels)[None],
                "position_ids": torch.from_numpy(positions)[None],
                "cu_seqlens": torch.from_numpy(boundaries),
            }
            if self.pin_memory:
                row = {key: value.pin_memory() for key, value in row.items()}  # ty: ignore[invalid-assignment]
            yield row
            if fill < size:
                return


def stream_rows(
    stream: TextStream,
    tokenizer: TokenizersBackend,
    *,
    start: StreamPosition,
    batch_tokens: int,
    rank: int = 0,
    world_size: int = 1,
    num_workers: int = 4,
    prefetch_factor: int | None = 4,
    pin_memory: bool = False,
) -> StreamPacker:
    """Return a packer over this rank's blocks, tokenized ahead by `num_workers` workers.

    The DataLoader returns blocks in order whatever the worker count, so the rows are
    the same for any `num_workers`.
    """
    pad = tokenizer.pad_token_id
    if pad is None:
        raise ValueError("The tokenizer needs a padding token")
    loader = DataLoader(
        BlockTokenizer(stream, tokenizer),
        sampler=RankBlocks(stream.blocks, start=start.block, rank=rank, world_size=world_size),
        batch_size=None,
        num_workers=num_workers,
        prefetch_factor=prefetch_factor if num_workers else None,
        multiprocessing_context="spawn" if num_workers else None,
        persistent_workers=False,
    )
    indices = iter(RankBlocks(stream.blocks, start=start.block, rank=rank, world_size=world_size))
    blocks = zip(indices, loader, strict=True)
    return StreamPacker(
        blocks,
        start=start,
        batch_tokens=batch_tokens,
        chunk_length=stream.chunk_length,
        pad_token_id=pad,
        pin_memory=pin_memory,
    )


def first_position(rank: int) -> StreamPosition:
    """Return where rank `rank` starts a fresh run: its first block."""
    return StreamPosition(block=rank)
