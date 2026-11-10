import copy
import json
import math
import os
import random
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import cast

import lance
import pyarrow as pa
import pytest
import torch
from lance.torch.data import SafeLanceDataset
from pydantic import ValidationError
from tokenizers.models import WordLevel
from torch import nn
from torch.nn import functional as F
from torch.utils.data import DataLoader

from flock.eval.tasks import TASKS, Choice, Dataset, MCDoc, MultipleChoiceTask, PerplexityTask
from flock.hub.models import upload as hub_upload
from flock.hub.models.files import write_training_state
from flock.training.core import trainer as trainer_module
from flock.training.core.checkpoint import TrainerCheckpoint
from flock.training.core.data_utils import LanceBatchCollator
from flock.training.core.lr_scheduler import CosineConfig, WSDConfig
from flock.training.core.models import BaseModel
from flock.training.core.models.olmo2 import Olmo2
from flock.training.core.optimization import AdamWConfig, MuonConfig
from flock.training.core.trainer import Trainer, TrainerConfig


@dataclass
class RecordingRun:
    entity: str = "flock"
    project: str = "pretrain"
    id: str = "run123"
    definitions: list = field(default_factory=list)
    metrics: list = field(default_factory=list)

    def define_metric(self, *args, **kwargs):
        self.definitions.append((args, kwargs))

    def log(self, metrics):
        self.metrics.append(dict(metrics))


@pytest.fixture
def trainer_factory(config, tokenizer, monkeypatch, tmp_path):
    # These tests must remain on CPU even when invoked on a developer GPU machine.
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)

    def make(dataset=None, logging_steps=1, num_workers=1, prefetch_factor=1, **config_overrides):
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
            config=TrainerConfig.model_validate(
                {
                    "run_name": "test-run",
                    "input_column": "text",
                    "batch_size": 2,
                    "optimizer_config": AdamWConfig(lr=0.01, betas=(0.8, 0.9)),
                    "lr_scheduler_config": CosineConfig(warmup_steps=0, min_lr_ratio=0.1),
                    "dataloader_num_workers": num_workers,
                    "dataloader_prefetch_factor": prefetch_factor,
                    "wandb_project": "offline-test",
                    "wandb_mode": "disabled",
                    "output_dir": str(tmp_path / "checkpoints"),
                    "max_norm": 0.01,
                    "logging_steps": logging_steps,
                    **config_overrides,
                },
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
        (("eval/*",), {"step_metric": "global_step"}),
    ]


@pytest.fixture
def validation_tasks(monkeypatch):
    """Real task/scorer paths with local rows and the training fixture's tokenizer."""
    text_task = PerplexityTask(
        "test_trainer_text",
        Dataset("offline/trainer", "0" * 40, None, "text"),
        window=8,
    )
    choice_task = MultipleChoiceTask(
        name="test_trainer_choices",
        dataset=Dataset("offline/trainer", "0" * 40, None, "choices"),
        to_doc=lambda row: MCDoc(row["id"], (Choice("a", " b"), Choice("a", " c")), 0),
        primary_metric="acc_raw",
        metrics=("acc_raw", "bpb"),
        unconditional_context=None,
    )
    rows = {
        "text": [{"id": "long", "text": "a b c d"}, {"id": "short", "text": "b c"}],
        "choices": [{"id": "q"}],
    }

    def load(dataset, split=None):
        assert dataset.repo == "offline/trainer", "validation must not load external data"
        return rows[split or dataset.split]

    monkeypatch.setattr(Dataset, "load", load)
    tasks = [text_task, choice_task]
    for task in tasks:
        monkeypatch.setitem(TASKS, task.name, task)
    return tasks


@pytest.mark.parametrize(
    "interval, expected_steps",
    [(None, []), (1, [1, 2, 3]), (2, [2, 3]), (3, [3]), (5, [3])],
)
def test_validation_schedule_and_training_mode(
    trainer_factory, validation_tasks, monkeypatch, interval, expected_steps
):
    trainer, _, _ = trainer_factory(
        num_workers=0,
        prefetch_factor=None,
        validation_tasks=[task.name for task in validation_tasks],
        validation_steps=interval,
    )
    calls, forward_modes = [], []

    def evaluate():
        calls.append(int(trainer.state.step.item()))
        trainer.model.eval()  # The training loop owns restoring training mode.

    monkeypatch.setattr(trainer, "evaluate", evaluate)
    hook = trainer.model.register_forward_pre_hook(
        lambda model, args: forward_modes.append(model.training)
    )
    try:
        trainer.train()
    finally:
        hook.remove()
    assert calls == expected_steps
    assert forward_modes == [True, True, True]
    assert trainer.model.training
    assert trainer.lr_scheduler.completed_steps == 3


def test_evaluation_scores_real_tasks_and_logs_at_training_step(
    trainer_factory, validation_tasks, monkeypatch
):
    trainer, run, module_logs = trainer_factory(
        num_workers=0,
        prefetch_factor=None,
        validation_tasks=[task.name for task in validation_tasks],
    )
    trainer.state.step.fill_(2)
    # Compute expectations directly from causal logits, without the eval scorer/metrics.
    reference = copy.deepcopy(trainer.model).eval()
    with torch.no_grad():
        total_nll = 0.0
        for inputs, targets in [([1, 2, 3, 4], [2, 3, 4, 5]), ([1, 3], [3, 4])]:
            ids = torch.tensor([inputs])  # EOS=1 supplies the first token's context.
            logits = reference(
                {"input_ids": ids, "attention_mask": torch.ones_like(ids), "labels": ids}
            )
            total_nll += F.cross_entropy(
                logits[0].float(), torch.tensor(targets), reduction="sum"
            ).item()
        ids = torch.tensor([[2]])  # Both choices have context "a".
        logits = reference(
            {"input_ids": ids, "attention_mask": torch.ones_like(ids), "labels": ids}
        )
        logprob = logits[0, 0].float().log_softmax(-1)
        expected_accuracy = float(logprob[3] >= logprob[4])
        expected_choice_bpb = -logprob[3].item() / (len(b" b") * math.log(2))

    # Evaluation must leave existing gradients, weights, counters and RNG state alone.
    for parameter in trainer.model.parameters():
        parameter.grad = torch.full_like(parameter, 0.125)
    weights = copy.deepcopy(trainer.model.state_dict())
    state = copy.deepcopy(trainer.get_trainer_state())
    calls = []
    score_task = trainer_module.score_task

    def score(scorer, task):
        assert not torch.is_grad_enabled()
        assert not trainer.model.training
        assert scorer.model.model is trainer.model
        assert scorer.tokenizer is trainer.tokenizer
        assert scorer.name == "test-run@step-2"
        calls.append(task)
        return score_task(scorer, task)

    monkeypatch.setattr(trainer_module, "score_task", score)
    trainer.evaluate()

    assert calls == validation_tasks
    assert module_logs == []
    assert len(run.metrics) == 1
    assert run.metrics[0] == pytest.approx(
        {
            "global_step": 2,
            "eval/test_trainer_text/bits_per_byte": total_nll
            / (len(b"a b c d") + len(b"b c"))
            / math.log(2),
            "eval/test_trainer_text/loss_per_token": total_nll / 6,
            "eval/test_trainer_text/perplexity": math.exp(total_nll / 6),
            "eval/test_trainer_choices/acc_raw": expected_accuracy,
            "eval/test_trainer_choices/bpb": expected_choice_bpb,
        },
        rel=1e-6,
        abs=1e-6,
    )
    assert_nested_equal(trainer.model.state_dict(), weights)
    assert_nested_equal(trainer.get_trainer_state(), state)
    for parameter in trainer.model.parameters():
        torch.testing.assert_close(parameter.grad, torch.full_like(parameter, 0.125))


