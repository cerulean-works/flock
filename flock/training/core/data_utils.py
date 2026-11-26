import copy
import logging
from collections import deque
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, NotRequired

import torch
from lance.torch.data import SafeLanceDataset
from torch.utils.data import Sampler
from transformers import TokenizersBackend
from typing_extensions import TypedDict

from flock.hub import s3


class Batch(TypedDict, closed=True):
    """Describe aligned `[batch, sequence]` tensors, plus a packed batch's boundaries.

    Attributes:
        input_ids (`torch.Tensor`): Integer token IDs, excluding the final token.
        attention_mask (`torch.Tensor`): Boolean mask; `True` marks a real input token.
        labels (`torch.Tensor`): Next-token IDs already shifted relative to inputs.
            A target is `-100` if either its input or target position is padding.
        position_ids (`torch.Tensor`): Each input's position in its sequence. They
            count from each row's first real token, so left padding does not shift
            them, and restart at zero in each document of a packed batch.
        cu_seq_lens (`torch.Tensor`, *optional*): int32 document boundaries in the
            flattened `[batch * sequence]` axis, from 0 to `batch * sequence`. They
            cover every position, and each row's padding is one more document.
            Present only in packed batches.
    """

    input_ids: torch.Tensor
    attention_mask: torch.Tensor
    labels: torch.Tensor
    position_ids: torch.Tensor
    cu_seq_lens: NotRequired[torch.Tensor]


