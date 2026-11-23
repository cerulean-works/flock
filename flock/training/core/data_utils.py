import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, NotRequired

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
            Packed rows, which hold `_flock_chunks`, also give `cu_seq_lens` and are
            padded to the row with the most inputs.

        Raises:
            `KeyError`: If a row lacks the configured input column.
        """
        if not batch:
            # Run attention on a valid input, but contribute no targets or loss.
            return {
                "input_ids": torch.full((1, 1), self.tokenizer.pad_token_id, dtype=torch.long),
                "attention_mask": torch.ones((1, 1), dtype=torch.bool),
                "labels": torch.full((1, 1), -100, dtype=torch.long),
                "position_ids": torch.zeros((1, 1), dtype=torch.long),
            }
        # Since the flock chunks is a packed representation, we use to check if the batch contains packed rows.
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

    def _collate_packed(self, batch: list[dict[str, Any]]) -> Batch:
        """Lay out each packed row's chunks as documents, padded to the longest row.

        Each chunk of `L` token IDs becomes one document of `L - 1` inputs with its own
        shifted targets, so no target crosses a document boundary. Positions restart at
        zero in each document. A row's unused tail is padding: one more document, with
        no targets and a `False` mask. `cu_seq_lens` covers every position of the
        flattened batch.
        """
        chunks = [
            [
                torch.as_tensor(chunk["_flock_chunk_ids"], dtype=torch.long)
                for chunk in item["_flock_chunks"]
            ]
            for item in batch
        ]
        rows = len(chunks)
        width = max(sum(len(ids) - 1 for ids in row) for row in chunks)
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