@pytest.mark.parametrize(
    "error_type, failed_task",
    [(None, None), (KeyError, 0), (KeyError, 1), (FileExistsError, 0), (FileExistsError, 1)],
)
def test_validation_preserves_training_updates(
    trainer_factory, validation_tasks, monkeypatch, tmp_path, caplog, error_type, failed_task
):
    reference, reference_run, _ = trainer_factory(num_workers=0, prefetch_factor=None)
    reference.train()
    expected_state = copy.deepcopy(reference.get_trainer_state())
    trainer, run, _ = trainer_factory(
        num_workers=0,
        prefetch_factor=None,
        output_dir=str(tmp_path / "validation-errors"),
        validation_tasks=[task.name for task in validation_tasks],
        validation_steps=1,
    )
    calls = []
    score_task = trainer_module.score_task

    def score(scorer, task):
        assert not trainer.model.training
        calls.append((int(trainer.state.step.item()), task.name))
        if error_type is not None and task is validation_tasks[failed_task]:
            raise error_type("validation fixture failure")
        return score_task(scorer, task)

    monkeypatch.setattr(trainer_module, "score_task", score)
    trainer.train()

    scored_tasks = validation_tasks if error_type is None else validation_tasks[: failed_task + 1]
    assert calls == [(step, task.name) for step in [1, 2, 3] for task in scored_tasks]
    if error_type is not None:
        assert caplog.text.count("Error during evaluation, skipping:") == 3
        assert "validation fixture failure" in caplog.text
    eval_logs = [metrics for metrics in run.metrics if any(k.startswith("eval/") for k in metrics)]
    assert [metrics["global_step"] for metrics in eval_logs] == (
        [1, 2, 3] if error_type is None else []
    )
    assert trainer.model.training
    assert [metrics for metrics in run.metrics if "train/loss_mean" in metrics] == (
        reference_run.metrics
    )
    assert_nested_equal(trainer.model.state_dict(), reference.model.state_dict())
    assert_nested_equal(trainer.get_trainer_state(), expected_state)


def test_unexpected_validation_error_is_not_silenced(
    trainer_factory, validation_tasks, monkeypatch
):
    trainer, run, _ = trainer_factory(
        num_workers=0,
        prefetch_factor=None,
        validation_tasks=[task.name for task in validation_tasks],
        validation_steps=1,
    )

    def score(scorer, task):
        raise ValueError("invalid validation window")

    monkeypatch.setattr(trainer_module, "score_task", score)
    with pytest.raises(ValueError, match="invalid validation window"):
        trainer.train()
    assert trainer.state.step.item() == trainer.lr_scheduler.completed_steps == 1
    assert all(not key.startswith("eval/") for metrics in run.metrics for key in metrics)


def test_local_checkpoint_records_run_identity(trainer_factory):
    trainer, _, _ = trainer_factory(num_workers=0, prefetch_factor=None)
    trainer.train()
    trainer.save_model()
    metadata = json.loads((trainer.output_dir / "checkpoint-3" / "flock.json").read_text())
    assert metadata["run"] == "flock/pretrain/run123"
    assert metadata["step"] == trainer.state.step.item() == 3


def assert_nested_equal(actual, expected):
    if isinstance(expected, torch.Tensor):
        torch.testing.assert_close(actual, expected, atol=0, rtol=0)
    elif isinstance(expected, dict):
        assert actual.keys() == expected.keys()
        for key in expected:
            assert_nested_equal(actual[key], expected[key])
    elif isinstance(expected, (list, tuple)):
        assert type(actual) is type(expected)
        assert len(actual) == len(expected)
        for left, right in zip(actual, expected, strict=True):
            assert_nested_equal(left, right)
    else:
        assert actual == expected


def restore_checkpoint_state(trainer, checkpoint, state, load_via):
    if load_via == "checkpoint":
        write_training_state(state, checkpoint)
        trainer.load_checkpoint(checkpoint)
    else:
        if load_via == "validated-state":
            state = TrainerCheckpoint.model_validate(state)
        trainer.load_trainer_state(state)


@pytest.fixture
def forbid_checkpoint_loads(monkeypatch):
    @contextmanager
    def forbid(trainer):
        def unexpected_load(*args, **kwargs):
            pytest.fail("Incompatible settings must be rejected before any checkpoint state loads")

        with monkeypatch.context() as patches:
            for owner, name in (
                (trainer.model, "load_state_dict"),
                (trainer.state, "load_state_dict"),
                (trainer.optimizer, "load_state_dict"),
                (trainer.lr_scheduler, "load_state_dict"),
                (torch, "set_rng_state"),
                (torch.cuda, "set_rng_state_all"),
                (random, "setstate"),
            ):
                patches.setattr(owner, name, unexpected_load)
            patches.setattr("flock.training.core.trainer.load_model", unexpected_load)
            yield

    return forbid


@pytest.fixture
def trained_trainer(trainer_factory):
    trainer, _, _ = trainer_factory(num_workers=0, prefetch_factor=None)
    trainer.train()
    return trainer