class LanceBatchCollator:
    """Tokenize text rows into padded inputs and already shifted next-token targets.

    This module-level class is pickleable for spawned DataLoader workers.

    Args:
        tokenizer (`TokenizersBackend`): Tokenizer with padding and length configured.
        input_column (`str`): Text field in each dataset row.
        token_chunks (`bool`, *optional*, defaults to `False`): Rows are chunks from a
            `TokenChunkDataset` instead of text.
        packed_tokens (`int`, *optional*): Pack each batch of text rows into one row of
            this many inputs, as `TokenBudgetBatchSampler` plans them. Each document is
            truncated to the tokenizer's `model_max_length`, not split.
    """

    def __init__(
        self,
        tokenizer: TokenizersBackend,
        input_column: str,
        *,
        token_chunks: bool = False,
        packed_tokens: int | None = None,
    ) -> None:
        """Store the tokenizer and source text column for worker-side collation."""
        if token_chunks and packed_tokens is not None:
            raise ValueError("Token chunks are packed by TokenChunkDataset, not the collator")
        self.tokenizer = tokenizer
        self.input_column = input_column
        self.token_chunks = token_chunks
        self.packed_tokens = packed_tokens

    def collate_fn(self, batch: list[dict[str, Any]]) -> Batch:
        """Pad and truncate rows, then align inputs with next-token labels.

        Args:
            batch (`list[dict[str, Any]]`): Rows containing the configured text field,
                `TokenChunkDataset` chunks, or packed rows holding `_flock_chunks`.

        Returns:
            `Batch`: Tensors of shape `[rows, padded_length - 1]`. Invalid targets
            use `-100`, including padding-to-text transitions with left padding.
            Packed rows, which hold `_flock_chunks`, also give `cu_seq_lens` and are
            padded to the row with the most inputs. With `packed_tokens`, the batch's
            documents become one packed row of exactly `packed_tokens` inputs.

        Raises:
            `KeyError`: If a row lacks the configured input column.
            `ValueError`: If packed documents exceed `packed_tokens` inputs.
        """
        if self.packed_tokens is not None:
            ids = tokenize_documents(self.tokenizer, [row[self.input_column] for row in batch])
            # A one-token document has no target, so it adds no positions.
            chunks = [{"_flock_chunk_ids": document} for document in ids if len(document) > 1]
            return self._collate_packed([{"_flock_chunks": chunks}], width=self.packed_tokens)
        if not batch:
            # Run attention on a valid input, but contribute no targets or loss.
            return {
                "input_ids": torch.full((1, 1), self.tokenizer.pad_token_id, dtype=torch.long),
                "attention_mask": torch.ones((1, 1), dtype=torch.bool),
                "labels": torch.full((1, 1), -100, dtype=torch.long),
                "position_ids": torch.zeros((1, 1), dtype=torch.long),
            }
        # Packed rows hold their chunks under `_flock_chunks`.
        if "_flock_chunks" in batch[0]:
            return self._collate_packed(batch)

        # It is up to the model to set max length, padding side and padding token
        if self.token_chunks:
            tokenized = self.tokenizer.pad(
                {"input_ids": [row["_flock_chunk_ids"] for row in batch]},
                return_tensors="pt",
                padding=True,
                return_attention_mask=True,
            )
        else:
            tokenized = self.tokenizer(
                [row[self.input_column] for row in batch],
                return_tensors="pt",
                padding=True,
                truncation=True,
            )
        input_ids = tokenized["input_ids"]
        mask = tokenized["attention_mask"].bool()

        labels = input_ids[:, 1:].clone()
        # Both the input and the target is real
        valid = mask[:, :-1] & mask[:, 1:]
        # PyTorch CE loss ignores labels of target -100
        labels.masked_fill_(~valid, -100)
        input_mask = mask[:, :-1]

        return {
            "input_ids": input_ids[:, :-1],
            "attention_mask": input_mask,
            "labels": labels,
            "position_ids": (input_mask.long().cumsum(-1) - 1).clamp_min(0),
        }

    def _collate_packed(self, batch: list[dict[str, Any]], *, width: int | None = None) -> Batch:
        """Lay out each packed row's chunks as documents, padded to the longest row.

        Each chunk of `L` token IDs becomes one document of `L - 1` inputs with its own
        shifted targets, so no target crosses a document boundary. Positions restart at
        zero in each document. A row's unused tail is padding: one more document, with
        no targets and a `False` mask. `cu_seq_lens` covers every position of the
        flattened batch. A given `width` fixes the padded row length instead.
        """
        chunks = [
            [
                torch.as_tensor(chunk["_flock_chunk_ids"], dtype=torch.long)
                for chunk in item["_flock_chunks"]
            ]
            for item in batch
        ]
        rows = len(chunks)
        longest = max(sum(len(ids) - 1 for ids in row) for row in chunks)
        if width is None:
            width = longest
        elif longest > width:
            raise ValueError(f"Packed documents hold {longest} inputs, over the {width} budget")

        input_ids = torch.full((rows, width), self.tokenizer.pad_token_id, dtype=torch.long)
        labels = torch.full((rows, width), -100, dtype=torch.long)
        attention_mask = torch.zeros((rows, width), dtype=torch.bool)
        position_ids = torch.zeros((rows, width), dtype=torch.long)
        lengths: list[int] = []

        for row, row_chunks in enumerate(chunks):
            start = 0
            for ids in row_chunks:
                stop = start + len(ids) - 1
                input_ids[row, start:stop] = ids[:-1]
                labels[row, start:stop] = ids[1:]
                attention_mask[row, start:stop] = True
                position_ids[row, start:stop] = torch.arange(stop - start)
                lengths.append(stop - start)
                start = stop
            if start < width:
                position_ids[row, start:] = torch.arange(width - start)
                lengths.append(width - start)

        cu_seq_lens = torch.zeros(len(lengths) + 1, dtype=torch.int32)
        cu_seq_lens[1:] = torch.tensor(lengths).cumsum(0)
        return {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "labels": labels,
            "position_ids": position_ids,
            "cu_seq_lens": cu_seq_lens,
        }


