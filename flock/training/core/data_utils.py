from dataclasses import dataclass, field
from math import ceil
from typing import Any, Iterator

import torch
from lance.torch.data import SafeLanceDataset
from torch.utils.data import Dataset, Sampler
from transformers import TokenizersBackend
from typing_extensions import TypedDict

from flock.hub import s3


class Batch(TypedDict, closed=True):
    input_ids: torch.Tensor
    attention_mask: torch.Tensor
    labels: torch.Tensor


class LanceBatchCollator:
    """DataLoader Collator that transforms a Lance dataset row into a tokenized and padded batch

    Since we use `spawn` as our worker strategy, the collate_fn must be pickleable. An easy way to
    do this is just to write a global class.
    """

    def __init__(self, tokenizer: TokenizersBackend, input_column: str):
        self.tokenizer = tokenizer
        self.input_column = input_column

    def collate_fn(self, batch: list[dict[str, Any]]) -> Batch:
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
    def __init__(
        self,
        batch_size: int,
        dataset_size: int,
        shuffle: bool = True,
        seed=42,
        skip_batches=0,
        drop_last=False,
    ):
        super().__init__()
        self.dataset_size = dataset_size
        self.batch_size = batch_size
        self.shuffle = shuffle
        self.seed = seed
        self.skip_batches = skip_batches
        self.drop_last = drop_last

    def __len__(self) -> int:
        if self.drop_last:
            return self.dataset_size // self.batch_size
        return ceil(self.dataset_size / self.batch_size)

    def __iter__(self) -> Iterator[list[int]]:
        generator = torch.Generator()
        generator.manual_seed(self.seed)

        indices = (
            torch.randperm(self.dataset_size, generator=generator).tolist()
            if self.shuffle
            else list(range(self.dataset_size))
        )

        for batch_idx in range(self.skip_batches, len(self)):
            start = batch_idx * self.batch_size
            end = start + self.batch_size
            yield indices[start:end]


@dataclass(frozen=True, kw_only=True)
class DatasetConfig:
    uri: str
    version: str | int = field(doc="Flock hub's version number or tag")


def get_dataset(config: DatasetConfig) -> SafeLanceDataset:
    if config.uri.startswith(("s3://", "s3a://", "s3n://")):
        s3.get_process(org="cerulean-labs", read_only=True)
    # Spawned workers inherit the native credential-process configuration.
    return SafeLanceDataset(
        uri=config.uri,
        dataset_options={"version": config.version},
    )
