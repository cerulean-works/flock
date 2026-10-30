import copy
from dataclasses import dataclass, field
from typing import cast

import lance
import pyarrow as pa
import pytest
import torch
from lance.torch.data import SafeLanceDataset
from torch import nn
from torch.nn import functional as F
from torch.utils.data import DataLoader

from flock.training.core.data_utils import LanceBatchCollator
from flock.training.core.lr_scheduler import CosineConfig
from flock.training.core.models.olmo2 import Olmo2
from flock.training.core.optimization import AdamWConfig, MuonConfig
from flock.training.core.trainer import Trainer, TrainerConfig


@dataclass
class RecordingRun:
    definitions: list = field(default_factory=list)
    metrics: list = field(default_factory=list)

    def define_metric(self, *args, **kwargs):
        self.definitions.append((args, kwargs))

    def log(self, metrics):
        self.metrics.append(dict(metrics))


@pytest.fixture
def trainer_factory(config, tokenizer, monkeypatch):
    # These tests must remain on CPU even when invoked on a developer GPU machine.
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(torch.mps, "is_available", lambda: False)

    def make(dataset=None, logging_steps=1, num_workers=1, prefetch_factor=1):
        run = RecordingRun()
        module_logs = []
        monkeypatch.setattr("flock.training.core.trainer.wandb.init", lambda **kwargs: run)
        # Keep this separate from run.log: routing the tail window through the global
        # W&B run is a regression, rather than something the fake should conceal.
        monkeypatch.setattr("flock.training.core.trainer.wandb.log", module_logs.append)
        if dataset is None:
            dataset = [
                {"text": "a b c d"},
                {"text": "b c"},
                {"text": "c d a"},
                {"text": "a b c"},
                {"text": "d a"},
            ]
        trainer = Trainer(
            config=TrainerConfig(
                input_column="text",
                batch_size=2,
                optimizer_config=AdamWConfig(lr=0.01, betas=(0.8, 0.9)),
                lr_scheduler_config=CosineConfig(warmup_steps=0, min_lr_ratio=0.1),
                dataloader_num_workers=num_workers,
                dataloader_prefetch_factor=prefetch_factor,
                wandb_project="offline-test",
                wandb_mode="disabled",
                max_norm=0.01,
                logging_steps=logging_steps,
            ),
            model=Olmo2(config),
            tokenizer=tokenizer,
            # A local list deliberately supplies the same map-style Dataset protocol.
            dataset=cast(SafeLanceDataset, dataset),
        )
        return trainer, run, module_logs

    return make


def test_trainer_initializes_schedule_and_metric_step(trainer_factory):
    trainer, run, _ = trainer_factory()
    assert trainer.device == torch.device("cpu")
    assert trainer.total_steps == 3  # Five rows include a final partial batch.
    assert trainer.lr_scheduler.total_steps == 3
    assert run.definitions == [
        (("global_step",), {}),
        (("train/*",), {"step_metric": "global_step"}),
    ]


def test_adamw_forwards_configuration_and_matches_torch_updates():
    trainer = object.__new__(Trainer)
    trainer.model = nn.Linear(3, 2)
    reference = copy.deepcopy(trainer.model)
    optimizer = trainer._get_optimizer(AdamWConfig(lr=0.02, betas=(0.7, 0.95)))
    expected_optimizer = torch.optim.AdamW(reference.parameters(), lr=0.02, betas=(0.7, 0.95))
    assert type(optimizer) is torch.optim.AdamW
    assert optimizer.defaults == expected_optimizer.defaults
    x = torch.tensor([[1.0, -2.0, 3.0], [0.5, 1.0, -1.5]])
    for _ in range(2):
        optimizer.zero_grad()
        expected_optimizer.zero_grad()
        trainer.model(x).square().mean().backward()
        reference(x).square().mean().backward()
        optimizer.step()
        expected_optimizer.step()
    for actual, expected in zip(trainer.model.parameters(), reference.parameters(), strict=True):
        torch.testing.assert_close(actual, expected, atol=0, rtol=0)


def test_muon_remains_explicitly_unsupported():
    trainer = object.__new__(Trainer)
    trainer.model = nn.Linear(3, 2)
    with pytest.raises(NotImplementedError, match="Muon"):
        trainer._get_optimizer(MuonConfig(lr=0.01, momentum=0.9))