class ResumableBatchDataSampler(Sampler[list[int]]):
    """Yield deterministic index batches while skipping a saved batch prefix.

    Each iteration reconstructs the same permutation with a private generator.
    `len(sampler)` reports the pass length, capped at `max_batches`, including skipped batches.

    Args:
        batch_size (`int`): Positive rows per batch.
        dataset_size (`int`): Nonnegative number of rows.
        shuffle (`bool`, *optional*, defaults to `True`): Shuffle row indices.
        seed (`int`, *optional*, defaults to `42`): Private permutation seed.
        batch_cursor (`int`, *optional*, defaults to `0`): Completed global batches to skip.
        rank (`int`, *optional*, defaults to `0`): Worker rank in `[0, world_size)`.
        world_size (`int`, *optional*, defaults to `1`): Total number of workers
        max_batches (`int`, *optional*): Stop after this many batches of the pass.
        drop_last (`bool`, *optional*, defaults to `False`): Drop the final partial batch.
    """

    def __init__(
        self,
        batch_size: int,
        dataset_size: int,
        shuffle: bool = True,
        seed: int = 42,
        batch_cursor: int = 0,
        rank: int = 0,
        world_size: int = 1,
        yield_empty_last_batch: bool | None = None,
        max_batches: int | None = None,
        drop_last: bool = False,
    ) -> None:
        """Store batching settings without consuming the global RNG stream.

        Args:
            batch_size (`int`): Positive rows per batch.
            dataset_size (`int`): Nonnegative number of rows.
            shuffle (`bool`, *optional*, defaults to `True`): Shuffle row indices.
            seed (`int`, *optional*, defaults to `42`): Private permutation seed.
            batch_cursor (`int`, *optional*, defaults to `0`): Completed global batches to skip.
            rank (`int`, *optional*, defaults to `0`): Worker rank in `[0, world_size)`.
            world_size (`int`, *optional*, defaults to `1`): Total number of workers
            yield_empty_last_batch (`bool`, *optional*, defaults to `True`):
                Yield an empty batch if other ranks still have data to yield.
            max_batches (`int`, *optional*): Cap the number of global updates.
            drop_last (`bool`, *optional*, defaults to `False`): Drop the final partial global batch.
        """
        super().__init__()
        self.dataset_size = dataset_size
        self.batch_size = batch_size
        self.shuffle = shuffle
        self.seed = seed
        self.batch_cursor = batch_cursor
        self.rank = rank
        self.world_size = world_size
        self.yield_empty_last_batch = (
            yield_empty_last_batch if yield_empty_last_batch is not None else True
        )

        self.logger = logging.getLogger(__name__)

        if rank < 0 or world_size < 1 or rank >= world_size:
            raise ValueError(
                f"Invalid rank {rank} for world size {world_size}. Rank must be in [0, world_size)."
            )

        if yield_empty_last_batch is not None and world_size == 1:
            self.logger.warning(
                "yield_empty_last_batch is set but world_size is 1. This setting has no effect in single-worker mode."
            )

        self.max_batches = max_batches
        self.drop_last = drop_last

    def __len__(self) -> int:
        """Return the capped per-rank batch count before applying the cursor."""
        full_batches, remainder = divmod(self.dataset_size, self.batch_size * self.world_size)
        batches = full_batches + int(
            not self.drop_last
            and remainder > 0
            and (self.yield_empty_last_batch or self.batch_size * self.rank < remainder)
        )
        return batches if self.max_batches is None else min(batches, self.max_batches)

    def __iter__(self) -> Iterator[list[int]]:
        """Yield row-index lists after the configured number of skipped batches."""
        generator = torch.Generator()
        generator.manual_seed(self.seed)

        indices = (
            torch.randperm(self.dataset_size, generator=generator)
            if self.shuffle
            else range(self.dataset_size)
        )

        for batch_idx in range(self.batch_cursor, len(self)):
            start = self.batch_size * (batch_idx * self.world_size + self.rank)
            end = start + self.batch_size
            batch = indices[start:end]
            yield batch.tolist() if isinstance(batch, torch.Tensor) else list(batch)


