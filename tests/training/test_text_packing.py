from __future__ import annotations

import itertools
import random
from typing import TYPE_CHECKING, cast

import lance
import pyarrow as pa
import pytest
import torch
from lance.torch.data import SafeLanceDataset
from pydantic import ValidationError

from flock.training.core.data_utils import (
    DocumentLengths,
    LanceBatchCollator,
    TokenBudgetBatchSampler,
    tokenize_documents,
)
from flock.training.core.lr_scheduler import LRSchedulerConfig, LRScheduleSegment
from flock.training.core.models.llama32 import Llama32
from flock.training.core.optimizers import AdamWConfig, MuonConfig
from flock.training.core.trainer import Trainer, TrainerConfig

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any

    from transformers import TokenizersBackend

    from flock.training.core.models.llama32 import Llama32Config

WORDS = ["a", "b", "c", "d"]


class FakeRun:
    entity, project, id = "flock", "pretrain", "run123"

    def __init__(self) -> None:
        self.metrics: list[dict[str, Any]] = []

    def define_metric(self, *args: Any, **kwargs: Any) -> None:
        pass

    def log(self, metrics: dict[str, Any]) -> None:
        self.metrics.append(metrics)


def documents(count: int, seed: int = 0) -> list[dict[str, str]]:
    """Documents of 0 to 20 words, so some exceed the tokenizer's 16-token limit."""
    rng = random.Random(seed)
    return [
        {"text": " ".join(rng.choice(WORDS) for _ in range(rng.randrange(21)))}
        for _ in range(count)
    ]


def sampler(lengths: list[int], *, max_tokens: int = 40, **options: Any) -> TokenBudgetBatchSampler:
    return TokenBudgetBatchSampler(
        dataset_size=len(lengths),
        lengths=lambda indices: [lengths[i] for i in indices],
        max_tokens=max_tokens,
        **{"max_batches": 1000, "block_size": 7, **options},
    )


def drain(budget: TokenBudgetBatchSampler) -> tuple[list[list[int]], list[int]]:
    batches, cursors = [], []
    for number, batch in enumerate(budget, start=budget.batch_cursor + 1):
        batches.append(batch)
        cursors.append(budget.document_cursor_after(number))
    return batches, cursors


@pytest.mark.parametrize("world_size", [1, 3])
def test_budget_batches_are_deterministic_and_cover_each_document_once(world_size: int) -> None:
    rng = random.Random(1)
    lengths = [rng.randrange(0, 16) for _ in range(200)]
    seen = []
    for rank in range(world_size):
        first, _ = drain(sampler(lengths, rank=rank, world_size=world_size))
        second, _ = drain(sampler(lengths, rank=rank, world_size=world_size))
        assert first == second
        nonempty = [batch for batch in first if batch]
        for batch, following in itertools.pairwise(nonempty):
            used = sum(lengths[i] for i in batch)
            assert used <= 40
            # A batch closes only when the next document would not fit.
            assert used + lengths[following[0]] > 40
        # Once a rank runs out it yields empty batches, to the same total as the others.
        assert len(first) == 1000 and not any(first[len(nonempty) :])
        seen += [i for batch in first for i in batch]
    assert sorted(seen) == list(range(200))
    # Rank zero of one reads the seeded permutation in order, as the row sampler does.
    order = torch.randperm(200, generator=torch.Generator().manual_seed(42)).tolist()
    batches = [i for batch in drain(sampler(lengths))[0] for i in batch]
    assert batches == order


@pytest.mark.parametrize("split", [0, 1, 4, 9])
def test_resuming_from_cursors_repeats_and_skips_no_document(split: int) -> None:
    rng = random.Random(2)
    lengths = [rng.randrange(0, 16) for _ in range(100)]
    options: dict[str, Any] = {"rank": 1, "world_size": 2, "max_batches": 20}
    reference, cursors = drain(sampler(lengths, **options))
    document_cursor = cursors[split - 1] if split else 0
    resumed, _ = drain(
        sampler(lengths, batch_cursor=split, document_cursor=document_cursor, **options)
    )
    assert reference[:split] + resumed == reference
    assert len(resumed) == 20 - split


