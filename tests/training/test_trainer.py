from __future__ import annotations

import copy
import json
import math
import os
import random
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Never, cast

import lance
import pyarrow as pa
import pytest
import torch
import torch._functorch.config as functorch_config
from lance.torch.data import SafeLanceDataset
from pydantic import ValidationError
from tokenizers.models import WordLevel
from torch.nn import functional as F
from torch.utils.data import DataLoader
from torch.utils.data.dataloader import _MultiProcessingDataLoaderIter

from flock.eval.tasks import TASKS, Choice, Dataset, MCDoc, MultipleChoiceTask, PerplexityTask
from flock.eval.tasks.perplexity import _docs_by_domain
from flock.hub.models import upload as hub_upload
from flock.hub.models.files import write_training_state
from flock.training import TokenChunkDataset
from flock.training.core import trainer as trainer_module
from flock.training.core.data_utils import LanceBatchCollator
from flock.training.core.distributed import DistributedRuntime
from flock.training.core.lr_scheduler import LRSchedulerConfig, LRScheduleSegment, StepLRScheduler
from flock.training.core.models import BaseModel
from flock.training.core.models.olmo2 import Olmo2
from flock.training.core.optimizers import AdamWConfig, OptimizerInterface
from flock.training.core.trainer import Trainer, TrainerCheckpoint, TrainerConfig, TrainerState

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from contextlib import AbstractContextManager
    from pathlib import Path
    from typing import Any

    from transformers import TokenizersBackend

    from flock.eval.model import ScoringModel
    from flock.eval.results import TaskResult
    from flock.eval.run import PreparedTask
    from flock.eval.tasks import Task
    from flock.hub.models._common import ModelVersion, Ref
    from flock.hub.models.upload import PendingUpload
    from flock.training.core.data_utils import Batch
    from flock.training.core.models.olmo2 import Olmo2Config


@dataclass
class RecordingRun:
    entity: str = "flock"
    project: str = "pretrain"
    id: str = "run123"
    definitions: list = field(default_factory=list)
    metrics: list = field(default_factory=list)

    def define_metric(self, *args: Any, **kwargs: Any) -> None:
        self.definitions.append((args, kwargs))

    def log(self, metrics: dict[str, Any]) -> None:
        self.metrics.append(dict(metrics))


type TrainerBundle = tuple[Trainer, RecordingRun, list[dict[str, Any]]]
type TrainerFactory = Callable[..., TrainerBundle]


@pytest.fixture
def trainer_factory(
    config: Olmo2Config,
    tokenizer: TokenizersBackend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> TrainerFactory:
    # These tests must remain on CPU even when invoked on a developer GPU machine.
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)

    def make(
        dataset: list[dict[str, str]] | SafeLanceDataset | TokenChunkDataset | None = None,
        logging_steps: int = 1,
        num_workers: int = 1,
        prefetch_factor: int | None = 1,
        **config_overrides: Any,
    ) -> TrainerBundle:
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
                    "lr_scheduler_config": LRSchedulerConfig(
                        schedule=[
                            LRScheduleSegment(
                                start=0,
                                end=1.0,
                                start_ratio=1.0,
                                end_ratio=0.1,
                                interpolation="cosine",
                            )
                        ]
                    ),
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


def test_trainer_initializes_schedule_and_metric_step(
    trainer_factory: TrainerFactory,
) -> None:
    trainer, run, _ = trainer_factory()
    assert trainer.device == torch.device("cpu")
    assert trainer.total_steps == 3  # Five rows include a final partial batch.
    assert trainer.lr_scheduler.total_steps == 3
    assert run.definitions == [
        (("global_step",), {}),
        (("train/*",), {"step_metric": "global_step"}),
        (("eval/*",), {"step_metric": "global_step"}),
    ]


@pytest.mark.parametrize("max_steps", [3, 100])
def test_step_cap_at_or_past_the_pass_changes_nothing(
    trainer_factory: TrainerFactory, max_steps: int
) -> None:
    default, default_run, _ = trainer_factory(num_workers=0, prefetch_factor=None)
    default.train()
    capped, capped_run, _ = trainer_factory(
        num_workers=0, prefetch_factor=None, max_steps=max_steps
    )
    capped.train()
    assert capped.total_steps == capped.lr_scheduler.total_steps == 3
    assert capped_run.metrics == default_run.metrics
    assert_nested_equal(capped.model.state_dict(), default.model.state_dict())
    assert_nested_equal(capped.state_dict(), default.state_dict())


@pytest.mark.parametrize(
    "schedule",
    [
        LRSchedulerConfig(
            schedule=[
                LRScheduleSegment(
                    start=0, end=1.0, start_ratio=1.0, end_ratio=0.1, interpolation="cosine"
                )
            ]
        ),
        LRSchedulerConfig(
            schedule=[
                LRScheduleSegment(start=0, end=1, start_ratio=1.0, end_ratio=1.0),
                LRScheduleSegment(
                    start=1, end=1.0, start_ratio=1.0, end_ratio=0.1, interpolation="cosine"
                ),
            ]
        ),
    ],
    ids=["cosine", "wsd"],
)
def test_step_cap_ends_the_run_and_its_schedule_at_the_cap(
    trainer_factory: TrainerFactory,
    monkeypatch: pytest.MonkeyPatch,
    schedule: LRSchedulerConfig,
) -> None:
    trainer, run, _ = trainer_factory(
        num_workers=0,
        prefetch_factor=None,
        logging_steps=5,
        save_steps=5,
        max_steps=2,
        lr_scheduler_config=schedule,
    )
    seen_batches = []
    forward = trainer.model._forward

    def recording_forward(*args: Any) -> torch.Tensor:
        seen_batches.append(args[0].detach().clone())
        return forward(*args)

    monkeypatch.setattr(trainer.model, "_forward", recording_forward)
    trainer.train()
    uncapped, _, _ = trainer_factory(num_workers=0, prefetch_factor=None)
    first_two = list(uncapped._get_training_dataloader())[:2]

    assert trainer.total_steps == trainer.lr_scheduler.total_steps == 2
    assert len(trainer._get_training_dataloader()) == 2
    assert trainer.lr_scheduler.completed_steps == 2
    # The decay ends at the cap, not at the end of the full pass.
    assert trainer.lr_scheduler.get_last_lr() == pytest.approx([0.001])
    assert [metrics["global_step"] for metrics in run.metrics] == [2]
    assert [path.name for path in trainer.checkpoint_manager.checkpoints()] == ["checkpoint-2"]
    assert_nested_equal(seen_batches, [batch["input_ids"] for batch in first_two])


def test_step_cap_shorter_than_the_schedule_is_rejected(
    trainer_factory: TrainerFactory,
) -> None:
    schedule = LRSchedulerConfig(
        schedule=[LRScheduleSegment(start=0, end=3, start_ratio=0.0, end_ratio=1.0)]
    )
    with pytest.raises(ValueError, match="Schedule endpoint exceeds total_steps"):
        trainer_factory(
            num_workers=0, prefetch_factor=None, max_steps=2, lr_scheduler_config=schedule
        )


def test_step_cap_change_rejects_resume(trainer_factory: TrainerFactory) -> None:
    capped, _, _ = trainer_factory(num_workers=0, prefetch_factor=None, save_steps=1, max_steps=2)
    capped.train()
    changed, _, _ = trainer_factory(
        num_workers=0, prefetch_factor=None, output_dir=str(capped.output_dir), max_steps=3
    )
    with pytest.raises(ValueError, match="plans 2 updates, but this run plans 3"):
        changed.train(resume_from_checkpoint=True)


