"""Check token-weighted DDP updates against one global batch on local Gloo workers."""

from __future__ import annotations

import copy
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import pytest
import torch
import torch.distributed as dist
import torch.multiprocessing as mp
from torch.nn import functional as F
from torch.nn.parallel import DistributedDataParallel as DDP

from flock.training.core.data_utils import LanceBatchCollator
from flock.training.core.distributed import DistributedRuntime
from flock.training.core.lr_scheduler import LRSchedulerConfig, LRScheduleSegment
from flock.training.core.models.olmo2 import Olmo2
from flock.training.core.optimizers import AdamWConfig
from flock.training.core.trainer import Trainer, TrainerConfig, TrainerState

if TYPE_CHECKING:
    from collections.abc import Callable

    from lance.torch.data import SafeLanceDataset
    from transformers import TokenizersBackend

    from flock.training.core.models.olmo2 import Olmo2Config


def _check_distributed_metrics(rank: int, rendezvous: str, logging_steps: int) -> None:
    torch.set_num_threads(1)
    dist.init_process_group(
        "gloo",
        init_method=rendezvous,
        rank=rank,
        world_size=2,
        timeout=timedelta(seconds=30),
    )
    try:
        torch.manual_seed(0)
        model = torch.nn.Linear(2, 3)
        reference = copy.deepcopy(model)
        training_model = DDP(model)
        optimizer = torch.optim.SGD(model.parameters(), lr=0.1)
        reference_optimizer = torch.optim.SGD(reference.parameters(), lr=0.1)
        trainer = object.__new__(Trainer)
        trainer.runtime = DistributedRuntime(rank, rank, 2, torch.device("cpu"))
        state = TrainerState()
        inputs = torch.linspace(-1, 2, 12).reshape(2, 3, 2)
        # Unequal counts, each rank empty in turn, and an entirely ignored batch.
        label_batches = [
            torch.tensor([[0, -100, -100], [2, 1, 0]]),
            torch.tensor([[-100, -100, -100], [1, 2, -100]]),
            torch.full((2, 3), -100, dtype=torch.long),
            torch.tensor([[0, 1, 2], [-100, -100, -100]]),
        ]
        window = []
        total_tokens = 0
        for step, labels in enumerate(label_batches, start=1):
            optimizer.zero_grad()
            reference_optimizer.zero_grad()
            output = training_model(inputs[rank : rank + 1])
            with patch.object(dist, "all_gather", wraps=dist.all_gather) as gather:
                loss, losses, tokens = trainer._calc_batch_loss(output, labels[rank : rank + 1])
                assert gather.call_count == 1
                assert not losses.requires_grad
                assert tokens.dtype == torch.int64
                torch.testing.assert_close(tokens, (labels != -100).sum(dim=1))
                reference_output = reference(inputs)
                reference_loss = F.cross_entropy(
                    reference_output.reshape(-1, 3), labels.reshape(-1), reduction="sum"
                ) / (labels != -100).sum().clamp_min(1)
                local_losses = F.cross_entropy(
                    reference_output.reshape(-1, 3), labels.reshape(-1), reduction="none"
                ).reshape(2, 3).sum(dim=1) / tokens.clamp_min(1)
                torch.testing.assert_close(losses, local_losses.detach().double())
                torch.testing.assert_close(
                    loss,
                    local_losses[rank] * 2 * tokens[rank] / tokens.sum().clamp_min(1),
                )

                loss.backward()
                reference_loss.backward()
                for actual, expected in zip(
                    model.parameters(), reference.parameters(), strict=True
                ):
                    torch.testing.assert_close(actual.grad, expected.grad)
                norm = torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=0.1)
                reference_norm = torch.nn.utils.clip_grad_norm_(
                    reference.parameters(), max_norm=0.1
                )
                torch.testing.assert_close(norm, reference_norm)
                optimizer.step()
                reference_optimizer.step()
                for actual, expected in zip(
                    model.parameters(), reference.parameters(), strict=True
                ):
                    torch.testing.assert_close(actual, expected)

                buffers = dict(state.named_buffers())
                with (
                    patch.object(
                        dist, "all_reduce", side_effect=AssertionError("unexpected reduce")
                    ),
                    patch.object(dist, "barrier", side_effect=AssertionError("unexpected barrier")),
                    patch.object(
                        dist,
                        "broadcast_object_list",
                        side_effect=AssertionError("unexpected broadcast"),
                    ),
                ):
                    state.update(1, losses, norm, tokens)
                assert gather.call_count == 1, "state updates must not communicate"
                assert all(state.get_buffer(name) is buffer for name, buffer in buffers.items())
            count = tokens.sum().item()
            total_tokens += count
            window.append(
                (reference_loss.item(), count, local_losses.max().item(), reference_norm.item())
            )
            window_tokens = sum(record[1] for record in window)
            metrics = state.get_logging_metrics()
            assert metrics == pytest.approx(
                {
                    "global_step": step,
                    "train/loss_mean": sum(loss * count for loss, count, _, _ in window)
                    / max(window_tokens, 1),
                    "train/loss_window_max": max(record[2] for record in window),
                    "train/grad_norm_mean": sum(record[3] for record in window) / len(window),
                    "train/grad_norm_window_max": max(record[3] for record in window),
                    "train/tokens": window_tokens,
                    "train/total_tokens": total_tokens,
                }
            )
            assert all(not buffer.requires_grad for buffer in state.buffers())
            # Compare every buffer, including unfinished windows, across real ranks.
            snapshots = [None, None]
            dist.all_gather_object(
                snapshots, {name: buffer.item() for name, buffer in state.named_buffers()}
            )
            assert snapshots[0] == snapshots[1]
            if step == 2:
                restored = TrainerState()
                restored.load_state_dict(copy.deepcopy(state.state_dict()))
                assert restored.get_logging_metrics() == metrics
                state = restored
            if step % logging_steps == 0 or step == len(label_batches):
                state.reset_window()
                window.clear()

        # Gathered counts must stay exact beyond FP32's integer range.
        state.update(1, torch.ones(2), torch.tensor(2.0), torch.tensor([2**24 + 1, 2**24 + 2]))
        metrics = state.get_logging_metrics()
        assert metrics["train/tokens"] == 2**25 + 3
        assert metrics["train/total_tokens"] == 2**25 + 3 + total_tokens
    finally:
        dist.destroy_process_group()