@pytest.mark.parametrize("dtype", [torch.float32, torch.float16, torch.bfloat16])
def test_loss_and_gradients_match_cross_entropy_over_valid_targets(dtype):
    # Noncontiguous logits check reshape rather than a contiguous-only view path.
    logits = (
        torch.linspace(-2, 2, 30, dtype=dtype)
        .reshape(2, 5, 3)
        .transpose(1, 2)
        .detach()
        .requires_grad_()
    )
    reference = logits.detach().clone().requires_grad_()
    labels = torch.tensor([[1, 2, -100], [-100, 4, 0]])
    valid = labels != -100
    loss, count = Trainer._calc_batch_loss(object.__new__(Trainer), logits, labels)
    expected = F.cross_entropy(reference.float()[valid], labels[valid])
    assert count.item() == 4
    assert loss.dtype == torch.float32
    torch.testing.assert_close(loss, expected)
    loss.backward()
    expected.backward()
    torch.testing.assert_close(logits.grad, reference.grad)
    assert logits.grad is not None
    assert torch.equal(logits.grad[~valid], torch.zeros_like(logits.grad[~valid]))


@pytest.mark.parametrize("dtype", [torch.float32, torch.float16, torch.bfloat16])
def test_all_ignored_targets_do_not_produce_nan_loss(dtype):
    logits = torch.linspace(-2, 2, 30, dtype=dtype).reshape(2, 3, 5).requires_grad_()
    labels = torch.full((2, 3), -100, dtype=torch.long)
    loss, count = Trainer._calc_batch_loss(object.__new__(Trainer), logits, labels)
    assert count.item() == 0
    assert torch.isfinite(loss), (
        "a batch with no training targets must not poison training with NaN"
    )
    assert loss.dtype == torch.float32
    assert loss.item() == 0.0
    loss.backward()
    assert logits.grad is not None
    torch.testing.assert_close(logits.grad, torch.zeros_like(logits), atol=0, rtol=0)


@pytest.mark.parametrize("logging_steps, expected_steps", [(1, [1, 2, 3]), (2, [2, 3]), (5, [3])])
def test_all_ignored_logging_windows_remain_finite(
    trainer_factory, monkeypatch, logging_steps, expected_steps
):
    trainer, run, _ = trainer_factory(logging_steps=logging_steps)
    batch = {
        "input_ids": torch.tensor([[2, 3], [4, 5]]),
        "attention_mask": torch.ones(2, 2, dtype=torch.bool),
        "labels": torch.full((2, 2), -100, dtype=torch.long),
    }
    monkeypatch.setattr(trainer, "_get_training_dataloader", lambda: [batch] * 3)
    trainer.train()
    assert [metrics["global_step"] for metrics in run.metrics] == expected_steps
    for metrics in run.metrics:
        assert metrics == {
            "global_step": metrics["global_step"],
            "train/loss_mean": 0.0,
            "train/loss_window_max": 0.0,
            "train/grad_norm_mean": 0.0,
            "train/grad_norm_window_max": 0.0,
        }


def reference_epoch(model, batches, max_norm):
    """Independent three-update loop: explicit LR values and gradient clipping math."""
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.01, betas=(0.8, 0.9))
    records = []
    for batch, factor in zip(batches, [1.0, 0.775, 0.325], strict=True):
        optimizer.zero_grad()
        valid = batch["labels"] != -100
        logits = model(batch)
        loss = F.cross_entropy(logits[valid], batch["labels"][valid])
        loss.backward()
        norm = torch.sqrt(sum(p.grad.square().sum() for p in model.parameters()))
        assert norm.item() > max_norm, "fixture must exercise actual gradient clipping"
        scale = (max_norm / (norm + 1e-6)).clamp(max=1)
        for parameter in model.parameters():
            parameter.grad.mul_(scale)
        optimizer.param_groups[0]["lr"] = 0.01 * factor
        optimizer.step()
        records.append((loss.item(), valid.sum().item(), norm.item()))
    return records