@pytest.fixture
def simulated_cuda_rng(monkeypatch):
    """Exercise CUDA RNG save/restore routing with independent CPU generators.

    These tests never initialize CUDA and do not establish CUDA kernel determinism.
    """

    def install(count):
        generators = [torch.Generator().manual_seed(100 + index) for index in range(count)]
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        monkeypatch.setattr(torch.cuda, "device_count", lambda: len(generators))
        monkeypatch.setattr(
            torch.cuda, "get_rng_state_all", lambda: [g.get_state() for g in generators]
        )
        # Keep the real set_rng_state_all loop, but restore into CPU generators.
        monkeypatch.setattr(
            "torch.cuda.random.set_rng_state",
            lambda state, device: generators[device].set_state(state),
        )
        return generators

    return install


@pytest.mark.parametrize("resume_latest", [True, False])
@pytest.mark.parametrize("split_step", [0, 1, 2, 3])
@pytest.mark.parametrize(
    "schedule",
    [
        CosineConfig(warmup_steps=1, min_lr_ratio=0.1),
        WSDConfig(warmup_steps=1, decay_steps=1, min_lr_ratio=0.1),
    ],
    ids=["cosine", "wsd"],
)
def test_checkpoint_resume_matches_uninterrupted_training(
    trainer_factory, monkeypatch, tmp_path, resume_latest, split_step, schedule
):
    def make(output_dir, **overrides):
        trainer, run, _ = trainer_factory(
            num_workers=0,
            prefetch_factor=None,
            logging_steps=2,
            output_dir=str(output_dir),
            save_steps=1,
            lr_scheduler_config=schedule,
            **overrides,
        )
        forward = trainer.model._forward
        seen_batches = []

        def stochastic_forward(*args):
            # Exercise restoration of both Python and torch RNGs, including the
            # extra DataLoader iterator created when a process resumes.
            seen_batches.append(args[0].detach().clone())
            return F.dropout(forward(*args), p=0.25, training=True) * (0.8 + random.random())

        monkeypatch.setattr(trainer.model, "_forward", stochastic_forward)
        return trainer, run, seen_batches

    reference, expected_run, expected_batches = make(tmp_path / "reference")
    reference.train()
    expected_state = copy.deepcopy(reference.get_trainer_state())
    expected_random = (random.random(), torch.rand(4))

    interrupted, first_run, first_batches = make(tmp_path / "resumed")
    save = interrupted.save_model

    def save_and_interrupt():
        save()
        if interrupted.state.step.item() == split_step:
            raise InterruptedError("simulate process exit after a completed checkpoint")

    if split_step == 0:
        interrupted._set_seed()
        interrupted.save_model()
    else:
        monkeypatch.setattr(interrupted, "save_model", save_and_interrupt)
        with pytest.raises(InterruptedError):
            interrupted.train()

    resumed, resumed_run, resumed_batches = make(interrupted.output_dir)
    resumed.save_steps = 2  # Cadence must use absolute steps after the restart.
    with torch.no_grad():
        for parameter in resumed.model.parameters():
            parameter.add_(10)  # The loader must restore weights, not only optimizer state.
    random.seed(1234)
    torch.manual_seed(5678)
    checkpoint = True if resume_latest else interrupted.output_dir / f"checkpoint-{split_step}"
    resumed.train(resume_from_checkpoint=checkpoint)

    assert_nested_equal(resumed.model.state_dict(), reference.model.state_dict())
    assert_nested_equal(resumed.get_trainer_state(), expected_state)
    assert first_run.metrics + resumed_run.metrics == expected_run.metrics
    assert_nested_equal(first_batches + resumed_batches, expected_batches)
    assert random.random() == expected_random[0]
    torch.testing.assert_close(torch.rand(4), expected_random[1], atol=0, rtol=0)
    expected_steps = list(range(1, split_step + 1)) if split_step else [0]
    expected_steps += [step for step in (2, 3) if step > split_step]
    assert [path.name for path in resumed._checkpoints()] == [
        f"checkpoint-{step}" for step in expected_steps
    ]
    # Resuming an already completed run must perform no further updates or saves.
    resumed.train(resume_from_checkpoint=True)
    assert resumed.lr_scheduler.completed_steps == 3
    assert first_run.metrics + resumed_run.metrics == expected_run.metrics


def test_checkpoint_retention_removes_nested_state_and_orders_by_step(trainer_factory):
    trainer, _, _ = trainer_factory(
        dataset=[{"text": "a b c"}] * 20,
        num_workers=0,
        prefetch_factor=None,
        save_steps=1,
        save_total_limit=2,
        overwrite_output_dir=True,
    )
    unrelated = trainer.output_dir / "checkpoint-not-a-step"
    unrelated.mkdir()
    trainer.train()
    assert [path.name for path in trainer._checkpoints()] == ["checkpoint-9", "checkpoint-10"]
    assert unrelated.is_dir()
    assert (
        trainer.output_dir / "checkpoint-10/training_state/optimizer_state_dict.safetensors"
    ).is_file()


def test_checkpoint_upload_uses_complete_local_folder(trainer_factory, monkeypatch):
    trainer, run, _ = trainer_factory(save_to_hub_repo="cerulean/test-model")
    uploads = []

    def upload(folder, name, **options):
        assert (folder / "model.safetensors").is_file()
        assert (folder / "training_state/python_rng_state.safetensors").is_file()
        uploads.append((folder, name, options))
        pending = Future()
        pending.set_result(None)
        return pending

    monkeypatch.setattr("flock.training.core.trainer.save_to_hub", upload)
    trainer.save_model()
    assert uploads == [
        (
            trainer.output_dir / "checkpoint-0",
            "cerulean/test-model",
            {"run": run, "step": 0, "tags": ["checkpoint-0"], "blocking": False},
        )
    ]
    assert uploads[0][0].is_dir()
    trainer.save_model(blocking=True)


def test_failed_checkpoint_write_does_not_publish_or_prune(trainer_factory, monkeypatch):
    trainer, _, _ = trainer_factory(
        num_workers=0,
        prefetch_factor=None,
        save_steps=1,
        save_total_limit=1,
        overwrite_output_dir=True,
    )
    trainer.save_model()

    def fail_write(model, folder, **kwargs):
        folder.mkdir()
        (folder / "flock.json").write_text("partial")
        raise OSError("disk full")

    monkeypatch.setattr("flock.training.core.trainer.write_model", fail_write)
    with pytest.raises(OSError, match="disk full"):
        trainer.train()
    assert [path.name for path in trainer.output_dir.iterdir()] == ["checkpoint-0"]


