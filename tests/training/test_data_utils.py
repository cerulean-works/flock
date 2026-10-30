from types import SimpleNamespace

import pytest
import torch

from flock.training.core import data_utils
from flock.training.core.data_utils import DatasetConfig, LanceBatchCollator


@pytest.mark.parametrize(
    "padding_side, inputs, masks, labels",
    [
        ("left", [[2, 3], [1, 3]], [[True, True], [False, True]], [[3, 1], [-100, 1]]),
        ("right", [[2, 3], [3, 1]], [[True, True], [True, True]], [[3, 1], [1, -100]]),
    ],
)
def test_collator_shifts_targets_and_preserves_real_eos(
    tokenizer, padding_side, inputs, masks, labels
):
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


def test_collator_truncates_before_shifting(tokenizer):
    tokenizer.model_max_length = 4
    batch = LanceBatchCollator(tokenizer, "text").collate_fn([{"text": "a b c d a b"}])
    assert batch["input_ids"].tolist() == [[2, 3, 4]]
    assert batch["labels"].tolist() == [[3, 4, 5]]
    assert batch["attention_mask"].tolist() == [[True, True, True]]


def test_collator_does_not_mutate_rows(tokenizer):
    rows = [{"text": "a b c", "metadata": {"id": 1}}]
    LanceBatchCollator(tokenizer, "text").collate_fn(rows)
    assert rows == [{"text": "a b c", "metadata": {"id": 1}}]


@pytest.mark.parametrize("version", [7, "release"])
def test_dataset_factory_forwards_version_and_storage_options(monkeypatch, version):
    calls = []
    options = {"aws_endpoint": "https://storage.invalid", "aws_region": "test-region"}

    def credentials(**kwargs):
        calls.append(("credentials", kwargs))
        return SimpleNamespace(storage_options=lambda: options)

    sentinel = object()

    def dataset(**kwargs):
        calls.append(("dataset", kwargs))
        return sentinel

    monkeypatch.setattr(data_utils.s3, "credentials", credentials)
    monkeypatch.setattr(data_utils, "SafeLanceDataset", dataset)
    config = DatasetConfig(uri="s3://offline-bucket/text", version=version)
    assert data_utils.get_dataset(config) is sentinel
    assert calls == [
        ("credentials", {"org": "cerulean-labs"}),
        (
            "dataset",
            {
                "uri": config.uri,
                "dataset_options": {"version": version, "storage_options": options},
            },
        ),
    ]


def test_dataset_factory_propagates_credentials_failure(monkeypatch):
    def credentials(**kwargs):
        raise RuntimeError("offline credential failure")

    monkeypatch.setattr(data_utils.s3, "credentials", credentials)
    with pytest.raises(RuntimeError, match="offline credential failure"):
        data_utils.get_dataset(DatasetConfig(uri="s3://offline-bucket/text", version=1))