def tokenize_documents(tokenizer: TokenizersBackend, texts: list[str]) -> list[list[int]]:
    """Tokenize documents without padding, truncated to the tokenizer's `model_max_length`.

    Special tokens follow the tokenizer, as in `LanceBatchCollator`. The packing sampler
    and collator share this, so a document's planned length is its collated length.
    """
    if not texts:
        return []
    return tokenizer(texts, truncation=True, padding=False)["input_ids"]


class DocumentLengths:
    """Measure documents' packed input counts: each truncated token count less one.

    The sampler calls it from a background thread, so it tokenizes with a private copy
    of the tokenizer: a call on the shared one could change its truncation meanwhile.

    Args:
        dataset (`SafeLanceDataset`): Indexable text rows.
        tokenizer (`TokenizersBackend`): Tokenizer whose `model_max_length` truncates.
        input_column (`str`): Text field in each row.
    """

    def __init__(self, dataset: Any, tokenizer: TokenizersBackend, input_column: str) -> None:
        """Keep the dataset and a private tokenizer copy."""
        self.dataset = dataset
        self.tokenizer = copy.deepcopy(tokenizer)
        self.input_column = input_column

    def __call__(self, indices: list[int]) -> list[int]:
        """Return the input count of each indexed document, in order."""
        if not indices:
            return []
        getitems = getattr(self.dataset, "__getitems__", None)
        rows = getitems(indices) if getitems is not None else [self.dataset[i] for i in indices]
        ids = tokenize_documents(self.tokenizer, [row[self.input_column] for row in rows])
        return [max(len(document) - 1, 0) for document in ids]