def test_resume_rejects_missing_state_and_incompatible_data(trainer_factory):
    trainer, _, _ = trainer_factory(num_workers=0, prefetch_factor=None)
    with pytest.raises(FileNotFoundError, match="No checkpoints"):
        trainer.train(resume_from_checkpoint=True)
    trainer.save_model()
    checkpoint = trainer.output_dir / "checkpoint-0"
    trainer.seed += 1
    with pytest.raises(ValueError, match="seed"):
        trainer.load_checkpoint(checkpoint)
    trainer.seed -= 1
    for path in (checkpoint / "training_state").iterdir():
        path.unlink()
    with pytest.raises(ValueError, match="no training state"):
        trainer.load_checkpoint(checkpoint)


def test_existing_output_requires_resume_or_overwrite(trainer_factory):
    trainer, _, _ = trainer_factory(num_workers=0, prefetch_factor=None)
    trainer.save_model()
    with pytest.raises(RuntimeError, match="Resume"):
        trainer.train()
    with pytest.raises(FileExistsError, match="already exists"):
        trainer.save_model()
    trainer.overwrite_output_dir = True
    trainer.save_model()
    trainer.load_checkpoint(trainer.output_dir / "checkpoint-0")


@pytest.mark.parametrize(
    "section, field, value",
    [
        ("data_config", "batch_size", "2"),
        ("trainer_state_dict", "step", torch.tensor(0.5)),
        ("trainer_state_dict", "step", torch.tensor([0])),
        ("trainer_state_dict", "step", torch.tensor(-1)),
        ("lr_scheduler_state_dict", "completed_steps", "0"),
        ("lr_scheduler_state_dict", "completed_steps", 1),
        ("lr_scheduler_state_dict", "total_steps", 4),
    ],
)
def test_checkpoint_schema_rejects_invalid_state_without_mutating_trainer(
    trainer_factory, section, field, value
):
    trainer, _, _ = trainer_factory()
    expected = copy.deepcopy(trainer.get_trainer_state())
    invalid = copy.deepcopy(expected)
    invalid[section][field] = value
    with pytest.raises(ValidationError):
        trainer.load_trainer_state(invalid)
    assert_nested_equal(trainer.get_trainer_state(), expected)


def test_checkpoint_schema_reports_missing_fields(trainer_factory):
    trainer, _, _ = trainer_factory()
    invalid = trainer.get_trainer_state()
    del invalid["trainer_state_dict"]["step"]
    with pytest.raises(ValidationError, match="trainer_state_dict.step"):
        trainer.load_trainer_state(invalid)


@pytest.mark.parametrize("shard_size", [2048, 5 * 2**30])
def test_checkpoint_preserves_tied_parameters_and_optimizer_references(
    trainer_factory, monkeypatch, shard_size
):
    from flock.hub.models.files import write_model

    trainer, _, _ = trainer_factory(num_workers=0, prefetch_factor=None, save_steps=3)
    trainer.model.lm_head.weight = trainer.model.model.embed_tokens.weight
    trainer.optimizer = trainer._get_optimizer(AdamWConfig(lr=0.01, betas=(0.8, 0.9)))
    trainer.lr_scheduler = trainer._get_lr_scheduler(
        CosineConfig(warmup_steps=0, min_lr_ratio=0.1), trainer.total_steps
    )

    def write_shards(model, folder, **options):
        options["shard_size"] = shard_size
        write_model(model, folder, **options)

    monkeypatch.setattr("flock.training.core.trainer.write_model", write_shards)
    trainer.train()
    checkpoint = trainer.output_dir / "checkpoint-3"
    assert (checkpoint / "model.safetensors.index.json").is_file() == (shard_size == 2048)
    weights = copy.deepcopy(trainer.model.state_dict())
    state = copy.deepcopy(trainer.get_trainer_state())
    parameters = [id(p) for p in trainer.model.parameters()]
    with torch.no_grad():
        for parameter in trainer.model.parameters():
            parameter.zero_()
    trainer.optimizer.state.clear()
    trainer.load_checkpoint(checkpoint)
    assert trainer.model.lm_head.weight is trainer.model.model.embed_tokens.weight
    assert [id(p) for p in trainer.model.parameters()] == parameters
    assert [id(p) for p in trainer.optimizer.param_groups[0]["params"]] == parameters
    assert_nested_equal(trainer.model.state_dict(), weights)
    assert_nested_equal(trainer.get_trainer_state(), state)


@pytest.mark.parametrize("corruption", ["missing", "unexpected"])
def test_sharded_checkpoint_rejects_missing_or_unexpected_weights(
    trainer_factory, monkeypatch, corruption
):
    from safetensors.torch import load_file, save_file

    from flock.hub.models.files import weight_files, write_model

    trainer, _, _ = trainer_factory()

    def write_shards(model, folder, **options):
        options["shard_size"] = 2048
        write_model(model, folder, **options)

    monkeypatch.setattr("flock.training.core.trainer.write_model", write_shards)
    trainer.save_model()
    checkpoint = trainer.output_dir / "checkpoint-0"
    shard = weight_files(checkpoint)[0]
    tensors = load_file(shard)
    if corruption == "missing":
        key = next(iter(tensors))
        del tensors[key]
    else:
        key = "unknown.weight"
        tensors[key] = torch.zeros(1)
    save_file(tensors, shard)
    with pytest.raises(RuntimeError, match=key):
        trainer.load_checkpoint(checkpoint)


def test_saved_checkpoints_remain_independent_of_later_updates(
    trainer_factory, monkeypatch, tmp_path
):
    trainer, _, _ = trainer_factory(num_workers=0, prefetch_factor=None, save_steps=1)
    snapshots = {}
    save = trainer.save_model

    def save_and_record():
        save()
        snapshots[int(trainer.state.step.item())] = (
            copy.deepcopy(trainer.model.state_dict()),
            copy.deepcopy(trainer.get_trainer_state()),
        )

    monkeypatch.setattr(trainer, "save_model", save_and_record)
    trainer.train()
    with torch.no_grad():
        for parameter in trainer.model.parameters():
            parameter.zero_()
    trainer.state.reset_global()
    trainer.optimizer.state.clear()

    restored, _, _ = trainer_factory(output_dir=str(tmp_path / "restored"))
    for step, (weights, state) in snapshots.items():
        restored.load_checkpoint(trainer.output_dir / f"checkpoint-{step}")
        assert_nested_equal(restored.model.state_dict(), weights)
        assert_nested_equal(restored.get_trainer_state(), state)


