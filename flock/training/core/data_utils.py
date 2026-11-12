"""Load pinned Lance datasets and collate resumable next-token training batches."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from math import ceil
from typing import Any

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
    """

    input_ids: torch.Tensor
    attention_mask: torch.Tensor
    labels: torch.Tensor


class LanceBatchCollator:
    """Tokenize text rows into padded inputs and already shifted next-token targets.

    This module-level class is pickleable for spawned DataLoader workers.

    Args:
        tokenizer (`TokenizersBackend`): Tokenizer with padding and length configured.
        input_column (`str`): Text field in each dataset row.
    """

    def __init__(self, tokenizer: TokenizersBackend, input_column: str) -> None:
        """Store the tokenizer and source text column for worker-side collation."""
        self.tokenizer = tokenizer
        self.input_column = input_column

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
        # It is up to the model to set max length, padding side and padding token
        tokenized: dict[str, torch.Tensor] = self.tokenizer(
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


class ResumableBatchDataSampler(Sampler[list[int]]):
    """Yield deterministic index batches while skipping a saved batch prefix.

    Each iteration reconstructs the same permutation with a private generator.
    `len(sampler)` reports the full pass length, including skipped batches.

    Args:
        batch_size (`int`): Positive rows per batch.
        dataset_size (`int`): Nonnegative number of rows.
        shuffle (`bool`, *optional*, defaults to `True`): Shuffle row indices.
        seed (`int`, *optional*, defaults to `42`): Private permutation seed.
        skip_batches (`int`, *optional*, defaults to `0`): Completed batches to skip.
        drop_last (`bool`, *optional*, defaults to `False`): Drop the final partial batch.
    """

    def __init__(
        self,
        batch_size: int,
        dataset_size: int,
        shuffle: bool = True,
        seed: int = 42,
        skip_batches: int = 0,
        drop_last: bool = False,
    ) -> None:
        """Store batching settings without consuming the global RNG stream."""
        super().__init__()
        self.dataset_size = dataset_size
        self.batch_size = batch_size
        self.shuffle = shuffle
        self.seed = seed
        self.skip_batches = skip_batches
        self.drop_last = drop_last

    def __len__(self) -> int:
        """Return the full number of batches before applying the skip prefix."""
        if self.drop_last:
            return self.dataset_size // self.batch_size
        return ceil(self.dataset_size / self.batch_size)

    def __iter__(self) -> Iterator[list[int]]:
        """Yield row-index lists after the configured number of skipped batches."""
        generator = torch.Generator()
        generator.manual_seed(self.seed)

        indices = (
            torch.randperm(self.dataset_size, generator=generator)
            if self.shuffle
            else range(self.dataset_size)
        )

        for batch_idx in range(self.skip_batches, len(self)):
            start = batch_idx * self.batch_size
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
