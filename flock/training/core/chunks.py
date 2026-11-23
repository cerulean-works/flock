import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Self

import lance
import numpy as np
import pyarrow as pa
from lance.torch.data import SafeLanceDataset
from torch.utils.data import Dataset
from transformers import TokenizersBackend


class TokenChunkDataset(Dataset):
    """Index cached document tokens as overlapping, independently sampled chunks.

    Args:
        cache_dir (`str` or `Path`): Local cache created by `prepare`.
        chunk_length (`int`, *optional*, defaults to `4096`): Token IDs per chunk, including one context token.
        packing (`bool`, *optional*, defaults to `False`): Pack chunks into rows of `chunk_length - 1` inputs with best-fit decreasing. Each item is then one row, `{"_flock_chunks": [chunk, ...]}`.

    Each chunk inherits its source row's metadata. `_flock_document_index` and `_flock_chunk_index` identify its position. Documents with fewer than two tokens have no items. Cache version 1 is pinned; preparing an existing path is refused.
    """

    def __init__(
        self, *, cache_dir: str | Path, chunk_length: int = 4096, packing: bool = False
    ) -> None:
        if chunk_length < 2:
            raise ValueError("chunk_length must be at least 2")
        self.chunk_length = chunk_length
        self.packing = packing
        self.cache_dir = str(Path(cache_dir).resolve())
        cached = lance.dataset(self.cache_dir, version=1)
        metadata = cached.schema.metadata or {}
        self.manifest = json.loads(metadata[b"flock_training_tokens"])
        self.dataset = SafeLanceDataset(self.cache_dir, dataset_options={"version": 1})
        self.lengths = cached.to_table(columns=["_flock_token_count"])[
            "_flock_token_count"
        ].to_numpy()
        # Each chunk contributes at most chunk_length - 1 new next-token targets.
        counts = (np.maximum(self.lengths - 1, 0) + chunk_length - 2) // (chunk_length - 1)
        self.ends = np.cumsum(counts, dtype=np.int64)
        # Unpacked caches keep their earlier fingerprint, so their checkpoints still resume.
        identity: list[Any] = [self.manifest, chunk_length]
        if packing:
            identity.append("bfd")
            self.row_offsets, self.row_chunks = _best_fit_decreasing(
                self._chunk_inputs(), capacity=chunk_length - 1
            )
        self.fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def tokenizer_fingerprint(tokenizer: TokenizersBackend) -> str:
        """Hash tokenization rules independently of mutable padding and truncation settings."""
        backend = json.loads(tokenizer.backend_tokenizer.to_str())
        backend.pop("padding", None)
        backend.pop("truncation", None)
        rules = {"backend": backend, "split_special_tokens": tokenizer.split_special_tokens}
        return hashlib.sha256(json.dumps(rules, sort_keys=True).encode()).hexdigest()

    @classmethod
    def prepare(
        cls,
        *,
        dataset: SafeLanceDataset,
        tokenizer: TokenizersBackend,
        input_column: str,
        cache_dir: str | Path,
        chunk_length: int = 4096,
    ) -> Self:
        """Tokenize a pinned source once into a new local cache, without truncation.

        Args:
            dataset (`SafeLanceDataset`): Source documents after document-level exclusions and deduplication.
            tokenizer (`TokenizersBackend`): Tokenizer whose special-token policy is retained once per document.
            input_column (`str`): Source text column.
            cache_dir (`str` or `Path`): New local directory; never a hub destination.
            chunk_length (`int`, *optional*, defaults to `4096`): Token IDs per chunk.

        Reopen the cache with the constructor to resume or use another context length. The source columns are preserved; names beginning with `_flock_` are reserved. Do not modify the cache after preparing it.
        """
        if chunk_length < 2:
            raise ValueError("chunk_length must be at least 2")
        destination = Path(cache_dir).resolve()
        if destination.exists():
            raise FileExistsError(destination)
        source = lance.dataset(dataset.uri, **dataset.dataset_options)
        if any(name.startswith("_flock_") for name in source.schema.names):
            raise ValueError("Source columns beginning with _flock_ are reserved")
        if input_column not in source.schema.names:
            raise KeyError(input_column)
        manifest = {
            "source_uri": str(source.uri),
            "source_version": source.version,
            "input_column": input_column,
            "tokenizer": cls.tokenizer_fingerprint(tokenizer),
        }
        schema = source.schema.append(pa.field("_flock_chunk_ids", pa.list_(pa.int64())))
        schema = schema.append(pa.field("_flock_token_count", pa.int64()))
        metadata = dict(schema.metadata or {})
        metadata[b"flock_training_tokens"] = json.dumps(manifest, sort_keys=True).encode()
        schema = schema.with_metadata(metadata)

        def batches() -> Iterator[pa.RecordBatch]:
            for batch in source.to_batches(batch_size=128):
                texts = batch.column(input_column).to_pylist()
                ids = tokenizer(texts, padding=False, truncation=False)["input_ids"]
                arrays = [*batch.columns, pa.array(ids, type=pa.list_(pa.int64()))]
                arrays.append(pa.array([len(row) for row in ids], type=pa.int64()))
                yield pa.RecordBatch.from_arrays(arrays, schema=schema)

        lance.write_dataset(batches(), str(destination), schema=schema, mode="create")
        return cls(cache_dir=destination, chunk_length=chunk_length)

    def _chunk_count(self) -> int:
        """Return the number of chunks with at least one prediction target."""
        return int(self.ends[-1]) if len(self.ends) else 0

    def _chunk_inputs(self) -> np.ndarray:
        """Return each chunk's input count, one less than its token IDs, in chunk order.

        Every chunk of a document holds `chunk_length - 1` inputs except the last,
        which holds the rest.
        """
        inputs = np.full(self._chunk_count(), self.chunk_length - 1, dtype=np.int64)
        targets = np.maximum(self.lengths.astype(np.int64) - 1, 0)
        has_chunks = targets > 0
        last = self.ends[has_chunks] - 1
        first = np.concatenate(([0], self.ends[:-1]))[has_chunks]
        inputs[last] = targets[has_chunks] - (last - first) * (self.chunk_length - 1)
        return inputs

    def __len__(self) -> int:
        """Return the number of items: chunks, or packed rows with `packing`."""
        return len(self.row_offsets) - 1 if self.packing else self._chunk_count()

    def __getitem__(self, index: int) -> dict[str, Any]:
        """Return one chunk with its unchanged document metadata, or one packed row."""
        return self.__getitems__([index])[0]

    def __getitems__(self, indices: list[int]) -> list[dict[str, Any]]:
        """Fetch each requested document once and slice its requested chunks.

        With `packing`, return each requested row as `{"_flock_chunks": [chunk, ...]}`.
        """
        if any(index < 0 or index >= len(self) for index in indices):
            raise IndexError("Index outside dataset")
        if not self.packing:
            return self._chunks(indices)
        bounds = [(int(self.row_offsets[row]), int(self.row_offsets[row + 1])) for row in indices]
        chunks = iter(self._chunks([int(c) for a, b in bounds for c in self.row_chunks[a:b]]))
        return [{"_flock_chunks": [next(chunks) for _ in range(b - a)]} for a, b in bounds]

    def _chunks(self, indices: list[int]) -> list[dict[str, Any]]:
        """Fetch each requested document once and slice its requested chunks."""
        documents = np.searchsorted(self.ends, indices, side="right").tolist()
        unique = list(dict.fromkeys(documents))
        rows = dict(zip(unique, self.dataset.__getitems__(unique))) if unique else {}
        result = []
        for index, document in zip(indices, documents):
            chunk_index = index - (int(self.ends[document - 1]) if document else 0)
            # Reuse the preceding chunk's final token as context for the next target.
            start = chunk_index * (self.chunk_length - 1)
            row = dict(rows[document])
            row["_flock_chunk_ids"] = row["_flock_chunk_ids"][start : start + self.chunk_length]
            row["_flock_document_index"] = document
            row["_flock_chunk_index"] = chunk_index
            result.append(row)
        return result