@pytest.mark.parametrize("load_via", ["checkpoint", "trainer-state", "validated-state"])
@pytest.mark.parametrize("target_steps", [0, 1])
@pytest.mark.parametrize(
    "schedule",
    [
        CosineConfig(warmup_steps=1, min_lr_ratio=0.1),
        WSDConfig(warmup_steps=1, decay_steps=1, min_lr_ratio=0.1),
    ],
    ids=["cosine", "wsd"],
)
def test_loading_restores_progress_with_matching_settings_and_different_current_lr(
    trainer_factory, tmp_path, load_via, target_steps, schedule
):
    source, _, _ = trainer_factory(
        num_workers=0, prefetch_factor=None, lr_scheduler_config=schedule
    )
    source.train()
    source.save_model()
    state = copy.deepcopy(source.get_trainer_state())
    target, _, _ = trainer_factory(
        output_dir=str(tmp_path / "target"),
        lr_scheduler_config=schedule,
    )
    for parameter in target.model.parameters():
        parameter.grad = torch.ones_like(parameter)
    for _ in range(target_steps):
        target.optimizer.step()
        target.lr_scheduler.step()
    target.state.step.fill_(target_steps)
    # Comparing scheduled LRs directly would reject an otherwise compatible resume.
    assert (
        target.optimizer.param_groups[0]["lr"]
        != state["optimizer_state_dict"]["param_groups"][0]["lr"]
    )
    weights = copy.deepcopy(
        source.model.state_dict() if load_via == "checkpoint" else target.model.state_dict()
    )
    parameter_ids = [id(p) for p in target.model.parameters()]
    restore_checkpoint_state(target, source.output_dir / "checkpoint-3", state, load_via)
    assert [id(p) for p in target.model.parameters()] == parameter_ids
    assert [id(p) for p in target.optimizer.param_groups[0]["params"]] == parameter_ids
    assert_nested_equal(target.model.state_dict(), weights)
    assert_nested_equal(target.get_trainer_state(), state)


@pytest.mark.parametrize("load_via", ["checkpoint", "trainer-state", "validated-state"])
@pytest.mark.parametrize(
    "overrides, error",
    [
        ({"lr_scheduler_config": WSDConfig(warmup_steps=0, decay_steps=1)}, "Scheduler config"),
        (
            {"lr_scheduler_config": CosineConfig(warmup_steps=1, min_lr_ratio=0.1)},
            "Scheduler config",
        ),
        (
            {"lr_scheduler_config": CosineConfig(warmup_steps=0, min_lr_ratio=0.2)},
            "Scheduler config",
        ),
        ({"optimizer_config": AdamWConfig(lr=0.3, betas=(0.8, 0.9))}, "base_lrs"),
        ({"optimizer_config": AdamWConfig(lr=0.01, betas=(0.7, 0.99))}, "optimizer"),
    ],
    ids=["schedule-type", "warmup", "minimum-lr", "base-lr", "betas"],
)
def test_incompatible_schedule_or_optimizer_is_rejected_before_loading(
    trained_trainer, trainer_factory, tmp_path, forbid_checkpoint_loads, load_via, overrides, error
):
    source = trained_trainer
    source.save_model()
    target, _, _ = trainer_factory(output_dir=str(tmp_path / "target"), **overrides)
    # Populate optimizer buffers so a failed restore must preserve existing state too.
    for parameter in target.model.parameters():
        parameter.grad = torch.ones_like(parameter)
    target.optimizer.step()
    target.lr_scheduler.step()
    target.state.step.fill_(1)
    weights = copy.deepcopy(target.model.state_dict())
    state = copy.deepcopy(target.get_trainer_state())
    saved = copy.deepcopy(source.get_trainer_state())
    expected_saved = copy.deepcopy(saved)
    with forbid_checkpoint_loads(target), pytest.raises(ValueError, match=error):
        restore_checkpoint_state(target, source.output_dir / "checkpoint-3", saved, load_via)
    assert_nested_equal(target.model.state_dict(), weights)
    assert_nested_equal(target.get_trainer_state(), state)
    assert_nested_equal(saved, expected_saved)


@pytest.mark.parametrize("load_via", ["checkpoint", "trainer-state", "validated-state"])
@pytest.mark.parametrize(
    "change, error",
    [
        ("group-count", "parameter groups"),
        ("group-size", "group 0 size"),
        ("weight_decay", "group 0 settings"),
        ("eps", "group 0 settings"),
        ("decay_steps", "Scheduler config"),
    ],
)
def test_incompatible_optimizer_layout_or_wsd_settings_reject_before_loading(
    trainer_factory, tmp_path, forbid_checkpoint_loads, load_via, change, error
):
    schedule = WSDConfig(warmup_steps=1, decay_steps=1, min_lr_ratio=0.1)
    source, _, _ = trainer_factory(
        num_workers=0, prefetch_factor=None, lr_scheduler_config=schedule
    )
    source.train()
    source.save_model()
    saved = copy.deepcopy(source.get_trainer_state())
    groups = saved["optimizer_state_dict"]["param_groups"]
    if change == "group-count":
        groups.append(copy.deepcopy(groups[0]))
    elif change == "group-size":
        groups[0]["params"].pop()
    elif change == "decay_steps":
        saved["lr_scheduler_state_dict"]["config"]["decay_steps"] = 2
    else:
        groups[0][change] *= 2

    target, _, _ = trainer_factory(
        output_dir=str(tmp_path / "target"), lr_scheduler_config=schedule
    )
    weights = copy.deepcopy(target.model.state_dict())
    state = copy.deepcopy(target.get_trainer_state())
    with forbid_checkpoint_loads(target), pytest.raises(ValueError, match=error):
        restore_checkpoint_state(target, source.output_dir / "checkpoint-3", saved, load_via)
    assert_nested_equal(target.model.state_dict(), weights)
    assert_nested_equal(target.get_trainer_state(), state)


@pytest.mark.parametrize("gpu_count", [1, 2])
def test_checkpoint_restores_cpu_python_and_cuda_rng_streams(
    trained_trainer, simulated_cuda_rng, gpu_count
):
    trainer = trained_trainer
    generators = simulated_cuda_rng(gpu_count)
    trainer.save_model()
    expected_python = [random.random() for _ in range(4)]
    expected_cpu = torch.rand(5)
    expected_cuda = [torch.rand(5, generator=g) for g in generators]
    random.seed(999)
    torch.random.default_generator.manual_seed(777)
    for generator in generators:
        generator.manual_seed(555)

    trainer.load_checkpoint(trainer.output_dir / "checkpoint-3")
    assert [random.random() for _ in range(4)] == expected_python
    torch.testing.assert_close(torch.rand(5), expected_cpu, atol=0, rtol=0)
    for generator, expected in zip(generators, expected_cuda, strict=True):
        torch.testing.assert_close(torch.rand(5, generator=generator), expected, atol=0, rtol=0)