@pytest.fixture
def validation_tasks(monkeypatch: pytest.MonkeyPatch) -> list[PerplexityTask | MultipleChoiceTask]:
    """Real task/scorer paths with local rows and the training fixture's tokenizer."""
    _docs_by_domain.cache_clear()
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

    def load(dataset: Dataset, split: str | None = None) -> list[dict[str, Any]]:
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
    trainer_factory: TrainerFactory,
    validation_tasks: list[PerplexityTask | MultipleChoiceTask],
    monkeypatch: pytest.MonkeyPatch,
    interval: None | int,
    expected_steps: list[int],
) -> None:
    trainer, _, _ = trainer_factory(
        num_workers=0,
        prefetch_factor=None,
        validation_tasks=[task.name for task in validation_tasks],
        validation_steps=interval,
    )
    calls, forward_modes = [], []

    def evaluate() -> None:
        calls.append(int(trainer.state.step.item()))

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
    trainer_factory: TrainerFactory,
    validation_tasks: list[PerplexityTask | MultipleChoiceTask],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trainer, run, module_logs = trainer_factory(
        num_workers=0,
        prefetch_factor=None,
        validation_tasks=[task.name for task in validation_tasks],
        model_dtype=torch.float32,
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
    state = copy.deepcopy(trainer.state_dict())
    calls = []
    score_prepared = trainer_module.score_prepared

    def score(scorer: ScoringModel, prepared: PreparedTask) -> TaskResult:
        task = prepared.task
        assert not torch.is_grad_enabled()
        assert not trainer.model.training
        assert scorer.model.model is trainer.model
        assert scorer.tokenizer is trainer.tokenizer
        assert scorer.name == "test-run@step-2"
        calls.append(task)
        return score_prepared(scorer, prepared)

    monkeypatch.setattr(trainer_module, "score_prepared", score)
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
    assert_nested_equal(trainer.state_dict(), state)
    for parameter in trainer.model.parameters():
        torch.testing.assert_close(parameter.grad, torch.full_like(parameter, 0.125))


@pytest.mark.parametrize(
    "error_type, failed_task",
    [(None, None), (KeyError, 0), (KeyError, 1), (FileExistsError, 0), (FileExistsError, 1)],
)
def test_validation_preserves_training_updates(
    trainer_factory: TrainerFactory,
    validation_tasks: list[PerplexityTask | MultipleChoiceTask],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    error_type: type[Exception] | None,
    failed_task: int | None,
) -> None:
    reference, reference_run, _ = trainer_factory(num_workers=0, prefetch_factor=None)
    reference.train()
    expected_state = copy.deepcopy(reference.state_dict())
    trainer, run, _ = trainer_factory(
        num_workers=0,
        prefetch_factor=None,
        output_dir=str(tmp_path / "validation-errors"),
        validation_tasks=[task.name for task in validation_tasks],
        validation_steps=1,
    )
    calls = []
    score_prepared = trainer_module.score_prepared

    def score(scorer: ScoringModel, prepared: PreparedTask) -> TaskResult:
        task = prepared.task
        assert not trainer.model.training
        calls.append((int(trainer.state.step.item()), task.name))
        if (
            error_type is not None
            and failed_task is not None
            and task is validation_tasks[failed_task]
        ):
            raise error_type("validation fixture failure")
        return score_prepared(scorer, prepared)

    monkeypatch.setattr(trainer_module, "score_prepared", score)
    trainer.train()

    scored_tasks = validation_tasks if failed_task is None else validation_tasks[: failed_task + 1]
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
    assert_nested_equal(trainer.state_dict(), expected_state)


def test_unexpected_validation_error_is_not_silenced(
    trainer_factory: TrainerFactory,
    validation_tasks: list[PerplexityTask | MultipleChoiceTask],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trainer, run, _ = trainer_factory(
        num_workers=0,
        prefetch_factor=None,
        validation_tasks=[task.name for task in validation_tasks],
        validation_steps=1,
    )

    def score(scorer: ScoringModel, prepared: PreparedTask) -> Never:
        raise ValueError("invalid validation window")

    monkeypatch.setattr(trainer_module, "score_prepared", score)
    with pytest.raises(ValueError, match="invalid validation window"):
        trainer.train()
    assert trainer.state.step.item() == trainer.lr_scheduler.completed_steps == 1
    assert trainer.model.training
    assert all(not key.startswith("eval/") for metrics in run.metrics for key in metrics)


def test_validation_reuses_loaded_and_tokenized_tasks_with_limits(
    trainer_factory: TrainerFactory,
    validation_tasks: list[PerplexityTask | MultipleChoiceTask],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trainer, run, _ = trainer_factory(
        num_workers=0,
        prefetch_factor=None,
        validation_tasks=[task.name for task in validation_tasks],
        validation_limit=1,
        validation_batch_tokens=4,
    )
    loads, tokenizations, prepared_calls, scored, batch_sizes = [], [], [], [], []
    load = Dataset.load
    tokenize = trainer_module.ScoringModel.tokenize
    prepare = trainer_module.prepare_task
    score = trainer_module.score_prepared

    def load_once(dataset: Dataset, split: str | None = None) -> list[dict[str, Any]]:
        loads.append(dataset.split)
        return load(dataset, split)

    def tokenize_once(scorer: ScoringModel, texts: list[str]) -> list[list[int]]:
        tokenizations.append(texts)
        return tokenize(scorer, texts)

    def prepare_once(scorer: ScoringModel, task: Task, limit: int | None) -> PreparedTask:
        prepared = prepare(scorer, task, limit)
        prepared_calls.append((task.name, prepared))
        return prepared

    def record_score(scorer: ScoringModel, prepared: PreparedTask) -> TaskResult:
        assert scorer.batch_tokens == 4
        result = score(scorer, prepared)
        assert result.limit == result.num_docs == 1
        scored.append(prepared)
        return result

    monkeypatch.setattr(Dataset, "load", load_once)
    monkeypatch.setattr(trainer_module.ScoringModel, "tokenize", tokenize_once)
    monkeypatch.setattr(trainer_module, "prepare_task", prepare_once)
    monkeypatch.setattr(trainer_module, "score_prepared", record_score)
    hook = trainer.model.register_forward_pre_hook(
        lambda model, args: batch_sizes.append(args[0]["input_ids"].numel())
    )
    try:
        trainer.evaluate()
        first_tokenizations = len(tokenizations)
        assert first_tokenizations > 0
        # The next step uses new weights, while its requests keep the same token IDs.
        with torch.no_grad():
            cast(Olmo2, trainer.model).lm_head.weight.zero_()
        trainer.state.step.fill_(1)
        trainer.evaluate()
    finally:
        hook.remove()
    assert loads == ["text", "choices"]
    assert len(tokenizations) == first_tokenizations
    assert [name for name, _ in prepared_calls] == [task.name for task in validation_tasks]
    assert all(scored[index] is prepared for index, (_, prepared) in enumerate(prepared_calls))
    assert all(scored[index + 2] is prepared for index, (_, prepared) in enumerate(prepared_calls))
    assert batch_sizes and max(batch_sizes) <= 4
    assert [metrics["global_step"] for metrics in run.metrics] == [0, 1]
    assert run.metrics[1]["eval/test_trainer_text/loss_per_token"] == pytest.approx(math.log(32))


@pytest.mark.parametrize("dtype", [torch.bfloat16, torch.float32])
@pytest.mark.parametrize("training", [True, False])
def test_evaluation_precision_and_module_modes(
    trainer_factory: TrainerFactory,
    validation_tasks: list[PerplexityTask | MultipleChoiceTask],
    dtype: torch.dtype,
    training: bool,
) -> None:
    trainer, _, _ = trainer_factory(
        num_workers=0,
        prefetch_factor=None,
        validation_tasks=[task.name for task in validation_tasks],
        model_dtype=dtype,
    )
    trainer.model.train(training)
    cast(Olmo2, trainer.model).lm_head.train(not training)
    modes = [module.training for module in trainer.model.modules()]
    outputs = []

    def record(
        model: torch.nn.Module, args: tuple[torch.Tensor, ...], output: torch.Tensor
    ) -> None:
        assert all(not module.training for module in model.modules())
        assert not torch.is_grad_enabled()
        assert torch.is_autocast_enabled("cpu") == (dtype == torch.bfloat16)
        outputs.append(output.dtype)

    hook = trainer.model.register_forward_hook(record)
    try:
        trainer.evaluate()
    finally:
        hook.remove()
    assert outputs and set(outputs) == {dtype}
    assert all(parameter.dtype == torch.float32 for parameter in trainer.model.parameters())
    assert [module.training for module in trainer.model.modules()] == modes
    assert not torch.is_autocast_enabled("cpu")


@pytest.mark.parametrize("error", [KeyError, ValueError])
def test_evaluation_restores_modes_after_preparation_failure(
    trainer_factory: TrainerFactory,
    validation_tasks: list[PerplexityTask | MultipleChoiceTask],
    monkeypatch: pytest.MonkeyPatch,
    error: type[Exception],
) -> None:
    trainer, _, _ = trainer_factory(validation_tasks=[task.name for task in validation_tasks])
    cast(Olmo2, trainer.model).lm_head.eval()
    modes = [module.training for module in trainer.model.modules()]

    def fail(*args: Any, **kwargs: Any) -> Never:
        raise error("preparation failed")

    monkeypatch.setattr(trainer_module, "prepare_task", fail)
    if error is ValueError:
        with pytest.raises(ValueError, match="preparation failed"):
            trainer.evaluate()
    else:
        trainer.evaluate()
    assert [module.training for module in trainer.model.modules()] == modes


@pytest.mark.parametrize("tasks_enabled", [True, False])
def test_disabled_validation_does_not_prepare_tasks(
    trainer_factory: TrainerFactory,
    validation_tasks: list[PerplexityTask | MultipleChoiceTask],
    monkeypatch: pytest.MonkeyPatch,
    tasks_enabled: bool,
) -> None:
    trainer, run, _ = trainer_factory(
        num_workers=0,
        prefetch_factor=None,
        validation_tasks=[task.name for task in validation_tasks] if tasks_enabled else [],
        validation_steps=None if tasks_enabled else 1,
    )

    def fail(*args: Any, **kwargs: Any) -> None:
        pytest.fail("disabled validation must not prepare tasks")

    monkeypatch.setattr(trainer_module, "prepare_task", fail)
    trainer.train()
    assert not any(key.startswith("eval/") for metrics in run.metrics for key in metrics)


@pytest.mark.parametrize("fail_evaluation", [True, False])
def test_training_saves_checkpoints_with_validation(
    trainer_factory: TrainerFactory,
    validation_tasks: list[PerplexityTask | MultipleChoiceTask],
    monkeypatch: pytest.MonkeyPatch,
    fail_evaluation: bool,
) -> None:
    trainer, _, _ = trainer_factory(
        num_workers=0,
        prefetch_factor=None,
        validation_tasks=[task.name for task in validation_tasks],
        validation_steps=2,
        save_steps=2,
    )
    calls = []
    evaluate = trainer.evaluate

    def validate() -> None:
        calls.append(int(trainer.state.step.item()))
        if fail_evaluation:
            raise RuntimeError("validation failed")
        evaluate()

    monkeypatch.setattr(trainer, "evaluate", validate)
    if fail_evaluation:
        with pytest.raises(RuntimeError, match="validation failed"):
            trainer.train()
        assert calls == [2]
        load_checkpoint(trainer, trainer.output_dir / "checkpoint-2")
        assert trainer.state.step.item() == trainer.lr_scheduler.completed_steps == 2
    else:
        trainer.train()
        assert calls == [2, 3]
        for step in calls:
            checkpoint = trainer.output_dir / f"checkpoint-{step}"
            assert (checkpoint / "model.safetensors").is_file()
            assert (checkpoint / "training_state/trainer_state_dict.safetensors").is_file()


def test_local_checkpoint_records_run_identity(
    trainer_factory: TrainerFactory,
) -> None:
    trainer, _, _ = trainer_factory(num_workers=0, prefetch_factor=None)
    trainer.train()
    save_checkpoint(trainer)
    metadata = json.loads((trainer.output_dir / "checkpoint-3" / "flock.json").read_text())
    assert metadata["run"] == "flock/pretrain/run123"
    assert metadata["step"] == trainer.state.step.item() == 3


def test_compiled_training_resumes_and_preserves_checkpoint_format(
    trainer_factory: TrainerFactory,
    validation_tasks: list[PerplexityTask | MultipleChoiceTask],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Exercise compiled autograd on CPU; GPU tests cover Inductor and CUDA graphs.
    from torch._dynamo.backends.registry import lookup_backend

    compile_calls = []
    compiled_functions = []
    captured_graphs = []
    real_compile = torch.compile
    aot_eager = lookup_backend("aot_eager")

    def backend(graph: torch.fx.GraphModule, inputs: list[torch.Tensor]) -> Callable:
        assert functorch_config.backward_pass_autocast == "off"
        captured_graphs.append(graph)
        return aot_eager(graph, inputs)

    def compile_for_test(function: Callable, **kwargs: Any) -> Callable:
        compile_calls.append(kwargs)
        compiled_functions.append(getattr(function, "__name__", None))
        return real_compile(function, backend=backend, fullgraph=kwargs["fullgraph"])

    monkeypatch.setattr(torch, "compile", compile_for_test)
    options = {
        "num_workers": 0,
        "prefetch_factor": None,
        "validation_tasks": [task.name for task in validation_tasks],
        "validation_steps": 1,
    }
    eager, _, _ = trainer_factory(**options, save_steps=1)
    eager.train()
    assert compile_calls == []
    compiled, run, _ = trainer_factory(
        **options, torch_compile=True, output_dir=str(tmp_path / "compiled")
    )
    original_model = compiled.model
    parameter_ids = [id(p) for p in compiled.model.parameters()]
    original_autocast = functorch_config.backward_pass_autocast
    checkpoint = eager.output_dir / "checkpoint-1"
    try:
        compiled.train(resume_from_checkpoint=checkpoint)
        assert captured_graphs
        assert compile_calls == [{"mode": "default", "fullgraph": False}] * 2
        # The loss collectives stay eager; only the per-rank token loss compiles.
        assert "_token_loss" in compiled_functions
        assert "_calc_batch_loss" not in compiled_functions
        assert compiled.model is original_model
        assert [id(p) for p in compiled.model.parameters()] == parameter_ids
        assert [id(p) for p in compiled.optimizer.param_groups[0]["params"]] == parameter_ids
        torch.testing.assert_close(compiled.model.state_dict(), eager.model.state_dict())
        torch.testing.assert_close(compiled.optimizer.state_dict(), eager.optimizer.state_dict())
        assert compiled.state.step.item() == compiled.lr_scheduler.completed_steps == 3
        assert any(key.startswith("eval/") for metrics in run.metrics for key in metrics)
        assert compiled.model.training
        assert functorch_config.backward_pass_autocast == original_autocast

        compiled._save_model(3)
        saved = compiled.output_dir / "checkpoint-3"
        metadata = json.loads((saved / "flock.json").read_text())
        original_metadata = json.loads((checkpoint / "flock.json").read_text())
        assert metadata["architecture"] == original_metadata["architecture"]
        restored, _, _ = trainer_factory(output_dir=str(tmp_path / "restored"))
        restored.load_state_dict(
            restored.checkpoint_manager.load_checkpoint(
                saved, restored.model, validate_state=restored.validate_state_dict
            )
        )
        assert_nested_equal(restored.model.state_dict(), compiled.model.state_dict())
        assert_nested_equal(restored.state_dict(), compiled.state_dict())

        # Reusing a Trainer must not wrap the model/loss again.
        compiled.train(resume_from_checkpoint=checkpoint)
        assert len(compile_calls) == 2
        torch.testing.assert_close(compiled.model.state_dict(), eager.model.state_dict())
    finally:
        torch.compiler.reset()


@pytest.mark.parametrize("error", [None, KeyError, RuntimeError])
@pytest.mark.parametrize(
    "mode", ["default", "reduce-overhead", "max-autotune", "max-autotune-no-cudagraphs"]
)
def test_evaluation_compilation_policy_and_restoration(
    trainer_factory: TrainerFactory,
    validation_tasks: list[PerplexityTask | MultipleChoiceTask],
    monkeypatch: pytest.MonkeyPatch,
    error: type[Exception] | None,
    mode: str,
) -> None:
    trainer, _, _ = trainer_factory(
        num_workers=0,
        prefetch_factor=None,
        torch_compile=True,
        torch_compile_mode=mode,
        validation_tasks=[task.name for task in validation_tasks],
        validation_batch_tokens=4,
    )
    executions = []

    def backend(graph: torch.fx.GraphModule, inputs: list[torch.Tensor]) -> Callable:
        def run(*args: torch.Tensor) -> Any:
            executions.append(None)
            return graph.forward(*args)

        return run

    probe = torch.compile(torch.sin, backend=backend, fullgraph=True)
    value = torch.ones(2)
    score = trainer_module.score_prepared
    bypass = mode in ("reduce-overhead", "max-autotune")

    def check_score(scorer: ScoringModel, prepared: PreparedTask) -> TaskResult:
        # A cached graph must also be bypassed, not just new compilations.
        before = len(executions)
        torch.testing.assert_close(probe(value), value.sin())
        assert len(executions) == before + (0 if bypass else 1)
        if error is not None:
            raise error("scoring failed")
        return score(scorer, prepared)

    monkeypatch.setattr(trainer_module, "score_prepared", check_score)
    try:
        with torch.no_grad(), torch.autocast("cpu", dtype=trainer.model_dtype):
            probe(value)
            assert len(executions) == 1
            if error is RuntimeError:
                with pytest.raises(RuntimeError, match="scoring failed"):
                    trainer.evaluate()
            else:
                trainer.evaluate()
            before = len(executions)
            probe(value)
            assert len(executions) == before + 1
        assert trainer.model.training
    finally:
        torch.compiler.reset()


def test_compile_autocast_setting_is_restored_after_training_failure(
    trainer_factory: TrainerFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    trainer, _, _ = trainer_factory(num_workers=0, prefetch_factor=None, torch_compile=True)
    original_autocast = functorch_config.backward_pass_autocast

    def fail(dataloader: DataLoader, start_step: int) -> Never:
        assert functorch_config.backward_pass_autocast == "off"
        raise RuntimeError("training failed")

    monkeypatch.setattr(trainer, "_inner_training_loop", fail)
    with pytest.raises(RuntimeError, match="training failed"):
        trainer.train()
    assert functorch_config.backward_pass_autocast == original_autocast


def assert_nested_equal(actual: Any, expected: Any) -> None:
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


def save_checkpoint(trainer: Trainer, *, blocking: bool = False) -> None:
    manager = trainer.checkpoint_manager
    assert manager is not None
    manager.save_model(
        int(trainer.state.step.item()),
        trainer.model,
        training_state=trainer.state_dict(),
        blocking=blocking,
    )


def load_checkpoint(trainer: Trainer, checkpoint: Path) -> None:
    manager = trainer.checkpoint_manager
    assert manager is not None
    trainer.load_state_dict(
        manager.load_checkpoint(
            checkpoint,
            trainer.model,
            validate_state=trainer.validate_state_dict,
        )
    )


def restore_checkpoint_state(
    trainer: Trainer, checkpoint: Path, state: dict[str, Any], load_via: str
) -> None:
    if load_via == "checkpoint":
        write_training_state(state, checkpoint)
        load_checkpoint(trainer, checkpoint)
    else:
        if load_via == "validated-state":
            trainer.load_state_dict(TrainerCheckpoint.model_validate(state))
        else:
            trainer.load_state_dict(state)


@pytest.fixture
def forbid_checkpoint_loads(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Trainer], AbstractContextManager[None]]:
    @contextmanager
    def forbid(trainer: Trainer) -> Generator[None]:
        def unexpected_load(*args: Any, **kwargs: Any) -> None:
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
            patches.setattr("flock.training.core.checkpoint.load_model", unexpected_load)
            yield

    return forbid


@pytest.fixture
def trained_trainer(
    trainer_factory: TrainerFactory,
) -> Trainer:
    trainer, _, _ = trainer_factory(num_workers=0, prefetch_factor=None)
    trainer.train()
    return trainer


@pytest.fixture
def simulated_cuda_rng(monkeypatch: pytest.MonkeyPatch) -> Callable[[int], list[torch.Generator]]:
    """Exercise CUDA RNG save/restore routing with independent CPU generators.

    These tests never initialize CUDA and do not establish CUDA kernel determinism.
    """

    def install(count: int) -> list[torch.Generator]:
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
@pytest.mark.parametrize(
    ("split_step", "max_steps"),
    [(0, None), (1, None), (2, None), (3, None), (4, None), (0, 3), (1, 3), (2, 3), (3, 3)],
)
@pytest.mark.parametrize(
    "schedule",
    [
        LRSchedulerConfig(
            schedule=[
                LRScheduleSegment(start=0, end=1, start_ratio=0.0, end_ratio=1.0),
                LRScheduleSegment(
                    start=1, end=1.0, start_ratio=1.0, end_ratio=0.1, interpolation="cosine"
                ),
            ]
        ),
        LRSchedulerConfig(
            schedule=[
                LRScheduleSegment(start=0, end=1, start_ratio=0.0, end_ratio=1.0),
                LRScheduleSegment(start=1, end=2, start_ratio=1, end_ratio=1),
                LRScheduleSegment(
                    start=2, end=1.0, start_ratio=1.0, end_ratio=0.1, interpolation="cosine"
                ),
            ]
        ),
    ],
    ids=["cosine", "wsd"],
)
def test_checkpoint_resume_matches_uninterrupted_training(
    trainer_factory: TrainerFactory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    resume_latest: bool,
    split_step: int,
    max_steps: int | None,
    schedule: LRSchedulerConfig,
) -> None:
    def make(output_dir: Path, **overrides: Any) -> TrainerBundle:
        trainer, run, _ = trainer_factory(
            # Seven rows make four updates, so a cap of three ends the run early.
            dataset=[
                {"text": text} for text in ("a b c d", "b c", "c d a", "a b c", "d a", "b", "c")
            ],
            num_workers=0,
            prefetch_factor=None,
            logging_steps=2,
            output_dir=str(output_dir),
            save_steps=1,
            lr_scheduler_config=schedule,
            max_steps=max_steps,
            **overrides,
        )
        forward = trainer.model._forward
        seen_batches = []

        def stochastic_forward(*args: Any) -> torch.Tensor:
            # Exercise restoration of both Python and torch RNGs, including the
            # extra DataLoader iterator created when a process resumes.
            seen_batches.append(args[0].detach().clone())
            return F.dropout(forward(*args), p=0.25, training=True) * (0.8 + random.random())

        monkeypatch.setattr(trainer.model, "_forward", stochastic_forward)
        return trainer, run, seen_batches

    reference, expected_run, expected_batches = make(tmp_path / "reference")
    reference.train()
    expected_state = copy.deepcopy(reference.state_dict())
    expected_random = (random.random(), torch.rand(4))

    interrupted, first_run, first_batches = make(tmp_path / "resumed")
    save = interrupted.checkpoint_manager.save_model

    def save_and_interrupt(*args: Any, **kwargs: Any) -> None:
        save(*args, **kwargs)
        if interrupted.state.step.item() == split_step:
            # A process interruption must bypass nonfatal checkpoint-write handling.
            raise KeyboardInterrupt("simulate process exit after a completed checkpoint")

    if split_step == 0:
        interrupted._set_seed()
        save_checkpoint(interrupted)
    else:
        monkeypatch.setattr(interrupted.checkpoint_manager, "save_model", save_and_interrupt)
        with pytest.raises(KeyboardInterrupt, match="simulate process exit"):
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
    assert_nested_equal(resumed.state_dict(), expected_state)
    assert first_run.metrics + resumed_run.metrics == expected_run.metrics
    assert_nested_equal(first_batches + resumed_batches, expected_batches)
    assert random.random() == expected_random[0]
    torch.testing.assert_close(torch.rand(4), expected_random[1], atol=0, rtol=0)
    expected_steps = list(range(1, split_step + 1)) if split_step else [0]
    # After the restart, saves fall on even steps and at the final update.
    total_steps = reference.total_steps
    expected_steps += [
        step
        for step in range(2, total_steps + 1)
        if step > split_step and (step % 2 == 0 or step == total_steps)
    ]
    assert [path.name for path in resumed.checkpoint_manager.checkpoints()] == [
        f"checkpoint-{step}" for step in expected_steps
    ]
    # Resuming an already completed run must perform no further updates or saves.
    resumed.train(resume_from_checkpoint=True)
    assert resumed.lr_scheduler.completed_steps == total_steps
    assert first_run.metrics + resumed_run.metrics == expected_run.metrics


def test_checkpoint_retention_removes_nested_state_and_orders_by_step(
    trainer_factory: TrainerFactory,
) -> None:
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
    assert [path.name for path in trainer.checkpoint_manager.checkpoints()] == [
        "checkpoint-9",
        "checkpoint-10",
    ]
    assert unrelated.is_dir()
    assert (
        trainer.output_dir / "checkpoint-10/training_state/optimizer_state_dict.safetensors"
    ).is_file()


def test_checkpoint_upload_uses_complete_local_folder(
    trainer_factory: TrainerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trainer, run, _ = trainer_factory(save_to_hub_repo="cerulean/test-model")
    uploads = []

    def upload(folder: Path, name: str, **options: Any) -> Future[Any]:
        assert (folder / "model.safetensors").is_file()
        assert (folder / "training_state/rank_states.safetensors").is_file()
        uploads.append((folder, name, options))
        pending = Future()
        pending.set_result(None)
        return pending

    monkeypatch.setattr("flock.training.core.checkpoint.save_to_hub", upload)
    save_checkpoint(trainer)
    trainer.checkpoint_manager.finish_checkpoint_uploads()
    assert uploads == [
        (
            trainer.output_dir / "checkpoint-0",
            "cerulean/test-model",
            {"run": run, "step": 0, "tags": ["checkpoint-0"], "blocking": False},
        )
    ]
    assert uploads[0][0].is_dir()


def test_failed_checkpoint_write_does_not_publish_or_prune(
    trainer_factory: TrainerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trainer, _, _ = trainer_factory(
        num_workers=0,
        prefetch_factor=None,
        save_steps=1,
        save_total_limit=1,
        overwrite_output_dir=True,
    )
    save_checkpoint(trainer)

    def fail_write(model: BaseModel, folder: Path, **kwargs: Any) -> Never:
        folder.mkdir()
        (folder / "flock.json").write_text("partial")
        raise OSError("disk full")

    monkeypatch.setattr("flock.training.core.checkpoint.write_model", fail_write)
    with pytest.raises(OSError, match="disk full"):
        trainer.train()
    assert [path.name for path in trainer.output_dir.iterdir()] == ["checkpoint-0"]


def test_resume_rejects_missing_state_and_incompatible_data(
    trainer_factory: TrainerFactory,
) -> None:
    trainer, _, _ = trainer_factory(num_workers=0, prefetch_factor=None)
    with pytest.raises(FileNotFoundError, match="No checkpoints"):
        trainer.train(resume_from_checkpoint=True)
    save_checkpoint(trainer)
    checkpoint = trainer.output_dir / "checkpoint-0"
    trainer.seed += 1
    with pytest.raises(ValueError, match="seed"):
        load_checkpoint(trainer, checkpoint)
    trainer.seed -= 1
    for path in (checkpoint / "training_state").iterdir():
        path.unlink()
    with pytest.raises(ValueError, match="no training state"):
        load_checkpoint(trainer, checkpoint)


@pytest.mark.parametrize("blocking", [False, True])
def test_existing_output_requires_resume_or_overwrite(
    trainer_factory: TrainerFactory, blocking: bool
) -> None:
    trainer, _, _ = trainer_factory(num_workers=0, prefetch_factor=None)
    save_checkpoint(trainer)
    with pytest.raises(RuntimeError, match="Resume"):
        trainer.train()
    with pytest.raises(FileExistsError, match="already exists"):
        save_checkpoint(trainer, blocking=blocking)
    trainer.overwrite_output_dir = True
    trainer.checkpoint_manager.overwrite_output_dir = True
    save_checkpoint(trainer, blocking=blocking)
    load_checkpoint(trainer, trainer.output_dir / "checkpoint-0")


@pytest.mark.parametrize(
    "section, field, value",
    [
        ("config", "batch_size", "2"),
        ("trainer_state_dict", "step", torch.tensor(0.5)),
        ("trainer_state_dict", "step", torch.tensor([0])),
        ("trainer_state_dict", "step", torch.tensor(-1)),
        ("lr_scheduler_state_dict", "completed_steps", "0"),
        ("lr_scheduler_state_dict", "completed_steps", 1),
        ("lr_scheduler_state_dict", "total_steps", 4),
    ],
)
def test_checkpoint_schema_rejects_invalid_state_without_mutating_trainer(
    trainer_factory: TrainerFactory,
    section: str,
    field: str,
    value: str | int,
) -> None:
    trainer, _, _ = trainer_factory()
    expected = copy.deepcopy(trainer.state_dict())
    invalid = copy.deepcopy(expected)
    invalid[section][field] = value
    with pytest.raises(ValidationError):
        trainer.load_state_dict(invalid)
    assert_nested_equal(trainer.state_dict(), expected)


def test_checkpoint_schema_reports_missing_fields(
    trainer_factory: TrainerFactory,
) -> None:
    trainer, _, _ = trainer_factory()
    invalid = trainer.state_dict()
    del invalid["trainer_state_dict"]["step"]
    with pytest.raises(ValidationError, match="trainer_state_dict.step"):
        trainer.load_state_dict(invalid)


@pytest.mark.parametrize("shard_size", [2048, 5 * 2**30])
def test_checkpoint_preserves_tied_parameters_and_optimizer_references(
    trainer_factory: TrainerFactory,
    monkeypatch: pytest.MonkeyPatch,
    shard_size: int,
) -> None:
    from flock.hub.models.files import write_model

    trainer, _, _ = trainer_factory(num_workers=0, prefetch_factor=None, save_steps=3)
    cast(Olmo2, trainer.model).lm_head.weight = cast(Olmo2, trainer.model).model.embed_tokens.weight
    trainer.optimizer = OptimizerInterface.get_interface(AdamWConfig)(
        AdamWConfig(lr=0.01, betas=(0.8, 0.9)), trainer.model.parameters()
    )
    trainer.lr_scheduler = StepLRScheduler(
        trainer.optimizer,
        LRSchedulerConfig(
            schedule=[
                LRScheduleSegment(
                    start=0, end=1.0, start_ratio=1.0, end_ratio=0.1, interpolation="cosine"
                )
            ]
        ),
        trainer.total_steps,
    )

    def write_shards(model: BaseModel, folder: Path, **options: Any) -> None:
        options["shard_size"] = shard_size
        write_model(model, folder, **options)

    monkeypatch.setattr("flock.training.core.checkpoint.write_model", write_shards)
    trainer.train()
    checkpoint = trainer.output_dir / "checkpoint-3"
    assert (checkpoint / "model.safetensors.index.json").is_file() == (shard_size == 2048)
    weights = copy.deepcopy(trainer.model.state_dict())
    state = copy.deepcopy(trainer.state_dict())
    parameters = [id(p) for p in trainer.model.parameters()]
    with torch.no_grad():
        for parameter in trainer.model.parameters():
            parameter.zero_()
    cast(torch.optim.Optimizer, trainer.optimizer).state.clear()
    load_checkpoint(trainer, checkpoint)
    assert (
        cast(Olmo2, trainer.model).lm_head.weight
        is cast(Olmo2, trainer.model).model.embed_tokens.weight
    )
    assert [id(p) for p in trainer.model.parameters()] == parameters
    assert [id(p) for p in trainer.optimizer.param_groups[0]["params"]] == parameters
    assert_nested_equal(trainer.model.state_dict(), weights)
    assert_nested_equal(trainer.state_dict(), state)


@pytest.mark.parametrize("corruption", ["missing", "unexpected"])
def test_sharded_checkpoint_rejects_missing_or_unexpected_weights(
    trainer_factory: TrainerFactory,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    from safetensors.torch import load_file, save_file

    from flock.hub.models.files import weight_files, write_model

    trainer, _, _ = trainer_factory()

    def write_shards(model: BaseModel, folder: Path, **options: Any) -> None:
        options["shard_size"] = 2048
        write_model(model, folder, **options)

    monkeypatch.setattr("flock.training.core.checkpoint.write_model", write_shards)
    save_checkpoint(trainer)
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
        load_checkpoint(trainer, checkpoint)


def test_saved_checkpoints_remain_independent_of_later_updates(
    trainer_factory: TrainerFactory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trainer, _, _ = trainer_factory(num_workers=0, prefetch_factor=None, save_steps=1)
    snapshots = {}
    save = trainer.checkpoint_manager.save_model

    def save_and_record(*args: Any, **kwargs: Any) -> None:
        save(*args, **kwargs)
        snapshots[int(trainer.state.step.item())] = (
            copy.deepcopy(trainer.model.state_dict()),
            copy.deepcopy(trainer.state_dict()),
        )

    monkeypatch.setattr(trainer.checkpoint_manager, "save_model", save_and_record)
    trainer.train()
    with torch.no_grad():
        for parameter in trainer.model.parameters():
            parameter.zero_()
    trainer.state.reset_global()
    cast(torch.optim.Optimizer, trainer.optimizer).state.clear()

    restored, _, _ = trainer_factory(output_dir=str(tmp_path / "restored"))
    for step, (weights, state) in snapshots.items():
        load_checkpoint(restored, trainer.output_dir / f"checkpoint-{step}")
        assert_nested_equal(restored.model.state_dict(), weights)
        assert_nested_equal(restored.state_dict(), state)


@pytest.mark.parametrize("load_via", ["checkpoint", "trainer-state", "validated-state"])
@pytest.mark.parametrize("target_steps", [0, 1])
@pytest.mark.parametrize(
    "schedule",
    [
        LRSchedulerConfig(
            schedule=[
                LRScheduleSegment(start=0, end=1, start_ratio=0.0, end_ratio=1.0),
                LRScheduleSegment(
                    start=1, end=1.0, start_ratio=1.0, end_ratio=0.1, interpolation="cosine"
                ),
            ]
        ),
        LRSchedulerConfig(
            schedule=[
                LRScheduleSegment(start=0, end=1, start_ratio=0.0, end_ratio=1.0),
                LRScheduleSegment(start=1, end=2, start_ratio=1, end_ratio=1),
                LRScheduleSegment(
                    start=2, end=1.0, start_ratio=1.0, end_ratio=0.1, interpolation="cosine"
                ),
            ]
        ),
    ],
    ids=["cosine", "wsd"],
)
def test_loading_restores_progress_with_matching_settings_and_different_current_lr(
    trainer_factory: TrainerFactory,
    tmp_path: Path,
    load_via: str,
    target_steps: int,
    schedule: LRSchedulerConfig,
) -> None:
    source, _, _ = trainer_factory(
        num_workers=0, prefetch_factor=None, lr_scheduler_config=schedule
    )
    source.train()
    save_checkpoint(source)
    state = copy.deepcopy(source.state_dict())
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
    assert_nested_equal(target.state_dict(), state)


@pytest.mark.parametrize("load_via", ["checkpoint", "trainer-state", "validated-state"])
@pytest.mark.parametrize(
    "overrides, error",
    [
        (
            {
                "lr_scheduler_config": LRSchedulerConfig(
                    schedule=[
                        LRScheduleSegment(
                            start=2, end=1.0, start_ratio=1.0, end_ratio=0.0, interpolation="cosine"
                        )
                    ]
                )
            },
            "Scheduler config",
        ),
        (
            {
                "lr_scheduler_config": LRSchedulerConfig(
                    schedule=[
                        LRScheduleSegment(start=0, end=1, start_ratio=0.0, end_ratio=1.0),
                        LRScheduleSegment(
                            start=1, end=1.0, start_ratio=1.0, end_ratio=0.1, interpolation="cosine"
                        ),
                    ]
                )
            },
            "Scheduler config",
        ),
        (
            {
                "lr_scheduler_config": LRSchedulerConfig(
                    schedule=[
                        LRScheduleSegment(
                            start=0, end=1.0, start_ratio=1.0, end_ratio=0.2, interpolation="cosine"
                        )
                    ]
                )
            },
            "Scheduler config",
        ),
        ({"optimizer_config": AdamWConfig(lr=0.3, betas=(0.8, 0.9))}, "base_lrs"),
        ({"optimizer_config": AdamWConfig(lr=0.01, betas=(0.7, 0.99))}, "optimizer"),
    ],
    ids=["schedule-type", "warmup", "minimum-lr", "base-lr", "betas"],
)
def test_incompatible_schedule_or_optimizer_is_rejected_before_loading(
    trained_trainer: Trainer,
    trainer_factory: TrainerFactory,
    tmp_path: Path,
    forbid_checkpoint_loads: Callable[[Trainer], AbstractContextManager[None]],
    load_via: str,
    overrides: dict[str, Any],
    error: str,
) -> None:
    source = trained_trainer
    save_checkpoint(source)
    target, _, _ = trainer_factory(output_dir=str(tmp_path / "target"), **overrides)
    # Populate optimizer buffers so a failed restore must preserve existing state too.
    for parameter in target.model.parameters():
        parameter.grad = torch.ones_like(parameter)
    target.optimizer.step()
    target.lr_scheduler.step()
    target.state.step.fill_(1)
    weights = copy.deepcopy(target.model.state_dict())
    state = copy.deepcopy(target.state_dict())
    saved = copy.deepcopy(source.state_dict())
    expected_saved = copy.deepcopy(saved)
    with forbid_checkpoint_loads(target), pytest.raises(ValueError, match=error):
        restore_checkpoint_state(target, source.output_dir / "checkpoint-3", saved, load_via)
    assert_nested_equal(target.model.state_dict(), weights)
    assert_nested_equal(target.state_dict(), state)
    assert_nested_equal(saved, expected_saved)


@pytest.mark.parametrize("load_via", ["checkpoint", "trainer-state", "validated-state"])
@pytest.mark.parametrize(
    "change, error",
    [
        ("group-count", "parameter groups"),
        ("group-size", "group 0 size"),
        ("weight_decay", "group 0 settings"),
        ("eps", "group 0 settings"),
        ("decay-start", "Scheduler config"),
    ],
)
def test_incompatible_optimizer_layout_or_wsd_settings_reject_before_loading(
    trainer_factory: TrainerFactory,
    tmp_path: Path,
    forbid_checkpoint_loads: Callable[[Trainer], AbstractContextManager[None]],
    load_via: str,
    change: str,
    error: str,
) -> None:
    schedule = LRSchedulerConfig(
        schedule=[
            LRScheduleSegment(start=0, end=1, start_ratio=0.0, end_ratio=1.0),
            LRScheduleSegment(start=1, end=2, start_ratio=1, end_ratio=1),
            LRScheduleSegment(
                start=2, end=1.0, start_ratio=1.0, end_ratio=0.1, interpolation="cosine"
            ),
        ]
    )
    source, _, _ = trainer_factory(
        num_workers=0, prefetch_factor=None, lr_scheduler_config=schedule
    )
    source.train()
    save_checkpoint(source)
    saved = copy.deepcopy(source.state_dict())
    groups = saved["optimizer_state_dict"]["param_groups"]
    if change == "group-count":
        groups.append(copy.deepcopy(groups[0]))
    elif change == "group-size":
        groups[0]["params"].pop()
    elif change == "decay-start":
        saved_schedule = saved["lr_scheduler_state_dict"]["config"]["schedule"]
        saved_schedule[-2]["end"] = 0.75
        saved_schedule[-1]["start"] = 0.75
    else:
        groups[0][change] *= 2

    target, _, _ = trainer_factory(
        output_dir=str(tmp_path / "target"), lr_scheduler_config=schedule
    )
    weights = copy.deepcopy(target.model.state_dict())
    state = copy.deepcopy(target.state_dict())
    with forbid_checkpoint_loads(target), pytest.raises(ValueError, match=error):
        restore_checkpoint_state(target, source.output_dir / "checkpoint-3", saved, load_via)
    assert_nested_equal(target.model.state_dict(), weights)
    assert_nested_equal(target.state_dict(), state)


@pytest.mark.parametrize("gpu_count", [1, 2])
def test_checkpoint_restores_cpu_python_and_cuda_rng_streams(
    trained_trainer: Trainer,
    simulated_cuda_rng: Callable[[int], list[torch.Generator]],
    gpu_count: int,
) -> None:
    trainer = trained_trainer
    generators = simulated_cuda_rng(gpu_count)
    save_checkpoint(trainer)
    expected_python = [random.random() for _ in range(4)]
    expected_cpu = torch.rand(5)
    expected_cuda = [torch.rand(5, generator=g) for g in generators]
    random.seed(999)
    torch.random.default_generator.manual_seed(777)
    for generator in generators:
        generator.manual_seed(555)

    load_checkpoint(trainer, trainer.output_dir / "checkpoint-3")
    assert [random.random() for _ in range(4)] == expected_python
    torch.testing.assert_close(torch.rand(5), expected_cpu, atol=0, rtol=0)
    for generator, expected in zip(generators, expected_cuda, strict=True):
        torch.testing.assert_close(torch.rand(5, generator=generator), expected, atol=0, rtol=0)


def test_cpu_checkpoint_never_uses_cuda_rng_apis(
    trained_trainer: Trainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_cuda_call(*args: Any, **kwargs: Any) -> None:
        pytest.fail("a CPU checkpoint must not call CUDA RNG APIs")

    for name in ("get_rng_state_all", "set_rng_state_all"):
        monkeypatch.setattr(torch.cuda, name, unexpected_cuda_call)
    trainer = trained_trainer
    save_checkpoint(trainer)
    load_checkpoint(trainer, trainer.output_dir / "checkpoint-3")
    assert trainer.state_dict()["rank_states"][0]["cuda_rng_state"] is None


def test_resume_latest_uses_step_not_mtime_and_ignores_staging(
    trainer_factory: TrainerFactory,
) -> None:
    trainer, _, _ = trainer_factory(
        dataset=[{"text": "a b c"}] * 20,
        num_workers=0,
        prefetch_factor=None,
        save_steps=1,
    )
    trainer.train()
    # Step 9 looks newer on disk and sorts after 10 lexicographically.
    for path in trainer.checkpoint_manager.checkpoints():
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


def test_failed_overwrite_keeps_original_checkpoint(
    trained_trainer: Trainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    trainer = trained_trainer
    save_checkpoint(trainer)
    checkpoint = trainer.output_dir / "checkpoint-3"
    original_files = {
        p.relative_to(checkpoint): p.read_bytes() for p in checkpoint.rglob("*") if p.is_file()
    }
    trainer.overwrite_output_dir = True
    trainer.checkpoint_manager.overwrite_output_dir = True
    with torch.no_grad():
        for parameter in trainer.model.parameters():
            parameter.add_(1)

    def fail_write(model: BaseModel, folder: Path, **options: Any) -> Never:
        folder.mkdir()
        (folder / "model.safetensors").write_bytes(b"incomplete")
        raise OSError("disk full")

    monkeypatch.setattr("flock.training.core.checkpoint.write_model", fail_write)
    with pytest.raises(OSError, match="disk full"):
        save_checkpoint(trainer)
    assert {
        p.relative_to(checkpoint): p.read_bytes() for p in checkpoint.rglob("*") if p.is_file()
    } == original_files
    assert not list(trainer.output_dir.glob(".checkpoint-*"))


@pytest.mark.parametrize("failed_step", [2, 3])
def test_failed_upload_continues_training_and_retention(
    trainer_factory: TrainerFactory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    failed_step: int,
) -> None:
    trainer, _, _ = trainer_factory(
        num_workers=0,
        prefetch_factor=None,
        save_steps=1,
        save_total_limit=1,
        save_to_hub_repo="cerulean/test-model",
    )

    upload_attempts = []

    def upload(folder: Path, name: str, **options: Any) -> Future[Any]:
        upload_attempts.append(options["step"])
        if options["step"] == failed_step:
            raise OSError("upload interrupted")
        pending = Future()
        pending.set_result(None)
        return pending

    monkeypatch.setattr("flock.training.core.checkpoint.save_to_hub", upload)
    trainer.train()
    assert sorted(upload_attempts) == [1, 2, 3]
    assert trainer.state.step.item() == trainer.total_steps == 3
    assert [p.name for p in trainer.checkpoint_manager.checkpoints()] == ["checkpoint-3"]
    assert (
        f"Failed to save checkpoint {trainer.output_dir / f'checkpoint-{failed_step}'} to hub "
        "cerulean/test-model"
    ) in caplog.text
    expected = copy.deepcopy(trainer.state_dict())
    restored, _, _ = trainer_factory(output_dir=str(tmp_path / "restored"))
    load_checkpoint(restored, trainer.output_dir / "checkpoint-3")
    assert_nested_equal(restored.model.state_dict(), trainer.model.state_dict())
    assert_nested_equal(restored.state_dict(), expected)


def test_async_uploads_overlap_training_and_keep_sources_until_finished(
    trainer_factory: TrainerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    def push(source: Path, ref: Ref, options: dict[str, Any], **kwargs: Any) -> None:
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
            assert [p.name for p in trainer.checkpoint_manager.checkpoints()] == [
                "checkpoint-1",
                "checkpoint-2",
                "checkpoint-3",
            ]
        finally:
            release.set()
            training.result(timeout=10)

    assert sorted(uploaded) == [1, 2, 3]
    trainer.checkpoint_manager.finish_checkpoint_uploads()  # Repeated cleanup must be harmless.
    assert [p.name for p in trainer.checkpoint_manager.checkpoints()] == ["checkpoint-3"]
    assert not trainer.checkpoint_manager.pending_uploads


@pytest.mark.parametrize("failed_step", [2, 3])
def test_async_upload_failure_is_logged_and_other_uploads_finish(
    trainer_factory: TrainerFactory,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failed_step: int,
) -> None:
    trainer, _, _ = trainer_factory(
        num_workers=0,
        prefetch_factor=None,
        save_steps=1,
        save_total_limit=1,
        save_to_hub_repo="cerulean/test-model",
    )
    uploaded = []

    def push(source: Path, ref: Ref, options: dict[str, Any], **kwargs: Any) -> None:
        if options["step"] == failed_step:
            raise OSError("background upload interrupted")
        uploaded.append(options["step"])

    monkeypatch.setattr(hub_upload, "push", push)
    trainer.train()
    assert sorted(uploaded) == [step for step in (1, 2, 3) if step != failed_step]
    assert trainer.state.step.item() == 3
    assert [p.name for p in trainer.checkpoint_manager.checkpoints()] == ["checkpoint-3"]
    assert "background upload interrupted" in caplog.text
    assert str(trainer.output_dir / f"checkpoint-{failed_step}") in caplog.text
    assert not trainer.checkpoint_manager.pending_uploads
    records = len(caplog.records)
    trainer.checkpoint_manager.finish_checkpoint_uploads()
    assert len(caplog.records) == records


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_training_error_drains_uploads_and_preserves_original_exception(
    trainer_factory: TrainerFactory,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
) -> None:
    trainer, _, _ = trainer_factory(
        num_workers=0,
        prefetch_factor=None,
        save_steps=1,
        save_to_hub_repo="cerulean/test-model",
    )
    pending = Future()
    monkeypatch.setattr("flock.training.core.checkpoint.save_to_hub", lambda *a, **kw: pending)
    forward = trainer.model.forward

    def fail_on_second_batch(batch: Batch) -> torch.Tensor:
        if trainer.state.step.item() == 1:
            pending.set_exception(OSError("background upload failed"))
            raise error_type("training interrupted")
        return forward(batch)

    monkeypatch.setattr(trainer.model, "forward", fail_on_second_batch)
    with pytest.raises(error_type, match="training interrupted"):
        trainer.train()
    assert pending.done()
    assert not trainer.checkpoint_manager.pending_uploads


def test_overwrite_waits_for_upload_to_finish_reading_original_checkpoint(
    trainer_factory: TrainerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trainer, _, _ = trainer_factory(
        overwrite_output_dir=True, save_to_hub_repo="cerulean/test-model"
    )
    release = threading.Event()
    waiting = threading.Event()
    snapshots = []

    def push(source: Path, ref: Ref, options: dict[str, Any], **kwargs: Any) -> None:
        assert release.wait(10), "upload was never released"
        snapshots.append((source / "model.safetensors").read_bytes())

    monkeypatch.setattr(hub_upload, "push", push)
    pending = []
    save_to_hub = hub_upload.save_to_hub

    def upload(*args: Any, **kwargs: Any) -> Future[Any] | ModelVersion | PendingUpload:
        result = save_to_hub(*args, **kwargs)
        pending.append(result)
        return result

    monkeypatch.setattr("flock.training.core.checkpoint.save_to_hub", upload)
    save_checkpoint(trainer)
    checkpoint = trainer.output_dir / "checkpoint-0/model.safetensors"
    original = checkpoint.read_bytes()
    with torch.no_grad():
        for parameter in trainer.model.parameters():
            parameter.add_(1)
    result = pending[0].result

    def wait_for_original(timeout: float | None = None) -> Any:
        waiting.set()
        return result(timeout)

    monkeypatch.setattr(pending[0], "result", wait_for_original)
    with ThreadPoolExecutor(max_workers=1) as executor:
        overwrite = executor.submit(save_checkpoint, trainer)
        try:
            assert waiting.wait(10), "overwrite did not wait for the existing upload"
            assert not overwrite.done()
            assert checkpoint.read_bytes() == original
        finally:
            release.set()
            overwrite.result(timeout=10)
            trainer.checkpoint_manager.finish_checkpoint_uploads()

    assert snapshots == [original, checkpoint.read_bytes()]
    assert snapshots[0] != snapshots[1]
    assert not trainer.checkpoint_manager.pending_uploads


@pytest.mark.parametrize("failed", [False, True], ids=["success", "failure"])
def test_next_save_collects_completed_upload_and_prunes_its_checkpoint(
    trainer_factory: TrainerFactory,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failed: bool,
) -> None:
    trainer, _, _ = trainer_factory(save_total_limit=1, save_to_hub_repo="cerulean/test-model")
    uploads = []

    def upload(*args: Any, **kwargs: Any) -> Future[Any]:
        pending = Future()
        uploads.append(pending)
        return pending

    monkeypatch.setattr("flock.training.core.checkpoint.save_to_hub", upload)
    save_checkpoint(trainer)
    if failed:
        uploads[0].set_exception(OSError("completed upload failed"))
    else:
        uploads[0].set_result(None)
    trainer.state.step.fill_(1)
    save_checkpoint(trainer)
    assert [p.name for p in trainer.checkpoint_manager.checkpoints()] == ["checkpoint-1"]
    assert list(trainer.checkpoint_manager.pending_uploads) == [trainer.output_dir / "checkpoint-1"]
    assert ("completed upload failed" in caplog.text) == failed
    if failed:
        assert str(trainer.output_dir / "checkpoint-0") in caplog.text
    records = len(caplog.records)
    uploads[1].set_result(None)
    trainer.checkpoint_manager.finish_checkpoint_uploads()
    assert not trainer.checkpoint_manager.pending_uploads
    assert len(caplog.records) == records


@pytest.mark.parametrize("failed", [False, True], ids=["success", "failure"])
def test_blocking_save_finishes_all_uploads_before_pruning(
    trainer_factory: TrainerFactory,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failed: bool,
) -> None:
    trainer, _, _ = trainer_factory(save_total_limit=1, save_to_hub_repo="cerulean/test-model")
    uploads = []
    waiting = [threading.Event(), threading.Event()]

    def upload(*args: Any, **kwargs: Any) -> Future[Any]:
        pending = Future()
        index = len(uploads)
        result = pending.result

        def wait(timeout: float | None = None) -> Any:
            waiting[index].set()
            return result(timeout)

        monkeypatch.setattr(pending, "result", wait)
        uploads.append(pending)
        return pending

    monkeypatch.setattr("flock.training.core.checkpoint.save_to_hub", upload)
    save_checkpoint(trainer)
    trainer.state.step.fill_(1)
    with ThreadPoolExecutor(max_workers=1) as executor:
        saving = executor.submit(save_checkpoint, trainer, blocking=True)
        try:
            assert waiting[0].wait(10), "blocking save did not wait for the older upload"
            assert [p.name for p in trainer.checkpoint_manager.checkpoints()] == [
                "checkpoint-0",
                "checkpoint-1",
            ]
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

    assert not trainer.checkpoint_manager.pending_uploads
    assert [p.name for p in trainer.checkpoint_manager.checkpoints()] == ["checkpoint-1"]
    assert ("blocking upload failed" in caplog.text) == failed


def test_failed_submission_keeps_latest_checkpoint_while_old_upload_is_pending(
    trainer_factory: TrainerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trainer, _, _ = trainer_factory(save_total_limit=1, save_to_hub_repo="cerulean/test-model")
    pending = Future()

    def upload(*args: Any, **kwargs: Any) -> Future[Any]:
        if kwargs["step"] == 1:
            raise OSError("could not start upload")
        return pending

    monkeypatch.setattr("flock.training.core.checkpoint.save_to_hub", upload)
    save_checkpoint(trainer)
    trainer.state.step.fill_(1)
    save_checkpoint(trainer)
    assert [p.name for p in trainer.checkpoint_manager.checkpoints()] == [
        "checkpoint-0",
        "checkpoint-1",
    ]
    pending.set_result(None)
    trainer.checkpoint_manager.finish_checkpoint_uploads()
    assert [p.name for p in trainer.checkpoint_manager.checkpoints()] == ["checkpoint-1"]
    assert not trainer.checkpoint_manager.pending_uploads


@pytest.mark.parametrize("changed_field", ["dataset_size", "batch_size", "seed", "input_column"])
def test_incompatible_data_is_rejected_before_model_or_rng_changes(
    trained_trainer: Trainer,
    trainer_factory: TrainerFactory,
    tmp_path: Path,
    changed_field: str,
) -> None:
    source = trained_trainer
    save_checkpoint(source)
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
    state = copy.deepcopy(target.state_dict())
    with pytest.raises(ValidationError):
        load_checkpoint(target, source.output_dir / "checkpoint-3")
    assert_nested_equal(target.model.state_dict(), weights)
    assert_nested_equal(target.state_dict(), state)


@pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="Dataset/tokenizer identity is not checkpointed yet"
)
@pytest.mark.parametrize("changed_identity", ["dataset_uri", "dataset_version", "tokenizer"])
def test_resume_rejects_changed_dataset_or_tokenizer_identity(
    trainer_factory: TrainerFactory,
    tmp_path: Path,
    changed_identity: str,
) -> None:
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
    save_checkpoint(source)
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
        load_checkpoint(target, source.output_dir / "checkpoint-3")
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
    trained_trainer: Trainer,
    trainer_factory: TrainerFactory,
    tmp_path: Path,
    overrides: dict[str, torch.dtype] | dict[str, float],
) -> None:
    source = trained_trainer
    save_checkpoint(source)
    target, _, _ = trainer_factory(output_dir=str(tmp_path / "target"), **overrides)
    try:
        load_checkpoint(target, source.output_dir / "checkpoint-3")
    except ValueError:
        return
    raise AssertionError("Checkpoint accepted changed training settings")


@pytest.mark.xfail(
    strict=True,
    raises=ValidationError,
    reason="Repeated train resets counters but retains optimizer/scheduler progress",
)
def test_repeated_training_rejects_reuse_or_leaves_a_loadable_checkpoint(
    trained_trainer: Trainer,
) -> None:
    trainer = trained_trainer
    weights = copy.deepcopy(trainer.model.state_dict())
    state = copy.deepcopy(trainer.state_dict())
    try:
        trainer.train()
    except RuntimeError:
        assert_nested_equal(trainer.model.state_dict(), weights)
        assert_nested_equal(trainer.state_dict(), state)
        return
    save_checkpoint(trainer)
    load_checkpoint(trainer, trainer.output_dir / f"checkpoint-{int(trainer.state.step.item())}")


def test_resume_handles_cuda_topology_change_cleanly(
    trained_trainer: Trainer, simulated_cuda_rng: Callable[[int], list[torch.Generator]]
) -> None:
    trainer = trained_trainer
    generators = simulated_cuda_rng(2)
    save_checkpoint(trainer)
    expected = torch.rand(5, generator=generators[0])
    del generators[1:]
    generators[0].manual_seed(999)
    try:
        load_checkpoint(trainer, trainer.output_dir / "checkpoint-3")
    except ValueError:
        return  # Explicitly rejecting a topology change is also a supported policy.
    torch.testing.assert_close(torch.rand(5, generator=generators[0]), expected, atol=0, rtol=0)


@pytest.mark.parametrize("corruption", ["rank", "cursor", "cpu_rng", "python_rng", "world_size"])
def test_rank_resume_state_is_validated_before_restoration(
    trainer_factory: TrainerFactory,
    forbid_checkpoint_loads: Callable[[Trainer], AbstractContextManager[None]],
    corruption: str,
) -> None:
    trainer, _, _ = trainer_factory(num_workers=0, prefetch_factor=None)
    saved = copy.deepcopy(trainer.state_dict())
    local = saved["rank_states"][0]
    if corruption == "rank":
        saved["rank_states"] = {1: local}
    elif corruption == "cursor":
        local["data_state"]["batch_cursor"] = 1
    elif corruption == "cpu_rng":
        local["cpu_rng_state"] = torch.zeros(1, dtype=torch.uint8)
    elif corruption == "python_rng":
        local["python_rng_state"] = (3, (0,), None)
    else:
        saved["config"]["world_size"] = 2
    with forbid_checkpoint_loads(trainer), pytest.raises(ValueError):
        trainer.load_state_dict(saved)


@pytest.mark.parametrize("dtype", [torch.float32, torch.float16, torch.bfloat16])
def test_loss_and_gradients_match_cross_entropy_over_valid_targets(dtype: torch.dtype) -> None:
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
    trainer = object.__new__(Trainer)
    trainer.runtime = DistributedRuntime(0, 0, 1, torch.device("cpu"))
    loss, losses, count = trainer._calc_batch_loss(logits, labels)
    expected = F.cross_entropy(reference.float()[valid], labels[valid])
    assert count.item() == 4
    assert loss.dtype == torch.float32
    torch.testing.assert_close(loss, expected)
    torch.testing.assert_close(losses, expected.detach())
    assert not losses.requires_grad
    loss.backward()
    expected.backward()
    torch.testing.assert_close(logits.grad, reference.grad)
    assert logits.grad is not None
    assert torch.equal(logits.grad[~valid], torch.zeros_like(logits.grad[~valid]))


@pytest.mark.parametrize("dtype", [torch.float32, torch.float16, torch.bfloat16])
def test_all_ignored_targets_do_not_produce_nan_loss(dtype: torch.dtype) -> None:
    logits = torch.linspace(-2, 2, 30, dtype=dtype).reshape(2, 3, 5).requires_grad_()
    labels = torch.full((2, 3), -100, dtype=torch.long)
    trainer = object.__new__(Trainer)
    trainer.runtime = DistributedRuntime(0, 0, 1, torch.device("cpu"))
    loss, losses, count = trainer._calc_batch_loss(logits, labels)
    assert count.item() == 0
    assert torch.isfinite(loss), (
        "a batch with no training targets must not poison training with NaN"
    )
    assert loss.dtype == torch.float32
    assert loss.item() == 0.0
    assert losses.item() == 0.0
    assert not losses.requires_grad
    loss.backward()
    assert logits.grad is not None
    torch.testing.assert_close(logits.grad, torch.zeros_like(logits), atol=0, rtol=0)


@pytest.mark.parametrize("compiled_loss", [False, True])
def test_dummy_batch_has_zero_loss_tokens_and_model_gradients(
    config: Olmo2Config, tokenizer: TokenizersBackend, compiled_loss: bool
) -> None:
    batch = LanceBatchCollator(tokenizer, "text").collate_fn([])
    assert batch["input_ids"].tolist() == [[tokenizer.pad_token_id]]
    assert batch["attention_mask"].tolist() == [[True]]
    assert batch["attention_mask"].dtype == torch.bool
    assert batch["labels"].tolist() == [[-100]]
    model = Olmo2(config)
    trainer = object.__new__(Trainer)
    trainer.runtime = DistributedRuntime(0, 0, 1, torch.device("cpu"))
    calc_loss = trainer._calc_batch_loss
    if compiled_loss:
        # Full-graph capture rejects graph breaks in the dummy loss path.
        calc_loss = torch.compile(calc_loss, backend="aot_eager", fullgraph=True)
    with torch.autocast("cpu", dtype=torch.bfloat16):
        logits = model(batch)
        loss, losses, tokens = calc_loss(logits, batch["labels"])
    assert torch.isfinite(logits).all()
    assert loss.item() == losses.item() == tokens.item() == 0
    loss.backward()
    for parameter in model.parameters():
        assert parameter.grad is not None
        torch.testing.assert_close(parameter.grad, torch.zeros_like(parameter), atol=0, rtol=0)


@pytest.mark.parametrize("logging_steps, expected_steps", [(1, [1, 2, 3]), (2, [2, 3]), (5, [3])])
def test_all_ignored_logging_windows_remain_finite(
    trainer_factory: TrainerFactory,
    monkeypatch: pytest.MonkeyPatch,
    logging_steps: int,
    expected_steps: list[int],
) -> None:
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
            "train/tokens": 0,
            "train/total_tokens": 0,
        }


def reference_epoch(
    model: BaseModel,
    batches: list[dict[str, torch.Tensor]],
    max_norm: float,
    model_dtype: torch.dtype,
) -> list[tuple[float, float, float]]:
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
        grads = []
        for parameter in model.parameters():
            assert parameter.grad is not None
            grads.append(parameter.grad)
        norm = torch.sqrt(cast(torch.Tensor, sum(grad.square().sum() for grad in grads)))
        assert norm.item() > max_norm, "fixture must exercise actual gradient clipping"
        scale = (max_norm / (norm + 1e-6)).clamp(max=1)
        for grad in grads:
            grad.mul_(scale)
        optimizer.param_groups[0]["lr"] = 0.01 * factor
        optimizer.step()
        records.append((loss.item(), valid.sum().item(), norm.item()))
    return records


@pytest.mark.parametrize("logging_steps", [1, 2, 5])
def test_training_updates_and_token_weighted_logging(
    trainer_factory: TrainerFactory,
    monkeypatch: pytest.MonkeyPatch,
    logging_steps: int,
) -> None:
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
                "train/tokens": sum(tokens for _, tokens, _ in window),
                "train/total_tokens": sum(
                    tokens for _, tokens, _ in records[: start + len(window)]
                ),
            }
        )
    # Hand count: left-padded rows of 4 and 2 words give 3 + 1 targets, 3 and 3 give 2 + 2,
    # and the final partial batch of one 2-word row gives 1.
    assert [tokens for _, tokens, _ in records] == [4, 4, 1]
    # Incomplete windows must use this trainer's run just like full windows.
    assert module_logs == [], "tail metrics must be sent to trainer.run.log"
    assert len(run.metrics) == len(expected_logs)
    for actual, expected in zip(run.metrics, expected_logs, strict=True):
        assert actual == pytest.approx(expected, abs=2e-5, rel=2e-5)


def test_token_counts_stay_exact_beyond_fp32_integers() -> None:
    state = TrainerState()
    one = torch.tensor(1.0)
    state.update(add_step=1, loss=one, grad_norm=one, tokens=torch.tensor(2**24 + 1))
    state.reset_window()
    state.update(add_step=1, loss=one, grad_norm=one, tokens=torch.tensor(2**24 + 1))
    metrics = state.get_logging_metrics()
    assert (metrics["train/tokens"], metrics["train/total_tokens"]) == (2**24 + 1, 2**25 + 2)


def test_state_does_not_advance_when_optimizer_step_fails(
    trainer_factory: TrainerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trainer, _, _ = trainer_factory(num_workers=0, prefetch_factor=None)

    def fail() -> Never:
        raise RuntimeError("optimizer step failed")

    monkeypatch.setattr(trainer.optimizer, "step", fail)
    with pytest.raises(RuntimeError, match="optimizer step failed"):
        trainer.train()
    assert trainer.state.step.item() == 0
    assert trainer.state.total_tokens.item() == 0
    assert trainer.state.window_steps.item() == 0
    assert trainer.lr_scheduler.completed_steps == 0


def test_training_loader_supports_zero_workers(
    trainer_factory: TrainerFactory,
) -> None:
    trainer, _, _ = trainer_factory(num_workers=0, prefetch_factor=None)
    loader = trainer._get_training_dataloader()
    assert loader.num_workers == 0
    assert loader.prefetch_factor is None
    assert loader.multiprocessing_context is None
    assert not loader.persistent_workers
    assert len(list(loader)) == trainer.total_steps


@pytest.mark.parametrize("prefetch_factor", [1, 2])
def test_zero_workers_reject_explicit_prefetch(
    trainer_factory: TrainerFactory,
    prefetch_factor: int,
) -> None:
    with pytest.raises(ValidationError, match="must be None"):
        trainer_factory(num_workers=0, prefetch_factor=prefetch_factor)


def test_worker_prefetch_can_use_pytorch_default(
    trainer_factory: TrainerFactory,
) -> None:
    trainer, _, _ = trainer_factory(num_workers=1, prefetch_factor=None)
    loader = trainer._get_training_dataloader()
    assert loader.prefetch_factor == 2
    assert loader.multiprocessing_context.get_start_method() == "spawn"
    assert loader.persistent_workers


def test_empty_dataset_is_rejected_without_an_optimizer_update(
    trainer_factory: TrainerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updates = []
    monkeypatch.setattr(torch.optim.AdamW, "step", lambda self: updates.append(True))
    with pytest.raises(ValueError, match="total_steps"):
        trainer_factory(dataset=[])
    assert updates == []


def test_real_spawned_loader_reads_temporary_lance_dataset(
    trainer_factory: TrainerFactory,
    tmp_path: Path,
) -> None:
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
    assert isinstance(iterator, _MultiProcessingDataLoaderIter)
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


@pytest.mark.parametrize("load", [False, True], ids=["preflight", "load"])
def test_trainer_state_api_delegates_component_validation_without_mutation(
    trainer_factory: TrainerFactory, monkeypatch: pytest.MonkeyPatch, load: bool
) -> None:
    trainer, _, _ = trainer_factory(num_workers=0, prefetch_factor=None)
    saved = copy.deepcopy(trainer.state_dict())
    expected = copy.deepcopy(saved)
    validated = []
    for name, component in (
        ("metrics", trainer.state),
        ("scheduler", trainer.lr_scheduler),
    ):
        validate = component.validate_state_dict

        def record(state: Any, name: str = name, validate: Any = validate) -> None:
            validated.append(name)
            validate(state)
            if name == "metrics":
                raise ValueError("metric validation rejected state")

        monkeypatch.setattr(component, "validate_state_dict", record)

    with pytest.raises(ValueError, match="metric validation"):
        if load:
            trainer.load_state_dict(saved)
        else:
            trainer.validate_state_dict(saved)
    assert set(validated) == {"metrics", "scheduler"}
    assert_nested_equal(trainer.state_dict(), expected)
    assert_nested_equal(saved, expected)


@pytest.mark.parametrize(
    "key,value",
    [
        ("step", torch.tensor(-1)),
        ("step", torch.tensor(1.5)),
        ("total_tokens", torch.tensor([2])),
        ("loss_sum", torch.ones(2)),
        ("grad_norm_sum", torch.tensor(2)),
    ],
)
def test_metric_state_rejects_malformed_buffers_before_copying(
    trainer_factory: TrainerFactory, key: str, value: torch.Tensor
) -> None:
    trainer, _, _ = trainer_factory(num_workers=0, prefetch_factor=None)
    expected = copy.deepcopy(trainer.state.state_dict())
    saved = copy.deepcopy(expected)
    saved["loss_max"] = torch.tensor(5.0)
    saved[key] = value
    for check in (trainer.state.validate_state_dict, trainer.state.load_state_dict):
        with pytest.raises(ValueError):
            check(saved)
        assert_nested_equal(trainer.state.state_dict(), expected)


def test_mutated_validated_state_is_revalidated_before_loading(
    trainer_factory: TrainerFactory,
) -> None:
    trainer, _, _ = trainer_factory(num_workers=0, prefetch_factor=None)
    expected = copy.deepcopy(trainer.state_dict())
    saved = trainer.validate_state_dict(copy.deepcopy(expected))
    saved.trainer_state_dict.total_tokens.fill_(-1)
    with pytest.raises(ValueError, match="counters"):
        trainer.load_state_dict(saved)
    assert_nested_equal(trainer.state_dict(), expected)


def test_checkpoint_manager_preserves_step_listing_and_selects_by_save_time(
    trainer_factory: TrainerFactory,
) -> None:
    trainer, _, _ = trainer_factory(num_workers=0, prefetch_factor=None)
    for step, saved_at in [(1, "2026-11-14T02:00:00Z"), (2, "2026-11-14T01:00:00Z")]:
        checkpoint = trainer.output_dir / f"checkpoint-{step}"
        checkpoint.mkdir()
        (checkpoint / "flock.json").write_text(json.dumps({"saved_at": saved_at}))
    (trainer.output_dir / ".checkpoint-unfinished").mkdir()
    (trainer.output_dir / "checkpoint-invalid").mkdir()
    assert [path.name for path in trainer.checkpoint_manager.checkpoints()] == [
        "checkpoint-1",
        "checkpoint-2",
    ]
    assert trainer.checkpoint_manager.latest_checkpoint() == trainer.output_dir / "checkpoint-1"


def test_chunk_training_resumes_weights_targets_and_sampler(
    trainer_factory: TrainerFactory,
    tokenizer: TokenizersBackend,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "chunk-source.lance"
    texts = ["a b c d " * 6, "b c d " * 5, "a b"]
    lance.write_dataset(pa.table({"text": texts}), str(source))
    chunks = TokenChunkDataset.prepare(
        dataset=SafeLanceDataset(str(source), dataset_options={"version": 1}),
        tokenizer=tokenizer,
        input_column="text",
        cache_dir=tmp_path / "tokens",
        chunk_length=4,
    )
    reference, _, _ = trainer_factory(
        dataset=chunks,
        batch_size=8,
        num_workers=0,
        prefetch_factor=None,
        output_dir=str(tmp_path / "reference"),
    )
    reference.train()
    assert reference.total_steps == math.ceil(len(chunks) / 8)
    assert int(reference.state.total_tokens) == sum(
        len(ids) - 1 for ids in tokenizer(texts, truncation=False)["input_ids"]
    )
    interrupted, _, _ = trainer_factory(
        dataset=chunks,
        batch_size=8,
        num_workers=0,
        prefetch_factor=None,
        output_dir=str(tmp_path / "interrupted"),
        save_steps=1,
    )
    save = interrupted.checkpoint_manager.save_model

    def save_and_interrupt(*args: Any, **kwargs: Any) -> None:
        save(*args, **kwargs)
        if int(interrupted.state.step) == 1:
            raise KeyboardInterrupt("completed first chunk batch")

    monkeypatch.setattr(interrupted.checkpoint_manager, "save_model", save_and_interrupt)
    with pytest.raises(KeyboardInterrupt):
        interrupted.train()
    resumed, _, _ = trainer_factory(
        dataset=TokenChunkDataset(cache_dir=tmp_path / "tokens", chunk_length=4),
        batch_size=8,
        num_workers=0,
        prefetch_factor=None,
        output_dir=str(interrupted.output_dir),
    )
    resumed.train(resume_from_checkpoint=True)
    assert_nested_equal(resumed.model.state_dict(), reference.model.state_dict())
    assert_nested_equal(resumed.state_dict(), reference.state_dict())
    # Length alone cannot detect a changed context when both runs have the same batches.
    changed, _, _ = trainer_factory(
        dataset=TokenChunkDataset(cache_dir=tmp_path / "tokens", chunk_length=5),
        batch_size=8,
        num_workers=0,
        prefetch_factor=None,
        output_dir=str(tmp_path / "changed"),
    )
    with pytest.raises(ValueError, match="data"):
        changed.validate_state_dict(resumed.state_dict())


def test_chunk_cache_rejects_changed_special_token_policy(
    trainer_factory: TrainerFactory,
    tokenizer: TokenizersBackend,
    tmp_path: Path,
) -> None:
    source = tmp_path / "special-source.lance"
    lance.write_dataset(pa.table({"text": ["a [EOS]"]}), str(source))
    chunks = TokenChunkDataset.prepare(
        dataset=SafeLanceDataset(str(source), dataset_options={"version": 1}),
        tokenizer=tokenizer,
        input_column="text",
        cache_dir=tmp_path / "original",
        chunk_length=8,
    )
    reference, _, _ = trainer_factory(dataset=chunks)
    checkpoint = reference.state_dict()
    digest = chunks.manifest["tokenizer"]
    tokenizer(["a", "a b c d"], padding=True, truncation=True, max_length=3)
    assert TokenChunkDataset.tokenizer_fingerprint(tokenizer) == digest

    tokenizer.split_special_tokens = True
    with pytest.raises(ValueError, match="tokenizer"):
        trainer_factory(dataset=chunks)
    changed = TokenChunkDataset.prepare(
        dataset=SafeLanceDataset(str(source), dataset_options={"version": 1}),
        tokenizer=tokenizer,
        input_column="text",
        cache_dir=tmp_path / "changed",
        chunk_length=8,
    )
    assert len(changed) == len(chunks)
    assert changed[0]["_flock_chunk_ids"] != chunks[0]["_flock_chunk_ids"]
    assert changed.fingerprint != chunks.fingerprint
    resumed, _, _ = trainer_factory(dataset=changed)
    with pytest.raises(ValueError, match="data"):
        resumed.validate_state_dict(checkpoint)


def packed_chunks(tmp_path: Path, tokenizer: TokenizersBackend) -> TokenChunkDataset:
    source = tmp_path / "packed-source.lance"
    texts = ["a b c d " * 6, "b c d " * 5, "a b", "c d a"]
    lance.write_dataset(pa.table({"text": texts}), str(source))
    return TokenChunkDataset.prepare(
        dataset=SafeLanceDataset(str(source), dataset_options={"version": 1}),
        tokenizer=tokenizer,
        input_column="text",
        cache_dir=tmp_path / "packed-tokens",
        chunk_length=4,
    )


def test_token_budget_training_covers_every_target_and_resumes(
    trainer_factory: TrainerFactory,
    tokenizer: TokenizersBackend,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunks = packed_chunks(tmp_path, tokenizer)
    options: dict[str, Any] = {
        "dataset": chunks,
        "batch_size": None,
        "batch_tokens": 7,
        "num_workers": 0,
        "prefetch_factor": None,
    }
    reference, _, _ = trainer_factory(**options, output_dir=str(tmp_path / "reference"))
    assert reference.token_plan is not None
    assert reference.total_steps == len(reference.token_plan)
    reference.train()
    assert int(reference.state.total_tokens) == int(chunks.target_counts().sum())

    interrupted, _, _ = trainer_factory(
        **options, output_dir=str(tmp_path / "interrupted"), save_steps=2
    )
    save = interrupted.checkpoint_manager.save_model

    def save_and_interrupt(*args: Any, **kwargs: Any) -> None:
        save(*args, **kwargs)
        raise KeyboardInterrupt("completed two packed updates")

    monkeypatch.setattr(interrupted.checkpoint_manager, "save_model", save_and_interrupt)
    with pytest.raises(KeyboardInterrupt):
        interrupted.train()
    resumed, _, _ = trainer_factory(**options, output_dir=str(interrupted.output_dir))
    resumed.train(resume_from_checkpoint=True)
    assert_nested_equal(resumed.model.state_dict(), reference.model.state_dict())
    assert_nested_equal(resumed.state_dict(), reference.state_dict())

    changed, _, _ = trainer_factory(
        **{**options, "batch_tokens": 9}, output_dir=str(tmp_path / "c")
    )
    with pytest.raises(ValueError, match="tokens"):
        changed.validate_state_dict(resumed.state_dict())


def test_token_budget_requires_a_chunk_cache_that_fits(
    trainer_factory: TrainerFactory, tokenizer: TokenizersBackend, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="TokenChunkDataset"):
        trainer_factory(batch_size=None, batch_tokens=8)
    with pytest.raises(ValueError, match="chunk_length"):
        trainer_factory(dataset=packed_chunks(tmp_path, tokenizer), batch_size=None, batch_tokens=2)


@pytest.mark.parametrize("batching", [{}, {"batch_size": 2, "batch_tokens": 8}])
def test_config_requires_exactly_one_batching_mode(batching: dict[str, int]) -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        TrainerConfig.model_validate(
            {
                "run_name": "run",
                "input_column": "text",
                "optimizer_config": AdamWConfig(lr=0.01, betas=(0.9, 0.95)),
                "lr_scheduler_config": LRSchedulerConfig(
                    schedule=[LRScheduleSegment(start=0, end=1.0, start_ratio=1, end_ratio=0)]
                ),
                "wandb_project": "test",
                **batching,
            }
        )
