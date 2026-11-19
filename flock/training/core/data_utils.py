import logging
import math
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, NotRequired, Self

import numpy as np
import torch
from lance.torch.data import SafeLanceDataset
from torch.utils.data import Sampler
from transformers import TokenizersBackend
from typing_extensions import TypedDict

from flock.hub import s3


class Batch(TypedDict, closed=True):
    """Describe aligned tensors with shape `[batch, sequence]`.

    Attributes:
        input_ids (`torch.Tensor`): Integer token IDs, excluding the final token.
        attention_mask (`torch.Tensor`): Boolean mask; `True` marks a real input token.
        labels (`torch.Tensor`): Next-token IDs already shifted relative to inputs.
            A target is `-100` if either its input or target position is padding.
        position_ids (`torch.Tensor`, *optional*): Positions that restart at zero
            in each packed document. Present only in packed batches.
        cu_seqlens (`torch.Tensor`, *optional*): int32 document boundaries over the
            flattened `[batch * sequence]` tokens. Present only in packed batches.
    """

    input_ids: torch.Tensor
    attention_mask: torch.Tensor
    labels: torch.Tensor
    position_ids: NotRequired[torch.Tensor]
    cu_seqlens: NotRequired[torch.Tensor]


class LanceBatchCollator:
    """Tokenize text rows into padded inputs and already shifted next-token targets.

    This module-level class is pickleable for spawned DataLoader workers.

    Args:
        tokenizer (`TokenizersBackend`): Tokenizer with padding and length configured.
        input_column (`str`): Text field in each dataset row.
    """

    def __init__(
        self, tokenizer: TokenizersBackend, input_column: str, *, token_chunks: bool = False
    ) -> None:
        """Store the tokenizer and source text column for worker-side collation."""
        self.tokenizer = tokenizer
        self.input_column = input_column
        self.token_chunks = token_chunks

    def collate_fn(self, batch: list[dict[str, Any]]) -> Batch:
        """Pad and truncate rows, then align inputs with next-token labels.

        Args:
            batch (`list[dict[str, Any]]`): Rows containing the configured text field.

        Returns:
            `Batch`: Tensors of shape `[rows, padded_length - 1]`. Invalid targets
            use `-100`, including padding-to-text transitions with left padding.

        Raises:
            `KeyError`: If a row lacks the configured input column.
        """
        if not batch:
            # Run attention on a valid input, but contribute no targets or loss.
            return {
                "input_ids": torch.full((1, 1), self.tokenizer.pad_token_id, dtype=torch.long),
                "attention_mask": torch.ones((1, 1), dtype=torch.bool),
                "labels": torch.full((1, 1), -100, dtype=torch.long),
            }

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

        return {
            "input_ids": input_ids[:, :-1],
            "attention_mask": mask[:, :-1],
            "labels": labels,
        }


