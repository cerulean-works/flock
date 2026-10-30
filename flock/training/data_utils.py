from dataclasses import dataclass, field
from typing import Any, TypedDict

import torch
from lance.torch.data import SafeLanceDataset
from transformers import TokenizersBackend

from flock.hub import s3


class Batch(TypedDict):
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
            "attention_mask": mask,
            "labels": labels,
        }


@dataclass
class DatasetConfig:
    uri: str
    version: str = field(doc="Flock hub's version number or tag")


def get_dataset(config: DatasetConfig) -> SafeLanceDataset:
    creds = s3.credentials(org="cerulean-labs")

    # SafeLanceDataset is a lazy wrapper around a lance dataset, delaying ds initialization until
    # first read to prevent pickling a LanceDataset during spawn
    return SafeLanceDataset(
        uri=config.uri,
        dataset_options={
            "version": config.version,
            "storage_options": creds.storage_options(),
        },
    )
