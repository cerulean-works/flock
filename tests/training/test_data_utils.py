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
    "padding_side, inputs, masks, labels, positions",
    [
        (
            "left",
            [[2, 3], [1, 3]],
            [[True, True], [False, True]],
            [[3, 1], [-100, 1]],
            # Left padding does not shift the first real token from position 0.
            [[0, 1], [0, 0]],
        ),
        (
            "right",
            [[2, 3], [3, 1]],
            [[True, True], [True, True]],
            [[3, 1], [1, -100]],
            [[0, 1], [0, 1]],
        ),
    ],
)
def test_collator_shifts_targets_and_preserves_real_eos(
    tokenizer: TokenizersBackend,
    padding_side: str,
    inputs: list[list[int]],
    masks: list[list[bool]],
    labels: list[list[int]],
    positions: list[list[int]],
) -> None:
    tokenizer.padding_side = padding_side
    assert tokenizer.eos_token_id == tokenizer.pad_token_id == 1
    batch = LanceBatchCollator(tokenizer, "body").collate_fn(
        [{"body": "a b [EOS]"}, {"body": "b [EOS]"}]
    )
    assert set(batch) == {"input_ids", "attention_mask", "labels", "position_ids"}
    assert batch["input_ids"].tolist() == inputs
    assert batch["attention_mask"].tolist() == masks
    assert batch["attention_mask"].dtype == torch.bool
    assert batch["labels"].tolist() == labels
    assert batch["labels"].dtype == batch["input_ids"].dtype == torch.long
    assert batch["position_ids"].tolist() == positions
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


@pytest.mark.parametrize(
    "dataset_size, world_size, yield_empty, expected",
    [
        (0, 1, True, [[]]),
        (0, 2, True, [[], []]),
        (4, 1, None, [[[0, 1], [2, 3]]]),
        (4, 1, False, [[[0, 1], [2, 3]]]),
        (8, 2, True, [[[0, 1], [4, 5]], [[2, 3], [6, 7]]]),
        (8, 2, False, [[[0, 1], [4, 5]], [[2, 3], [6, 7]]]),
        (5, 2, True, [[[0, 1], [4]], [[2, 3], []]]),
        (5, 2, False, [[[0, 1], [4]], [[2, 3]]]),
        (1, 3, None, [[[0]], [[]], [[]]]),
        (1, 3, False, [[[0]], [], []]),
    ],
)
def test_sampler_keeps_complete_batches_and_handles_rank_tails(
    dataset_size: int,
    world_size: int,
    yield_empty: bool | None,
    expected: list[list[list[int]]],
) -> None:
    for rank in range(world_size):
        sampler = ResumableBatchDataSampler(
            batch_size=2,
            dataset_size=dataset_size,
            shuffle=False,
            rank=rank,
            world_size=world_size,
            yield_empty_last_batch=yield_empty,
        )
        assert len(sampler) == len(expected[rank])
        assert list(sampler) == expected[rank]


@pytest.mark.parametrize("shuffle", [False, True])
@pytest.mark.parametrize("yield_empty", [False, True])
def test_sampler_shards_one_permutation_and_resumes_each_rank(
    shuffle: bool, yield_empty: bool
) -> None:
    rng_state = torch.get_rng_state().clone()
    batches = []
    for rank in range(3):
        sampler = ResumableBatchDataSampler(
            batch_size=2,
            dataset_size=13,
            shuffle=shuffle,
            seed=17,
            rank=rank,
            world_size=3,
            yield_empty_last_batch=yield_empty,
        )
        full = list(sampler)
        assert list(sampler) == full
        for cursor in range(5):
            sampler.batch_cursor = cursor
            assert len(sampler) == len(full)
            assert list(sampler) == full[cursor:]
        batches.append(full)
    ordered_rows = [
        row
        for step in range(max(map(len, batches)))
        for rank_batches in batches
        if step < len(rank_batches)
        for row in rank_batches[step]
    ]
    expected = (
        torch.randperm(13, generator=torch.Generator().manual_seed(17)).tolist()
        if shuffle
        else list(range(13))
    )
    assert ordered_rows == expected
    if yield_empty:
        assert [len(rank_batches) for rank_batches in batches] == [3, 3, 3]
    else:
        assert all(batch for rank_batches in batches for batch in rank_batches)
    torch.testing.assert_close(torch.get_rng_state(), rng_state, rtol=0, atol=0)


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
        batch_size=2, dataset_size=5, batch_cursor=skip_batches, max_batches=max_batches
    )
    assert len(sampler) == (3 if max_batches is None else min(3, max_batches))
    assert list(sampler) == [full[index] for index in expected]