class PackedBatchCollator:
    """Pack cached token chunks end to end into one row of exactly `batch_tokens` inputs.

    Each chunk of `L` token IDs contributes `L - 1` inputs and targets and becomes
    its own document: positions restart at zero and attention stays inside it.
    The unused tail is padding, split into documents no longer than
    `max_document_tokens` so kernel sizing stays bounded. Padding has no targets.
    The fixed row length keeps tensor shapes stable across steps.

    This module-level class is pickleable for spawned DataLoader workers.

    Args:
        pad_token_id (`int`): Token ID placed in the padding tail.
        batch_tokens (`int`): Input positions per row.
        max_document_tokens (`int`): Longest document, in inputs; the chunk length minus one.
    """

    def __init__(self, *, pad_token_id: int, batch_tokens: int, max_document_tokens: int) -> None:
        """Store the row size and padding settings for worker-side collation."""
        if batch_tokens < max_document_tokens or max_document_tokens < 1:
            raise ValueError("batch_tokens must hold at least one full chunk")
        self.pad_token_id = pad_token_id
        self.batch_tokens = batch_tokens
        self.max_document_tokens = max_document_tokens

    def collate_fn(self, batch: list[dict[str, Any]]) -> Batch:
        """Concatenate chunks, then fill the row with padding documents.

        Args:
            batch (`list[dict[str, Any]]`): Chunk rows from `TokenChunkDataset`. An
                empty list produces a row of padding with no targets.

        Returns:
            `Batch`: `[1, batch_tokens]` tensors with `position_ids` and `cu_seqlens`.

        Raises:
            `ValueError`: If the chunks hold more than `batch_tokens` inputs.
        """
        input_ids = torch.full((self.batch_tokens,), self.pad_token_id, dtype=torch.long)
        labels = torch.full((self.batch_tokens,), -100, dtype=torch.long)
        position_ids = torch.zeros(self.batch_tokens, dtype=torch.long)
        lengths: list[int] = []
        start = 0
        for row in batch:
            ids = torch.as_tensor(row["_flock_chunk_ids"], dtype=torch.long)
            length = ids.numel() - 1
            if length < 1:
                continue
            if length > self.max_document_tokens or start + length > self.batch_tokens:
                raise ValueError("Packed chunks exceed batch_tokens or the document length")
            input_ids[start : start + length] = ids[:-1]
            labels[start : start + length] = ids[1:]
            position_ids[start : start + length] = torch.arange(length)
            lengths.append(length)
            start += length
        while start < self.batch_tokens:
            length = min(self.max_document_tokens, self.batch_tokens - start)
            position_ids[start : start + length] = torch.arange(length)
            lengths.append(length)
            start += length

        cu_seqlens = torch.zeros(len(lengths) + 1, dtype=torch.int32)
        cu_seqlens[1:] = torch.tensor(lengths).cumsum(0)
        return {
            "input_ids": input_ids[None],
            "attention_mask": torch.ones((1, self.batch_tokens), dtype=torch.bool),
            "labels": labels[None],
            "position_ids": position_ids[None],
            "cu_seqlens": cu_seqlens,
        }


@dataclass(frozen=True)
class TokenBatchPlan:
    """Greedily pack a seeded item order into batches of at most `batch_tokens` targets.

    Batch `k` holds `order[bounds[k]:bounds[k + 1]]`. Items keep their shuffled
    order; a batch closes when the next item would exceed the budget. The same
    lengths, budget and seed always give the same plan.

    Attributes:
        order (`numpy.ndarray`): Item indices in pass order.
        bounds (`numpy.ndarray`): Start offsets into `order`, ending with its length.
        tokens (`numpy.ndarray`): Targets in each batch.
        batch_tokens (`int`): Per-batch target budget.
    """

    order: np.ndarray
    bounds: np.ndarray
    tokens: np.ndarray
    batch_tokens: int

    @classmethod
    def build(
        cls, lengths: np.ndarray, batch_tokens: int, *, seed: int = 42, shuffle: bool = True
    ) -> Self:
        """Plan one pass over items whose target counts are `lengths`.

        Args:
            lengths (`numpy.ndarray`): Positive targets per item, such as
                `TokenChunkDataset.target_counts()`.
            batch_tokens (`int`): Positive budget; every item must fit on its own.
            seed (`int`, *optional*, defaults to `42`): Private permutation seed.
            shuffle (`bool`, *optional*, defaults to `True`): Shuffle the items first.

        Raises:
            `ValueError`: If the budget is not positive or an item exceeds it.
        """
        lengths = np.asarray(lengths, dtype=np.int64)
        if batch_tokens <= 0:
            raise ValueError("batch_tokens must be positive")
        if len(lengths) and (lengths.max() > batch_tokens or lengths.min() < 1):
            raise ValueError("Every item needs between 1 and batch_tokens targets")
        generator = torch.Generator().manual_seed(seed)
        order = (
            torch.randperm(len(lengths), generator=generator).numpy()
            if shuffle
            else np.arange(len(lengths))
        )
        cumulative = np.concatenate(([0], np.cumsum(lengths[order])))
        bounds = [0]
        while bounds[-1] < len(order):
            # The furthest end whose running total stays within the budget.
            limit = cumulative[bounds[-1]] + batch_tokens
            bounds.append(int(np.searchsorted(cumulative, limit, side="right")) - 1)
        bounds_array = np.asarray(bounds, dtype=np.int64)
        tokens = cumulative[bounds_array[1:]] - cumulative[bounds_array[:-1]]
        return cls(order=order, bounds=bounds_array, tokens=tokens, batch_tokens=batch_tokens)

    def __len__(self) -> int:
        """Return the number of packed batches in the pass."""
        return len(self.bounds) - 1

    def global_steps(self, world_size: int = 1) -> int:
        """Return the updates in one pass when each rank takes one batch per update."""
        return math.ceil(len(self) / world_size)

    def step_target_counts(self, world_size: int = 1) -> np.ndarray:
        """Return the targets each global update trains on, across all ranks."""
        padded = np.zeros(self.global_steps(world_size) * world_size, dtype=np.int64)
        padded[: len(self)] = self.tokens
        return padded.reshape(-1, world_size).sum(axis=1)

    def batch(self, index: int) -> list[int]:
        """Return the item indices of one packed batch."""
        return self.order[self.bounds[index] : self.bounds[index + 1]].tolist()


