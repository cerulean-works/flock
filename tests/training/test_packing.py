"""Token-budget packing: plan, sampler, collator and packed model forward."""

from itertools import pairwise
from pathlib import Path

import lance
import numpy as np
import pyarrow as pa
import pytest
import torch
from lance.torch.data import SafeLanceDataset
from transformers import TokenizersBackend

from flock.training import TokenChunkDataset
from flock.training.core.data_utils import (
    PackedBatchCollator,
    ResumableBatchDataSampler,
    TokenBatchPlan,
    TokenBudgetBatchSampler,
)
from flock.training.core.models.olmo2 import Olmo2, Olmo2Config


def make_chunks(
    tmp_path: Path, tokenizer: TokenizersBackend, chunk_length: int = 4
) -> TokenChunkDataset:
    texts = ["a b c d " * 3, "b c", "d a b c d", "a", "c d a b " * 2]
    source = tmp_path / "source.lance"
    lance.write_dataset(pa.table({"text": texts}), str(source))
    return TokenChunkDataset.prepare(
        dataset=SafeLanceDataset(str(source), dataset_options={"version": 1}),
        tokenizer=tokenizer,
        input_column="text",
        cache_dir=tmp_path / "tokens",
        chunk_length=chunk_length,
    )


@pytest.mark.parametrize("chunk_length", [2, 4, 7])
def test_target_counts_match_each_chunk(
    tmp_path: Path, tokenizer: TokenizersBackend, chunk_length: int
) -> None:
    chunks = make_chunks(tmp_path, tokenizer, chunk_length)
    expected = [len(row["_flock_chunk_ids"]) - 1 for row in chunks]
    assert chunks.target_counts().tolist() == expected


@pytest.mark.parametrize("budget", [3, 4, 10])
@pytest.mark.parametrize("seed", [0, 7])
def test_plan_packs_each_item_once_within_budget(budget: int, seed: int) -> None:
    lengths = np.array([3, 1, 2, 3, 3, 1, 1, 2, 3])
    plan = TokenBatchPlan.build(lengths, budget, seed=seed)
    packed = [plan.batch(index) for index in range(len(plan))]
    assert sorted(i for batch in packed for i in batch) == list(range(len(lengths)))
    assert all(0 < lengths[batch].sum() <= budget for batch in packed)
    # Greedy packing: the next item never fit into the batch it follows.
    for batch, following in pairwise(packed):
        assert lengths[batch].sum() + lengths[following[0]] > budget
    assert plan.tokens.tolist() == [int(lengths[batch].sum()) for batch in packed]
    rebuilt = TokenBatchPlan.build(lengths, budget, seed=seed)
    assert np.array_equal(rebuilt.order, plan.order)


def test_plan_rejects_items_larger_than_the_budget() -> None:
    with pytest.raises(ValueError, match="batch_tokens"):
        TokenBatchPlan.build(np.array([2, 5]), 4)


@pytest.mark.parametrize("world_size", [1, 2, 3])
def test_sampler_splits_updates_across_ranks_and_resumes(world_size: int) -> None:
    plan = TokenBatchPlan.build(np.array([3, 1, 2, 3, 3, 1, 1, 2, 3]), 4, seed=1)
    steps = plan.global_steps(world_size)
    per_rank = [
        list(TokenBudgetBatchSampler(plan, rank=rank, world_size=world_size))
        for rank in range(world_size)
    ]
    assert all(len(batches) == steps for batches in per_rank)
    flattened = [per_rank[rank][step] for step in range(steps) for rank in range(world_size)]
    assert [batch for batch in flattened if batch] == [plan.batch(i) for i in range(len(plan))]
    counts = plan.step_target_counts(world_size)
    assert counts.sum() == plan.tokens.sum() and len(counts) == steps
    resumed = TokenBudgetBatchSampler(plan, batch_cursor=1, rank=0, world_size=world_size)
    assert list(resumed) == per_rank[0][1:]
    assert len(TokenBudgetBatchSampler(plan, world_size=world_size, max_batches=1)) == 1


@pytest.mark.parametrize("world_size", [1, 2])
@pytest.mark.parametrize("max_batches", [None, 2])
def test_item_sampler_step_counts_follow_its_batches(
    world_size: int, max_batches: int | None
) -> None:
    lengths = np.array([3, 1, 2, 3, 3, 1, 1])
    samplers = [
        ResumableBatchDataSampler(
            batch_size=2,
            dataset_size=len(lengths),
            seed=3,
            rank=rank,
            world_size=world_size,
            max_batches=max_batches,
        )
        for rank in range(world_size)
    ]
    expected = [
        sum(int(lengths[batch].sum()) for batch in step)
        for step in zip(*(list(sampler) for sampler in samplers))
    ]
    assert samplers[0].step_target_counts(lengths).tolist() == expected


def test_collator_isolates_documents_and_fills_the_row() -> None:
    collator = PackedBatchCollator(pad_token_id=0, batch_tokens=9, max_document_tokens=3)
    batch = collator.collate_fn([{"_flock_chunk_ids": [2, 3, 4, 5]}, {"_flock_chunk_ids": [6, 7]}])
    assert batch["input_ids"].tolist() == [[2, 3, 4, 6, 0, 0, 0, 0, 0]]
    assert batch["labels"].tolist() == [[3, 4, 5, 7, -100, -100, -100, -100, -100]]
    assert batch["position_ids"].tolist() == [[0, 1, 2, 0, 0, 1, 2, 0, 1]]
    # Padding is split into documents of at most three inputs.
    assert batch["cu_seqlens"].tolist() == [0, 3, 4, 7, 9]
    assert batch["cu_seqlens"].dtype == torch.int32
    assert batch["attention_mask"].all()

    empty = collator.collate_fn([])
    assert (empty["labels"] == -100).all() and empty["input_ids"].shape == (1, 9)
    with pytest.raises(ValueError, match="exceed"):
        collator.collate_fn([{"_flock_chunk_ids": [1, 2, 3, 4]}] * 4)


def test_packed_forward_matches_documents_run_separately(config: Olmo2Config) -> None:
    model = Olmo2(config).eval()
    documents = [torch.tensor([2, 3, 4]), torch.tensor([5]), torch.tensor([3, 2, 5, 4])]
    collator = PackedBatchCollator(pad_token_id=0, batch_tokens=10, max_document_tokens=4)
    batch = collator.collate_fn([{"_flock_chunk_ids": [*ids.tolist(), 1]} for ids in documents])
    with torch.no_grad():
        packed = model(batch)[0]
        start = 0
        for ids in documents:
            separate = model(
                {
                    "input_ids": ids[None],
                    "attention_mask": torch.ones(1, len(ids), dtype=torch.bool),
                    "labels": torch.full((1, len(ids)), -100),
                }
            )[0]
            torch.testing.assert_close(packed[start : start + len(ids)], separate)
            start += len(ids)
