from __future__ import annotations

from typing import TYPE_CHECKING, Never

import pytest
import torch

from flock.training.core import data_utils
from flock.training.core.data_utils import (
    DatasetConfig,
    LanceBatchCollator,
    ResumableBatchDataSampler,
)

if TYPE_CHECKING:
    from typing import Any

    from transformers import TokenizersBackend


@pytest.mark.parametrize(
    "padding_side, inputs, masks, labels",
    [
        ("left", [[2, 3], [1, 3]], [[True, True], [False, True]], [[3, 1], [-100, 1]]),
        ("right", [[2, 3], [3, 1]], [[True, True], [True, True]], [[3, 1], [1, -100]]),
    ],
)
def test_collator_shifts_targets_and_preserves_real_eos(
    tokenizer: TokenizersBackend,
    padding_side: str,
    inputs: list[list[int]],
    masks: list[list[bool]],
    labels: list[list[int]],
) -> None:
    tokenizer.padding_side = padding_side
    assert tokenizer.eos_token_id == tokenizer.pad_token_id == 1
    batch = LanceBatchCollator(tokenizer, "body").collate_fn(
        [{"body": "a b [EOS]"}, {"body": "b [EOS]"}]
    )
    assert set(batch) == {"input_ids", "attention_mask", "labels"}
    assert batch["input_ids"].tolist() == inputs
    assert batch["attention_mask"].tolist() == masks
    assert batch["attention_mask"].dtype == torch.bool
    assert batch["labels"].tolist() == labels
    assert batch["labels"].dtype == batch["input_ids"].dtype == torch.long
    # Neither masked padding nor a pad -> first-token boundary contributes a target.
    assert (batch["labels"] != -100).sum().item() == 3
    assert (batch["labels"] == tokenizer.eos_token_id).sum().item() == 2


def test_collator_truncates_before_shifting(tokenizer: TokenizersBackend) -> None:
    tokenizer.model_max_length = 4
    batch = LanceBatchCollator(tokenizer, "text").collate_fn([{"text": "a b c d a b"}])
    assert batch["input_ids"].tolist() == [[2, 3, 4]]
    assert batch["labels"].tolist() == [[3, 4, 5]]
    assert batch["attention_mask"].tolist() == [[True, True, True]]


def test_collator_does_not_mutate_rows(tokenizer: TokenizersBackend) -> None:
    rows = [{"text": "a b c", "metadata": {"id": 1}}]
    LanceBatchCollator(tokenizer, "text").collate_fn(rows)
    assert rows == [{"text": "a b c", "metadata": {"id": 1}}]


@pytest.mark.parametrize("version", [7, "release"])
@pytest.mark.parametrize("uri", ["s3://offline-bucket/text", "/offline/text.lance"])
def test_dataset_factory_configures_native_credentials_for_s3(
    monkeypatch: pytest.MonkeyPatch, version: int | str, uri: str
) -> None:
    calls = []
    sentinel = object()

    def get_process(**kwargs: Any) -> None:
        calls.append(("get_process", kwargs))

    def dataset(**kwargs: Any) -> object:
        calls.append(("dataset", kwargs))
        return sentinel

    monkeypatch.setattr(data_utils, "SafeLanceDataset", dataset)
    monkeypatch.setattr(data_utils.s3, "get_process", get_process)
    config = DatasetConfig(uri=uri, version=version)
    assert data_utils.get_dataset(config) is sentinel
    setup = (
        [("get_process", {"org": "cerulean-labs", "read_only": True})]
        if uri.startswith("s3://")
        else []
    )
    assert calls == [
        *setup,
        (
            "dataset",
            {
                "uri": config.uri,
                "dataset_options": {"version": version},
            },
        ),
    ]


def test_dataset_factory_propagates_open_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def dataset(**kwargs: Any) -> Never:
        raise RuntimeError("dataset open failed")

    monkeypatch.setattr(data_utils, "SafeLanceDataset", dataset)
    with pytest.raises(RuntimeError, match="dataset open failed"):
        data_utils.get_dataset(DatasetConfig(uri="/offline/text.lance", version=1))


def test_dataset_factory_propagates_process_setup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def get_process(**kwargs: Any) -> Never:
        raise RuntimeError("credential process setup failed")

    monkeypatch.setattr(data_utils.s3, "get_process", get_process)
    with pytest.raises(RuntimeError, match="credential process setup failed"):
        data_utils.get_dataset(DatasetConfig(uri="s3://offline-bucket/text", version=1))


@pytest.mark.parametrize(
    ("skip_batches", "max_batches", "expected"),
    [(0, None, [0, 1, 2]), (0, 2, [0, 1]), (1, 2, [1]), (2, 2, []), (0, 10, [0, 1, 2])],
)
def test_sampler_cap_keeps_the_seeded_order(
    skip_batches: int, max_batches: int | None, expected: list[int]
) -> None:
    full = list(ResumableBatchDataSampler(batch_size=2, dataset_size=5))
    sampler = ResumableBatchDataSampler(
        batch_size=2, dataset_size=5, skip_batches=skip_batches, max_batches=max_batches
    )
    assert len(sampler) == (3 if max_batches is None else min(3, max_batches))
    assert list(sampler) == [full[index] for index in expected]