class TokenBudgetBatchSampler(Sampler[list[int]]):
    """Yield one packed batch per rank per update from a `TokenBatchPlan`.

    Update `t` gives rank `r` the plan's batch `t * world_size + r`. A rank with no
    batch left in the final update yields an empty list, so every rank still takes
    part in the update. Resume by passing the completed update count as the cursor.

    Args:
        plan (`TokenBatchPlan`): Packed batches for the pass.
        batch_cursor (`int`, *optional*, defaults to `0`): Completed global updates to skip.
        rank (`int`, *optional*, defaults to `0`): Worker rank in `[0, world_size)`.
        world_size (`int`, *optional*, defaults to `1`): Total number of workers.
        max_batches (`int`, *optional*): Cap the number of global updates.
    """

    def __init__(
        self,
        plan: TokenBatchPlan,
        *,
        batch_cursor: int = 0,
        rank: int = 0,
        world_size: int = 1,
        max_batches: int | None = None,
    ) -> None:
        """Store the plan and this rank's position."""
        super().__init__()
        if rank < 0 or world_size < 1 or rank >= world_size:
            raise ValueError(
                f"Invalid rank {rank} for world size {world_size}. Rank must be in [0, world_size)."
            )
        self.plan = plan
        self.batch_cursor = batch_cursor
        self.rank = rank
        self.world_size = world_size
        self.max_batches = max_batches

    def __len__(self) -> int:
        """Return the capped global update count before applying the cursor."""
        steps = self.plan.global_steps(self.world_size)
        return steps if self.max_batches is None else min(steps, self.max_batches)

    def __iter__(self) -> Iterator[list[int]]:
        """Yield this rank's item indices for each remaining update."""
        for step in range(self.batch_cursor, len(self)):
            index = step * self.world_size + self.rank
            yield self.plan.batch(index) if index < len(self.plan) else []


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

    def step_target_counts(self, lengths: np.ndarray) -> np.ndarray:
        """Return the targets each global update trains on, across all ranks.

        Args:
            lengths (`numpy.ndarray`): Targets per dataset item, such as
                `TokenChunkDataset.target_counts()`. Only exact for items the
                collator does not truncate.
        """
        lengths = np.asarray(lengths, dtype=np.int64)
        if len(lengths) != self.dataset_size:
            raise ValueError("lengths must have one entry per dataset item")
        generator = torch.Generator().manual_seed(self.seed)
        order = (
            torch.randperm(self.dataset_size, generator=generator).numpy()
            if self.shuffle
            else np.arange(self.dataset_size)
        )
        global_batch = self.batch_size * self.world_size
        steps = (
            self.dataset_size // global_batch
            if self.drop_last
            else math.ceil(self.dataset_size / global_batch)
        )
        if self.max_batches is not None:
            steps = min(steps, self.max_batches)
        ordered = lengths[order][: steps * global_batch]
        padded = np.zeros(steps * global_batch, dtype=np.int64)
        padded[: len(ordered)] = ordered
        return padded.reshape(steps, global_batch).sum(axis=1)


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