def test_cpu_checkpoint_never_uses_cuda_rng_apis(trained_trainer, monkeypatch):
    def unexpected_cuda_call(*args, **kwargs):
        pytest.fail("a CPU checkpoint must not call CUDA RNG APIs")

    for name in ("get_rng_state_all", "set_rng_state_all"):
        monkeypatch.setattr(torch.cuda, name, unexpected_cuda_call)
    trainer = trained_trainer
    trainer.save_model()
    trainer.load_checkpoint(trainer.output_dir / "checkpoint-3")
    assert trainer.get_trainer_state()["cuda_rng_state"] is None


def test_resume_latest_uses_step_not_mtime_and_ignores_staging(trainer_factory):
    trainer, _, _ = trainer_factory(
        dataset=[{"text": "a b c"}] * 20,
        num_workers=0,
        prefetch_factor=None,
        save_steps=1,
    )
    trainer.train()
    # Step 9 looks newer on disk and sorts after 10 lexicographically.
    for path in trainer._checkpoints():
        os.utime(path, (100, 100))
    os.utime(trainer.output_dir / "checkpoint-9", (200, 200))
    (trainer.output_dir / ".checkpoint-incomplete").mkdir()
    (trainer.output_dir / "checkpoint-not-a-step").mkdir()
    expected = copy.deepcopy(trainer.model.state_dict())
    with torch.no_grad():
        for parameter in trainer.model.parameters():
            parameter.zero_()
    trainer.train(resume_from_checkpoint=True)
    assert trainer.state.step.item() == trainer.lr_scheduler.completed_steps == 10
    assert_nested_equal(trainer.model.state_dict(), expected)


def test_failed_overwrite_keeps_original_checkpoint(trained_trainer, monkeypatch):
    trainer = trained_trainer
    trainer.save_model()
    checkpoint = trainer.output_dir / "checkpoint-3"
    original_files = {
        p.relative_to(checkpoint): p.read_bytes() for p in checkpoint.rglob("*") if p.is_file()
    }
    trainer.overwrite_output_dir = True
    with torch.no_grad():
        for parameter in trainer.model.parameters():
            parameter.add_(1)

    def fail_write(model, folder, **options):
        folder.mkdir()
        (folder / "model.safetensors").write_bytes(b"incomplete")
        raise OSError("disk full")

    monkeypatch.setattr("flock.training.core.trainer.write_model", fail_write)
    with pytest.raises(OSError, match="disk full"):
        trainer.save_model()
    assert {
        p.relative_to(checkpoint): p.read_bytes() for p in checkpoint.rglob("*") if p.is_file()
    } == original_files
    assert not list(trainer.output_dir.glob(".checkpoint-*"))


@pytest.mark.parametrize("failed_step", [2, 3])
def test_failed_upload_continues_training_and_retention(
    trainer_factory, monkeypatch, tmp_path, caplog, failed_step
):
    trainer, _, _ = trainer_factory(
        num_workers=0,
        prefetch_factor=None,
        save_steps=1,
        save_total_limit=1,
        save_to_hub_repo="cerulean/test-model",
    )

    upload_attempts = []

    def upload(folder, name, **options):
        upload_attempts.append(options["step"])
        if options["step"] == failed_step:
            raise OSError("upload interrupted")
        pending = Future()
        pending.set_result(None)
        return pending

    monkeypatch.setattr("flock.training.core.trainer.save_to_hub", upload)
    trainer.train()
    assert upload_attempts == [1, 2, 3]
    assert trainer.state.step.item() == trainer.total_steps == 3
    assert [p.name for p in trainer._checkpoints()] == ["checkpoint-3"]
    assert (
        f"Failed to save checkpoint {trainer.output_dir / f'checkpoint-{failed_step}'} to hub "
        "cerulean/test-model"
    ) in caplog.text
    expected = copy.deepcopy(trainer.get_trainer_state())
    restored, _, _ = trainer_factory(output_dir=str(tmp_path / "restored"))
    restored.load_checkpoint(trainer.output_dir / "checkpoint-3")
    assert_nested_equal(restored.model.state_dict(), trainer.model.state_dict())
    assert_nested_equal(restored.get_trainer_state(), expected)


def test_async_uploads_overlap_training_and_keep_sources_until_finished(
    trainer_factory, monkeypatch
):
    trainer, _, _ = trainer_factory(
        num_workers=0,
        prefetch_factor=None,
        save_steps=1,
        save_total_limit=1,
        save_to_hub_repo="cerulean/test-model",
    )
    release = threading.Event()
    all_started = threading.Event()
    lock = threading.Lock()
    started = []
    uploaded = []

    def push(source, ref, options, **kwargs):
        with lock:
            started.append(options["step"])
            if len(started) == 3:
                all_started.set()
        assert release.wait(10), "upload was never released"
        # Folder uploads read these files in the worker, after save_model has returned.
        assert (source / "model.safetensors").read_bytes()
        assert (source / "training_state/optimizer_state_dict.safetensors").read_bytes()
        assert json.loads((source / "flock.json").read_text())["step"] == options["step"]
        with lock:
            uploaded.append(options["step"])

    monkeypatch.setattr(hub_upload, "push", push)
    with ThreadPoolExecutor(max_workers=1) as executor:
        training = executor.submit(trainer.train)
        try:
            assert all_started.wait(10), "training blocked on an unfinished upload"
            assert not training.done(), "train returned before uploads finished"
            assert trainer.state.step.item() == 3
            assert [p.name for p in trainer._checkpoints()] == [
                "checkpoint-1",
                "checkpoint-2",
                "checkpoint-3",
            ]
        finally:
            release.set()
            training.result(timeout=10)

    assert sorted(uploaded) == [1, 2, 3]
    assert [p.name for p in trainer._checkpoints()] == ["checkpoint-3"]
    assert not trainer.pending_uploads
    trainer.save_model(blocking=True)  # Repeated cleanup must be harmless.