@pytest.mark.parametrize("logging_steps", [1, 2, 5])
def test_distributed_metrics_and_updates_match_global_batch(
    tmp_path: Path, logging_steps: int
) -> None:
    mp.spawn(
        _check_distributed_metrics,
        args=((tmp_path / "rendezvous").as_uri(), logging_steps),
        nprocs=2,
        join=True,
    )


def _check_distributed_step_budget(
    rank: int, directory: Path, config: Olmo2Config, tokenizer: TokenizersBackend
) -> None:
    torch.set_num_threads(1)
    dist.init_process_group(
        "gloo",
        init_method=(directory / "rendezvous").as_uri(),
        rank=rank,
        world_size=2,
        timeout=timedelta(seconds=30),
    )
    try:
        # Include empty-rank tails, including a dataset smaller than the world.
        for dataset_size, expected_steps in [(1, 1), (4, 1), (5, 2), (6, 2), (7, 2), (8, 2)]:
            with patch("flock.training.core.trainer.wandb.init") as init_run:
                trainer = Trainer(
                    config=TrainerConfig(
                        run_name="step-budget",
                        input_column="text",
                        batch_size=2,
                        optimizer_config=AdamWConfig(lr=0.01, betas=(0.8, 0.9)),
                        lr_scheduler_config=LRSchedulerConfig(
                            schedule=[
                                LRScheduleSegment(
                                    start=0,
                                    end=1.0,
                                    start_ratio=1.0,
                                    end_ratio=0.1,
                                )
                            ]
                        ),
                        dataloader_num_workers=0,
                        dataloader_prefetch_factor=None,
                        wandb_project="offline-test",
                        wandb_mode="disabled",
                        output_dir=str(directory / str(dataset_size)),
                        logging_steps=10,
                        model_dtype=torch.float32,
                    ),
                    model=Olmo2(config),
                    tokenizer=tokenizer,
                    dataset=cast("SafeLanceDataset", [{"text": "a b c"}] * dataset_size),
                    runtime=DistributedRuntime(rank, rank, 2, torch.device("cpu")),
                )
                assert isinstance(trainer.training_model, DDP)
                assert trainer.total_steps == trainer.lr_scheduler.total_steps == expected_steps
                assert len(trainer._get_training_dataloader()) == expected_steps
                collator = LanceBatchCollator(tokenizer, "text")
                optimizer_step = trainer.optimizer.step
                reference_step = 0

                def check_global_gradients_and_step(
                    trainer: Trainer = trainer,
                    dataset_size: int = dataset_size,
                    collator: LanceBatchCollator = collator,
                    optimizer_step: Callable[[], float | None] = optimizer_step,
                ) -> None:
                    nonlocal reference_step
                    reference = copy.deepcopy(trainer.model)
                    # Rows are identical, so shuffling cannot change this reference.
                    rows = [{"text": "a b c"}] * min(4, dataset_size - reference_step * 4)
                    batch = collator.collate_fn(rows)
                    logits = reference(batch)
                    F.cross_entropy(logits.reshape(-1, 32), batch["labels"].reshape(-1)).backward()
                    torch.nn.utils.clip_grad_norm_(reference.parameters(), trainer.max_norm)
                    for actual, expected in zip(
                        trainer.model.parameters(), reference.parameters(), strict=True
                    ):
                        torch.testing.assert_close(actual.grad, expected.grad, atol=2e-6, rtol=2e-5)
                    optimizer_step()
                    reference_step += 1

                with patch.object(trainer.optimizer, "step", check_global_gradients_and_step):
                    trainer.train()
                parameters = torch.nn.utils.parameters_to_vector(
                    trainer.model.parameters()
                ).detach()
                replicas = [torch.empty_like(parameters) for _ in range(2)]
                dist.all_gather(replicas, parameters)
                torch.testing.assert_close(replicas[0], replicas[1], atol=0, rtol=0)
                assert trainer.state.step.item() == expected_steps
                assert trainer.lr_scheduler.completed_steps == expected_steps
                assert trainer.lr_scheduler.get_last_lr() == pytest.approx([0.001])
                assert trainer.state.total_tokens.item() == dataset_size * 2
                assert trainer.state.window_steps.item() == 0
                if rank == 0:
                    calls = init_run.return_value.log.call_args_list
                    assert len(calls) == 1
                    assert calls[0].args[0]["global_step"] == expected_steps
                    assert calls[0].args[0]["train/tokens"] == dataset_size * 2
                else:
                    init_run.assert_not_called()
    finally:
        dist.destroy_process_group()


def test_distributed_trainer_step_budget_and_final_log(
    tmp_path: Path, config: Olmo2Config, tokenizer: TokenizersBackend
) -> None:
    mp.spawn(
        _check_distributed_step_budget,
        args=(tmp_path, config, tokenizer),
        nprocs=2,
        join=True,
    )