class _OpenSpaces:
    """Count open rows by remaining space, and find the smallest space that fits.

    A Fenwick tree over spaces `1..capacity` finds it in `O(log capacity)`.
    """

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self.tree = [0] * (capacity + 1)
        self.top = 1 << capacity.bit_length()

    def add(self, space: int, delta: int) -> None:
        while space <= self.capacity:
            self.tree[space] += delta
            space += space & -space

    def _prefix(self, space: int) -> int:
        total = 0
        while space > 0:
            total += self.tree[space]
            space -= space & -space
        return total

    def smallest_at_least(self, size: int) -> int | None:
        """Return the smallest open space of at least `size`, or `None`."""
        # Find the first space whose prefix count exceeds the count below `size`.
        rank = self._prefix(size - 1) + 1
        position, step = 0, self.top
        while step:
            nxt = position + step
            if nxt <= self.capacity and self.tree[nxt] < rank:
                position = nxt
                rank -= self.tree[nxt]
            step >>= 1
        return position + 1 if position < self.capacity else None


def _best_fit_decreasing(sizes: np.ndarray, *, capacity: int) -> tuple[np.ndarray, np.ndarray]:
    """Pack items into rows of `capacity` with best-fit decreasing, deterministically.

    Items go in decreasing size, ties by index, each into the open row with the
    least space that still fits it, or into a new row. This follows OLMo-core's
    OBFD and TRL's `bfd`: no item is split across rows, and rows have little padding.

    Returns:
        `tuple[numpy.ndarray, numpy.ndarray]`: Row offsets, starting at 0, into the
        item indices of every row, concatenated in row order.
    """
    rows: list[list[int]] = []
    spaces = _OpenSpaces(capacity)
    # Rows that are still open, by their remaining space.
    open_rows: list[list[int]] = [[] for _ in range(capacity + 1)]
    for item in np.argsort(-sizes, kind="stable").tolist():
        size = int(sizes[item])
        space = spaces.smallest_at_least(size) if size < capacity else None
        if space is None:
            row, remaining = len(rows), capacity - size
            rows.append([item])
        else:
            row, remaining = open_rows[space].pop(), space - size
            spaces.add(space, -1)
            rows[row].append(item)
        if remaining:
            open_rows[remaining].append(row)
            spaces.add(remaining, 1)
    offsets = np.zeros(len(rows) + 1, dtype=np.int64)
    np.cumsum([len(row) for row in rows], out=offsets[1:])
    items = np.fromiter((item for row in rows for item in row), dtype=np.int64, count=len(sizes))
    return offsets, items