@pytest.mark.parametrize("failed_step", [2, 3])
def test_async_upload_failure_is_logged_and_other_uploads_finish(
    trainer_factory, monkeypatch, caplog, failed_step
):
    trainer, _, _ = trainer_factory(
        num_workers=0,
        prefetch_factor=None,
        save_steps=1,
        save_total_limit=1,
        save_to_hub_repo="cerulean/test-model",
    )
    uploaded = []

    def push(source, ref, options, **kwargs):
        if options["step"] == failed_step:
            raise OSError("background upload interrupted")
        uploaded.append(options["step"])

    monkeypatch.setattr(hub_upload, "push", push)
    trainer.train()
    assert sorted(uploaded) == [step for step in (1, 2, 3) if step != failed_step]
    assert trainer.state.step.item() == 3
    assert [p.name for p in trainer._checkpoints()] == ["checkpoint-3"]
    assert "background upload interrupted" in caplog.text
    assert str(trainer.output_dir / f"checkpoint-{failed_step}") in caplog.text
    assert not trainer.pending_uploads
    records = len(caplog.records)
    trainer.save_model(blocking=True)
    assert len(caplog.records) == records


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_training_error_drains_uploads_and_preserves_original_exception(
    trainer_factory, monkeypatch, error_type
):
    trainer, _, _ = trainer_factory(
        num_workers=0,
        prefetch_factor=None,
        save_steps=1,
        save_to_hub_repo="cerulean/test-model",
    )
    pending = Future()
    monkeypatch.setattr("flock.training.core.trainer.save_to_hub", lambda *a, **kw: pending)
    forward = trainer.model.forward

    def fail_on_second_batch(batch):
        if trainer.state.step.item() == 1:
            pending.set_exception(OSError("background upload failed"))
            raise error_type("training interrupted")
        return forward(batch)

    monkeypatch.setattr(trainer.model, "forward", fail_on_second_batch)
    with pytest.raises(error_type, match="training interrupted"):
        trainer.train()
    assert pending.done()
    assert not trainer.pending_uploads


def test_overwrite_waits_for_upload_to_finish_reading_original_checkpoint(
    trainer_factory, monkeypatch
):
    trainer, _, _ = trainer_factory(
        overwrite_output_dir=True, save_to_hub_repo="cerulean/test-model"
    )
    release = threading.Event()
    waiting = threading.Event()
    snapshots = []

    def push(source, ref, options, **kwargs):
        assert release.wait(10), "upload was never released"
        snapshots.append((source / "model.safetensors").read_bytes())

    monkeypatch.setattr(hub_upload, "push", push)
    pending = []
    save_to_hub = hub_upload.save_to_hub

    def upload(*args, **kwargs):
        result = save_to_hub(*args, **kwargs)
        pending.append(result)
        return result

    monkeypatch.setattr("flock.training.core.trainer.save_to_hub", upload)
    trainer.save_model()
    checkpoint = trainer.output_dir / "checkpoint-0/model.safetensors"
    original = checkpoint.read_bytes()
    with torch.no_grad():
        for parameter in trainer.model.parameters():
            parameter.add_(1)
    result = pending[0].result

    def wait_for_original(timeout=None):
        waiting.set()
        return result(timeout)

    monkeypatch.setattr(pending[0], "result", wait_for_original)
    with ThreadPoolExecutor(max_workers=1) as executor:
        overwrite = executor.submit(trainer.save_model)
        try:
            assert waiting.wait(10), "overwrite did not wait for the existing upload"
            assert not overwrite.done()
            assert checkpoint.read_bytes() == original
        finally:
            release.set()
            overwrite.result(timeout=10)
            trainer.save_model(blocking=True)

    assert snapshots == [original, checkpoint.read_bytes()]
    assert snapshots[0] != snapshots[1]
    assert not trainer.pending_uploads


@pytest.mark.parametrize("failed", [False, True], ids=["success", "failure"])
def test_completed_uploads_are_reaped_on_the_next_save(
    trainer_factory, monkeypatch, caplog, failed
):
    trainer, _, _ = trainer_factory(save_total_limit=1, save_to_hub_repo="cerulean/test-model")
    uploads = []

    def upload(*args, **kwargs):
        pending = Future()
        uploads.append(pending)
        return pending

    monkeypatch.setattr("flock.training.core.trainer.save_to_hub", upload)
    trainer.save_model()
    if failed:
        uploads[0].set_exception(OSError("completed upload failed"))
    else:
        uploads[0].set_result(None)
    trainer.state.step.fill_(1)
    trainer.save_model()
    assert [p.name for p in trainer._checkpoints()] == ["checkpoint-1"]
    assert list(trainer.pending_uploads) == [trainer.output_dir / "checkpoint-1"]
    assert ("completed upload failed" in caplog.text) == failed
    if failed:
        assert str(trainer.output_dir / "checkpoint-0") in caplog.text
    records = len(caplog.records)
    uploads[1].set_result(None)
    trainer.save_model(blocking=True)
    assert not trainer.pending_uploads
    assert len(caplog.records) == records


@pytest.mark.parametrize("failed", [False, True], ids=["success", "failure"])
def test_blocking_save_finishes_all_uploads_before_pruning(
    trainer_factory, monkeypatch, caplog, failed
):
    trainer, _, _ = trainer_factory(save_total_limit=1, save_to_hub_repo="cerulean/test-model")
    uploads = []
    waiting = [threading.Event(), threading.Event()]

    def upload(*args, **kwargs):
        pending = Future()
        index = len(uploads)
        result = pending.result

        def wait(timeout=None):
            waiting[index].set()
            return result(timeout)

        monkeypatch.setattr(pending, "result", wait)
        uploads.append(pending)
        return pending

    monkeypatch.setattr("flock.training.core.trainer.save_to_hub", upload)
    trainer.save_model()
    trainer.state.step.fill_(1)
    with ThreadPoolExecutor(max_workers=1) as executor:
        saving = executor.submit(trainer.save_model, blocking=True)
        try:
            assert waiting[0].wait(10), "blocking save did not wait for the older upload"
            assert [p.name for p in trainer._checkpoints()] == ["checkpoint-0", "checkpoint-1"]
            assert not saving.done()
            uploads[0].set_result(None)
            assert waiting[1].wait(10), "blocking save did not wait for its own upload"
            assert not saving.done()
            if failed:
                uploads[1].set_exception(OSError("blocking upload failed"))
            else:
                uploads[1].set_result(None)
        finally:
            for pending in uploads:
                if not pending.done():
                    pending.set_result(None)
            saving.result(timeout=10)

    assert not trainer.pending_uploads
    assert [p.name for p in trainer._checkpoints()] == ["checkpoint-1"]
    assert ("blocking upload failed" in caplog.text) == failed