def test_cursor_reads_follow_a_loader_running_ahead() -> None:
    budget = sampler([3] * 30, max_tokens=10, max_batches=5)
    batches = iter(budget)
    ahead = [next(batches) for _ in range(3)]
    assert [len(batch) for batch in ahead] == [3, 3, 3]
    assert budget.document_cursor_after(2) == 6
    assert budget.document_cursor_after(3) == 9
    with pytest.raises(KeyError):
        budget.document_cursor_after(1)


def test_document_over_the_budget_is_rejected() -> None:
    with pytest.raises(ValueError, match="over the 4 budget"):
        drain(sampler([2, 5], max_tokens=4))


def test_collator_packs_truncated_documents_with_boundaries(tokenizer: TokenizersBackend) -> None:
    rows = [
        {"text": "a b c d"},
        {"text": "d c"},
        {"text": "d"},
        {"text": ""},
        {"text": " ".join(["a"] * 30)},
    ]
    batch = LanceBatchCollator(tokenizer, "text", packed_tokens=24).collate_fn(rows)
    ids = tokenize_documents(tokenizer, [row["text"] for row in rows])
    # The long document is truncated to the tokenizer's 16-token limit.
    assert [len(document) for document in ids] == [4, 2, 1, 0, 16]
    assert batch["input_ids"].shape == (1, 24)
    # Documents of 3, 1 and 15 inputs; those without a target add nothing; 5 of padding.
    assert batch["cu_seq_lens"].tolist() == [0, 3, 4, 19, 24]
    assert batch["cu_seq_lens"].dtype == torch.int32
    assert batch["position_ids"][0].tolist() == [0, 1, 2, 0, *range(15), *range(5)]
    assert batch["input_ids"][0, :3].tolist() == ids[0][:-1]
    assert batch["labels"][0, :3].tolist() == ids[0][1:]
    assert batch["labels"][0, 3].item() == ids[1][1]
    assert batch["labels"][0, 19:].eq(-100).all()
    assert batch["attention_mask"][0].tolist() == [True] * 19 + [False] * 5
    assert int((batch["labels"] != -100).sum()) == 3 + 1 + 15

    empty = LanceBatchCollator(tokenizer, "text", packed_tokens=24).collate_fn([])
    assert empty["input_ids"].shape == (1, 24)
    assert empty["cu_seq_lens"].tolist() == [0, 24]
    assert empty["labels"].eq(-100).all()


def test_lengths_match_the_collated_documents(tokenizer: TokenizersBackend) -> None:
    rows = documents(50)
    lengths = DocumentLengths(rows, tokenizer, "text")(list(range(50)))
    collator = LanceBatchCollator(tokenizer, "text", packed_tokens=1000)
    batch = collator.collate_fn(rows)
    documents_inputs = batch["cu_seq_lens"].diff().tolist()[:-1]
    assert documents_inputs == [length for length in lengths if length > 0]
    assert max(lengths) == tokenizer.model_max_length - 1