class TokenBudgetBatchSampler(Sampler[list[int]]):
    """Pack each rank's documents, in a seeded order, into batches under a token budget.

    Rank `r` reads positions `r, r + world_size, ...` of the seeded permutation that
    `ResumableBatchDataSampler` uses. A batch takes the rank's next documents in order
    while their input counts fit `max_tokens`; the first that does not fit starts the
    next batch, so no document is skipped or repeated. Once a rank's documents run out,
    it yields empty batches, so every rank yields the same number of batches.

    Lengths are measured ahead, in blocks, on a background thread. Since a loader runs
    ahead of training, the sampler keeps each batch's resulting document cursor until
    `document_cursor_after` reads it. Resume with the saved batch and document cursors.

    Args:
        dataset_size (`int`): Nonnegative number of rows.
        lengths (`Callable[[list[int]], list[int]]`): Input count of each document, as
            `DocumentLengths` measures it; each must fit `max_tokens`.
        max_tokens (`int`): Positive input budget of one batch.
        max_batches (`int`): Batches per rank, including skipped ones.
        seed (`int`, *optional*, defaults to `42`): Private permutation seed.
        rank (`int`, *optional*, defaults to `0`): This rank, in `[0, world_size)`.
        world_size (`int`, *optional*, defaults to `1`): Number of ranks.
        batch_cursor (`int`, *optional*, defaults to `0`): Completed batches to skip.
        document_cursor (`int`, *optional*, defaults to `0`): Documents of this rank
            those batches consumed.
        block_size (`int`, *optional*, defaults to `1024`): Documents measured at once.
    """

    def __init__(
        self,
        *,
        dataset_size: int,
        lengths: Callable[[list[int]], list[int]],
        max_tokens: int,
        max_batches: int,
        seed: int = 42,
        rank: int = 0,
        world_size: int = 1,
        batch_cursor: int = 0,
        document_cursor: int = 0,
        block_size: int = 1024,
    ) -> None:
        """Validate and store packing settings without consuming the global RNG stream."""
        super().__init__()
        if rank < 0 or world_size < 1 or rank >= world_size:
            raise ValueError(f"Invalid rank {rank} for world size {world_size}")
        if max_tokens <= 0 or block_size <= 0 or max_batches < 0:
            raise ValueError("max_tokens and block_size must be positive")
        if not 0 <= batch_cursor <= max_batches or document_cursor < 0:
            raise ValueError("Cursors must be nonnegative and within max_batches")
        self.dataset_size = dataset_size
        self.lengths = lengths
        self.max_tokens = max_tokens
        self.max_batches = max_batches
        self.seed = seed
        self.rank = rank
        self.world_size = world_size
        self.batch_cursor = batch_cursor
        self.document_cursor = document_cursor
        self.block_size = block_size
        self._cursors: dict[int, int] = {}

    def __len__(self) -> int:
        """Return the batch count per rank, including skipped batches."""
        return self.max_batches

    def document_cursor_after(self, batch: int) -> int:
        """Return this rank's consumed documents after `batch` batches, once yielded.

        Forget earlier batches' cursors, which training has passed.

        Raises:
            `KeyError`: If the sampler has not yielded that batch in this iteration.
        """
        cursor = self._cursors.pop(batch)
        for earlier in [key for key in self._cursors if key < batch]:
            del self._cursors[earlier]
        return cursor

    def __iter__(self) -> Iterator[list[int]]:
        """Yield the packed batches after the configured cursors."""
        generator = torch.Generator()
        generator.manual_seed(self.seed)
        order = torch.randperm(self.dataset_size, generator=generator)[self.rank :: self.world_size]
        self._cursors = {}
        cursor = self.document_cursor
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="flock-packing")
        pending: deque[tuple[list[int], Future[list[int]]]] = deque()
        next_block = cursor

        def measure_ahead() -> None:
            # Keep two blocks in flight, so the next is ready when this one runs out.
            nonlocal next_block
            while len(pending) < 2 and next_block < len(order):
                indices = order[next_block : next_block + self.block_size].tolist()
                pending.append((indices, executor.submit(self.lengths, indices)))
                next_block += len(indices)

        documents: list[int] = []
        sizes: list[int] = []
        position = 0
        try:
            for batch_number in range(self.batch_cursor, self.max_batches):
                batch: list[int] = []
                used = 0
                while True:
                    if position == len(documents):
                        measure_ahead()
                        if not pending:
                            break
                        documents, future = pending.popleft()
                        sizes, position = future.result(), 0
                        measure_ahead()
                    size = sizes[position]
                    if size > self.max_tokens:
                        raise ValueError(
                            f"Document {documents[position]} has {size} inputs, "
                            f"over the {self.max_tokens} budget"
                        )
                    if used + size > self.max_tokens:
                        break
                    batch.append(documents[position])
                    used += size
                    position += 1
                    cursor += 1
                self._cursors[batch_number + 1] = cursor
                yield batch
        finally:
            executor.shutdown(wait=False, cancel_futures=True)


@dataclass(frozen=True, kw_only=True)
class DatasetConfig:
    """Select a Lance dataset and pin the version opened by training.

    Args:
        uri (`str`): Local path or Lance object-storage URI.
        version (`str` or `int`): Lance version number or tag.
    """

    uri: str
    version: str | int = field(doc="Flock hub's version number or tag")


def get_dataset(config: DatasetConfig) -> SafeLanceDataset:
    """Open a pinned Lance dataset with renewable credentials for S3 storage.

    Args:
        config ([`training.DatasetConfig`]): Dataset location and required version or tag.

    Returns:
        `SafeLanceDataset`: Indexable dataset suitable for spawned loader workers.

    For `s3://`, `s3a://` and `s3n://` URIs, configure read-only credentials for
    `cerulean-labs` using `flock.hub.s3.get_process` before opening the dataset.
    This changes the process environment inherited by workers. Local paths
    leave credential configuration untouched.
    """
    if config.uri.startswith(("s3://", "s3a://", "s3n://")):
        s3.get_process(org="cerulean-labs", read_only=True)
    # Spawned workers inherit the native credential-process configuration.
    return SafeLanceDataset(
        uri=config.uri,
        dataset_options={"version": config.version},
    )