def test_failed_submission_keeps_latest_checkpoint_while_old_upload_is_pending(
    trainer_factory, monkeypatch
):
    trainer, _, _ = trainer_factory(save_total_limit=1, save_to_hub_repo="cerulean/test-model")
    pending = Future()

    def upload(*args, **kwargs):
        if kwargs["step"] == 1:
            raise OSError("could not start upload")
        return pending

    monkeypatch.setattr("flock.training.core.trainer.save_to_hub", upload)
    trainer.save_model()
    trainer.state.step.fill_(1)
    trainer.save_model()
    assert [p.name for p in trainer._checkpoints()] == ["checkpoint-0", "checkpoint-1"]
    pending.set_result(None)
    trainer.save_model(blocking=True)
    assert [p.name for p in trainer._checkpoints()] == ["checkpoint-1"]
    assert not trainer.pending_uploads


@pytest.mark.parametrize("changed_field", ["dataset_size", "batch_size", "seed", "input_column"])
def test_incompatible_data_is_rejected_before_model_or_rng_changes(
    trained_trainer, trainer_factory, tmp_path, changed_field
):
    source = trained_trainer
    source.save_model()
    options = {"output_dir": str(tmp_path / "target")}
    if changed_field == "dataset_size":
        options["dataset"] = [{"text": "a b c"}] * 6
    else:
        options[changed_field] = {"batch_size": 1, "seed": 99, "input_column": "other"}[
            changed_field
        ]
    target, _, _ = trainer_factory(**options)
    with torch.no_grad():
        for parameter in target.model.parameters():
            parameter.add_(1)
    weights = copy.deepcopy(target.model.state_dict())
    state = copy.deepcopy(target.get_trainer_state())
    with pytest.raises(ValidationError):
        target.load_checkpoint(source.output_dir / "checkpoint-3")
    assert_nested_equal(target.model.state_dict(), weights)
    assert_nested_equal(target.get_trainer_state(), state)


@pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="Dataset/tokenizer identity is not checkpointed yet"
)
@pytest.mark.parametrize("changed_identity", ["dataset_uri", "dataset_version", "tokenizer"])
def test_resume_rejects_changed_dataset_or_tokenizer_identity(
    trainer_factory, tmp_path, changed_identity
):
    original_path = tmp_path / "original.lance"
    changed_path = tmp_path / "changed.lance"
    lance.write_dataset(pa.table({"text": ["a b c"] * 5}), str(original_path))
    lance.write_dataset(pa.table({"text": ["b c d"] * 5}), str(original_path), mode="overwrite")
    lance.write_dataset(pa.table({"text": ["d c b"] * 5}), str(changed_path))
    source, _, _ = trainer_factory(
        dataset=SafeLanceDataset(str(original_path), dataset_options={"version": 1}),
        num_workers=0,
        prefetch_factor=None,
    )
    source.train()
    source.save_model()
    target, _, _ = trainer_factory(
        dataset=SafeLanceDataset(
            str(changed_path if changed_identity == "dataset_uri" else original_path),
            dataset_options={"version": 2 if changed_identity == "dataset_version" else 1},
        ),
        output_dir=str(tmp_path / "target"),
    )
    if changed_identity == "tokenizer":
        target.tokenizer = copy.deepcopy(target.tokenizer)
        vocabulary = target.tokenizer.get_vocab()
        vocabulary["a"], vocabulary["b"] = vocabulary["b"], vocabulary["a"]
        target.tokenizer.backend_tokenizer.model = WordLevel(vocabulary, unk_token="[UNK]")
    try:
        target.load_checkpoint(source.output_dir / "checkpoint-3")
    except ValueError:
        return
    raise AssertionError(f"Checkpoint accepted a changed {changed_identity}")


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Autocast dtype and clipping threshold are not checkpointed yet",
)
@pytest.mark.parametrize(
    "overrides", [{"model_dtype": torch.float32}, {"max_norm": 2.0}], ids=["dtype", "clipping"]
)
def test_resume_rejects_changed_training_settings(
    trained_trainer, trainer_factory, tmp_path, overrides
):
    source = trained_trainer
    source.save_model()
    target, _, _ = trainer_factory(output_dir=str(tmp_path / "target"), **overrides)
    try:
        target.load_checkpoint(source.output_dir / "checkpoint-3")
    except ValueError:
        return
    raise AssertionError("Checkpoint accepted changed training settings")


@pytest.mark.xfail(
    strict=True,
    raises=ValidationError,
    reason="Repeated train resets counters but retains optimizer/scheduler progress",
)
def test_repeated_training_rejects_reuse_or_leaves_a_loadable_checkpoint(trained_trainer):
    trainer = trained_trainer
    weights = copy.deepcopy(trainer.model.state_dict())
    state = copy.deepcopy(trainer.get_trainer_state())
    try:
        trainer.train()
    except RuntimeError:
        assert_nested_equal(trainer.model.state_dict(), weights)
        assert_nested_equal(trainer.get_trainer_state(), state)
        return
    trainer.save_model()
    trainer.load_checkpoint(trainer.output_dir / f"checkpoint-{int(trainer.state.step.item())}")


@pytest.mark.xfail(
    strict=True, raises=IndexError, reason="CUDA RNG restore assumes unchanged visible device count"
)
def test_resume_handles_cuda_topology_change_cleanly(trained_trainer, simulated_cuda_rng):
    trainer = trained_trainer
    generators = simulated_cuda_rng(2)
    trainer.save_model()
    expected = torch.rand(5, generator=generators[0])
    del generators[1:]
    generators[0].manual_seed(999)
    try:
        trainer.load_checkpoint(trainer.output_dir / "checkpoint-3")
    except ValueError:
        return  # Explicitly rejecting a topology change is also a supported policy.
    torch.testing.assert_close(torch.rand(5, generator=generators[0]), expected, atol=0, rtol=0)


def test_adamw_forwards_configuration_and_matches_torch_updates():
    trainer = object.__new__(Trainer)
    trainer.model = cast(BaseModel, nn.Linear(3, 2))
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
    trainer.model = cast(BaseModel, nn.Linear(3, 2))
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


def reference_epoch(model, batches, max_norm, model_dtype):
    """Independent three-update loop: explicit LR values and gradient clipping math."""
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.01, betas=(0.8, 0.9))
    records = []
    for batch, factor in zip(batches, [1.0, 0.775, 0.325], strict=True):
        optimizer.zero_grad()
        valid = batch["labels"] != -100
        # Match the trainer's forward precision, keeping loss and master weights in FP32.
        with torch.autocast("cpu", dtype=model_dtype):
            logits = model(batch)
        loss = F.cross_entropy(logits.float()[valid], batch["labels"][valid])
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
    records = reference_epoch(reference, batches, trainer.max_norm, trainer.model_dtype)
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
    with pytest.raises(ValidationError, match="must be None"):
        trainer_factory(num_workers=0, prefetch_factor=prefetch_factor)


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