@pytest.fixture
def packed_trainer(
    llama32_config: Llama32Config,
    tokenizer: TokenizersBackend,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    source = tmp_path / "docs.lance"
    lance.write_dataset(pa.Table.from_pylist(documents(60)), str(source))

    def make(output_dir: Path, **overrides: Any) -> Trainer:
        monkeypatch.setattr("flock.training.core.trainer.wandb.init", lambda **kwargs: FakeRun())
        config = TrainerConfig.model_validate(
            {
                "run_name": "packed",
                "input_column": "text",
                "batch_size": 1,
                "max_steps": 8,
                "max_tokens_per_device": 40,
                "optimizer_config": MuonConfig(
                    lr=0.02,
                    momentum=0.95,
                    companion_optimizer_config=AdamWConfig(lr=0.01, betas=(0.9, 0.99)),
                ),
                "lr_scheduler_config": LRSchedulerConfig(
                    schedule=[
                        LRScheduleSegment(start=0, end=2, start_ratio=0.0, end_ratio=1.0),
                        LRScheduleSegment(
                            start=2,
                            end=1.0,
                            start_ratio=1.0,
                            end_ratio=0.01,
                            interpolation="1-sqrt",
                        ),
                    ]
                ),
                "dataloader_num_workers": 0,
                "dataloader_prefetch_factor": None,
                "wandb_project": "offline-test",
                "wandb_mode": "disabled",
                "output_dir": str(output_dir),
                "save_steps": 3,
                "logging_steps": 1,
                **overrides,
            }
        )
        return Trainer(
            config=config,
            model=Llama32(llama32_config),
            tokenizer=tokenizer,
            dataset=SafeLanceDataset(str(source), dataset_options={"version": 1}),
        )

    return make


def test_packed_training_resumes_like_uninterrupted_training(
    packed_trainer: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reference = cast(Trainer, packed_trainer(tmp_path / "reference"))
    seen: list[torch.Tensor] = []
    forward = reference.model.forward

    def record(batch: Any) -> torch.Tensor:
        assert batch["input_ids"].shape == (1, 40)
        assert int(batch["cu_seq_lens"][-1]) == 40
        seen.append(batch["input_ids"].clone())
        return forward(batch)

    monkeypatch.setattr(reference.model, "forward", record)
    reference.train()
    assert reference.total_steps == 8 and len(seen) == 8
    expected_state = reference.state_dict()
    cursor = expected_state["rank_states"][0]["data_state"]
    assert cursor["batch_cursor"] == 8 and 0 < cursor["document_cursor"] <= 60

    interrupted = cast(Trainer, packed_trainer(tmp_path / "resumed"))
    save = interrupted.checkpoint_manager.save_model

    def save_and_interrupt(*args: Any, **kwargs: Any) -> None:
        save(*args, **kwargs)
        raise KeyboardInterrupt("after the first checkpoint")

    monkeypatch.setattr(interrupted.checkpoint_manager, "save_model", save_and_interrupt)
    with pytest.raises(KeyboardInterrupt):
        interrupted.train()
    resumed = cast(Trainer, packed_trainer(tmp_path / "resumed"))
    resumed.train(resume_from_checkpoint=True)
    torch.testing.assert_close(resumed.model.state_dict(), reference.model.state_dict())
    resumed_state = resumed.state_dict()
    # The same documents trained once each: same cursor, targets and optimizer state.
    assert resumed_state["rank_states"][0]["data_state"] == cursor
    assert int(resumed.state.total_tokens) == int(reference.state.total_tokens)
    resumed_optimizer = resumed_state["optimizer_state_dict"]
    expected_optimizer = expected_state["optimizer_state_dict"]
    assert resumed_optimizer["param_groups"] == expected_optimizer["param_groups"]
    torch.testing.assert_close(resumed_optimizer["state"], expected_optimizer["state"])

    # A different budget plans different updates, so its checkpoint must not resume.
    other = cast(Trainer, packed_trainer(tmp_path / "other", max_tokens_per_device=50))
    with pytest.raises(ValueError, match="data"):
        other.validate_state_dict(resumed_state)


def test_packing_settings_are_validated(packed_trainer: Any, tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="max_steps"):
        packed_trainer(tmp_path / "a", max_steps=None)
    with pytest.raises(ValidationError, match="batch_size=1"):
        packed_trainer(tmp_path / "b", batch_size=2)
    with pytest.raises(ValueError, match="model_max_length"):
        packed_trainer(tmp_path / "c", max_tokens_per_device=8)