@pytest.mark.parametrize("logging_steps", [1, 2, 5])
def test_training_updates_and_token_weighted_logging(trainer_factory, monkeypatch, logging_steps):
    trainer, run, module_logs = trainer_factory(logging_steps=logging_steps)
    collator = LanceBatchCollator(trainer.tokenizer, trainer.ds_input_column)
    # Stable input order isolates trainer math from shuffle implementation details.
    loader = DataLoader(trainer.dataset, batch_size=2, collate_fn=collator.collate_fn)
    batches = list(loader)
    reference = copy.deepcopy(trainer.model)
    records = reference_epoch(reference, batches, trainer.max_norm)
    trainer.model.eval()
    monkeypatch.setattr(trainer, "_get_training_dataloader", lambda: loader)
    trainer.train()
    assert trainer.model.training
    assert trainer.lr_scheduler.completed_steps == 3
    assert trainer.lr_scheduler.get_last_lr() == pytest.approx([0.001])
    for name, parameter in trainer.model.named_parameters():
        torch.testing.assert_close(
            parameter,
            reference.get_parameter(name),
            atol=2e-6,
            rtol=2e-5,
            msg=lambda error, name=name: f"{name}: {error}",
        )
    expected_logs = []
    for start in range(0, len(records), logging_steps):
        window = records[start : start + logging_steps]
        expected_logs.append(
            {
                "global_step": start + len(window),
                "train/loss_mean": sum(loss * tokens for loss, tokens, _ in window)
                / sum(tokens for _, tokens, _ in window),
                "train/loss_window_max": max(loss for loss, _, _ in window),
                "train/grad_norm_mean": sum(norm for _, _, norm in window) / len(window),
                "train/grad_norm_window_max": max(norm for _, _, norm in window),
            }
        )
    # Incomplete windows must use this trainer's run just like full windows.
    assert module_logs == [], "tail metrics must be sent to trainer.run.log"
    assert len(run.metrics) == len(expected_logs)
    for actual, expected in zip(run.metrics, expected_logs, strict=True):
        assert actual == pytest.approx(expected, abs=2e-5, rel=2e-5)


def test_training_loader_supports_zero_workers(trainer_factory):
    trainer, _, _ = trainer_factory(num_workers=0, prefetch_factor=None)
    loader = trainer._get_training_dataloader()
    assert loader.num_workers == 0
    assert loader.prefetch_factor is None
    assert loader.multiprocessing_context is None
    assert not loader.persistent_workers
    assert len(list(loader)) == trainer.total_steps


@pytest.mark.parametrize("prefetch_factor", [1, 2])
def test_zero_workers_reject_explicit_prefetch(trainer_factory, prefetch_factor):
    trainer, _, _ = trainer_factory(num_workers=0, prefetch_factor=prefetch_factor)
    with pytest.raises(ValueError, match="must be None"):
        trainer._get_training_dataloader()


def test_worker_prefetch_can_use_pytorch_default(trainer_factory):
    trainer, _, _ = trainer_factory(num_workers=1, prefetch_factor=None)
    loader = trainer._get_training_dataloader()
    assert loader.prefetch_factor == 2
    assert loader.multiprocessing_context.get_start_method() == "spawn"
    assert loader.persistent_workers


def test_empty_dataset_is_rejected_without_an_optimizer_update(trainer_factory, monkeypatch):
    trainer, run, _ = trainer_factory(dataset=[])
    updates = []
    monkeypatch.setattr(trainer.optimizer, "step", lambda: updates.append(True))
    with pytest.raises(ValueError):
        trainer.train()
    assert updates == []
    assert run.metrics == []


def test_real_spawned_loader_reads_temporary_lance_dataset(trainer_factory, tmp_path):
    path = tmp_path / "training.lance"
    lance.write_dataset(pa.table({"text": ["a b c", "b c d", "c d a"]}), str(path))
    dataset = SafeLanceDataset(str(path))
    trainer, _, _ = trainer_factory(dataset=dataset, num_workers=1)
    loader = trainer._get_training_dataloader()
    assert loader.multiprocessing_context.get_start_method() == "spawn"
    assert loader.persistent_workers
    assert loader.prefetch_factor == 1
    loader.timeout = 120  # Bound a worker failure; allow startup on busy CI hosts.
    iterator = iter(loader)
    try:
        batches = list(iterator)
    finally:
        iterator._shutdown_workers()
    assert len(batches) == trainer.total_steps == 2
    assert sorted(row for batch in batches for row in batch["labels"].tolist()) == [
        [3, 4],
        [4, 5],
        [5, 2],
    ]
