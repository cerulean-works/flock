"""Exercise the compiled Trainer with Inductor, CUDA graphs, FA3 and resumable checkpoints."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest
import torch
from torch.nn import attention

from flock.eval.model import ScoringModel
from flock.eval.tasks import TASKS, Dataset, PerplexityTask
from flock.training.core.lr_scheduler import LRSchedulerConfig, LRScheduleSegment
from flock.training.core.models.olmo2 import Olmo2
from flock.training.core.optimizers import AdamWConfig
from flock.training.core.trainer import Trainer, TrainerConfig

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any

    from lance.torch.data import SafeLanceDataset
    from transformers import TokenizersBackend

    from flock.training.core.models.olmo2 import Olmo2Config

pytestmark = [pytest.mark.gpu, pytest.mark.fa3]


@pytest.mark.parametrize(
    "mode", ["default", "reduce-overhead", "max-autotune-no-cudagraphs", "max-autotune"]
)
def test_compiled_trainer_matches_eager_resumes_and_evaluates(
    config: Olmo2Config,
    tokenizer: TokenizersBackend,
    cuda_device: torch.device,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: str,
) -> None:
    previous = attention.current_flash_attention_impl()
    assert previous in (None, "FA3")
    torch.compiler.reset()
    model_config = replace(
        config,
        hidden_size=256,
        intermediate_size=384,
        num_key_value_heads=2,
        attn_implementation="flash_attention_3",
    )
    # Repeated shapes exercise CUDA graph reuse; the final batch has a different size.
    rows = [
        {"id": str(index), "text": text}
        for index, text in enumerate(["a b c d", "b c", "c d a", "a b c", "d a"] * 3)
    ]
    validation_rows = [{"id": str(index), "text": "a b c d"} for index in range(64)]
    task = PerplexityTask(
        "test_compile_text", Dataset("offline/compile", "0" * 40, None, "text"), window=8
    )
    monkeypatch.setitem(TASKS, task.name, task)
    monkeypatch.setattr(Dataset, "load", lambda self: validation_rows)
    monkeypatch.setattr(
        "flock.training.core.trainer.wandb.init",
        lambda **kwargs: Mock(entity="flock", project="offline-test", id="compile"),
    )

    def make(name: str, compiled: bool = False) -> Trainer:
        return Trainer(
            config=TrainerConfig.model_validate(
                {
                    "run_name": name,
                    "input_column": "text",
                    "batch_size": 2,
                    "optimizer_config": AdamWConfig(lr=5e-5, betas=(0.9, 0.95)),
                    "lr_scheduler_config": LRSchedulerConfig(
                        schedule=[
                            LRScheduleSegment(start=0, end=1, start_ratio=0.0, end_ratio=1.0),
                            LRScheduleSegment(
                                start=1,
                                end=1.0,
                                start_ratio=1.0,
                                end_ratio=0.1,
                                interpolation="cosine",
                            ),
                        ]
                    ),
                    "dataloader_num_workers": 0,
                    "dataloader_prefetch_factor": None,
                    "wandb_project": "offline-test",
                    "wandb_mode": "disabled",
                    "output_dir": str(tmp_path / name),
                    "save_steps": 1,
                    "validation_tasks": [task.name],
                    "validation_steps": 1,
                    "validation_batch_tokens": 8,
                    "torch_compile": compiled,
                    "torch_compile_mode": mode,
                }
            ),
            model=Olmo2(model_config),
            tokenizer=tokenizer,
            dataset=cast("SafeLanceDataset", rows),
        )

    try:
        eager = make("eager")
        assert eager.device == cuda_device
        eager.train()
        compiled = make("compiled", compiled=True)
        captures, scored_batches = [], []
        capture_begin = torch.cuda.CUDAGraph.capture_begin
        score = ScoringModel._score

        def record_capture(graph: torch.cuda.CUDAGraph, *args: Any, **kwargs: Any) -> None:
            captures.append(None)
            capture_begin(graph, *args, **kwargs)

        def record_score(scorer: ScoringModel, *args: Any, **kwargs: Any) -> Any:
            scored_batches.append(None)
            return score(scorer, *args, **kwargs)

        monkeypatch.setattr(torch.cuda.CUDAGraph, "capture_begin", record_capture)
        monkeypatch.setattr(ScoringModel, "_score", record_score)
        evaluate = compiled.evaluate

        def check_evaluation() -> None:
            # Evaluation between updates must leave the training gradients untouched.
            batches_before = len(scored_batches)
            gradients = []
            for parameter in compiled.model.parameters():
                assert parameter.grad is not None
                gradients.append(parameter.grad.clone())
            evaluate()
            assert len(scored_batches) - batches_before >= 32
            for parameter, gradient in zip(compiled.model.parameters(), gradients, strict=True):
                torch.testing.assert_close(parameter.grad, gradient, atol=0, rtol=0)

        monkeypatch.setattr(compiled, "evaluate", check_evaluation)
        # The eager checkpoint keeps float learning rates and CPU step counts.
        compiled.train(resume_from_checkpoint=eager.output_dir / "checkpoint-1")
        if mode in ("reduce-overhead", "max-autotune"):
            assert captures, "training must capture CUDA graphs"
        assert compiled.state.step.item() == compiled.lr_scheduler.completed_steps == 8
        assert compiled.model.training
        assert any(
            key.startswith("eval/")
            for call in cast(Mock, compiled.run).log.call_args_list
            for key in call.args[0]
        )
        for group in compiled.optimizer.param_groups:
            assert isinstance(group["lr"], torch.Tensor) and group["lr"].is_cuda
            assert group["capturable"]
        assert all(state["step"].is_cuda for state in compiled.optimizer.state.values())

        torch.testing.assert_close(
            compiled.model.state_dict(), eager.model.state_dict(), atol=2e-4, rtol=2e-2
        )
        grad_error = torch.zeros((), device=cuda_device)
        grad_reference = torch.zeros((), device=cuda_device)
        for actual, expected in zip(
            compiled.model.parameters(), eager.model.parameters(), strict=True
        ):
            assert actual.grad is not None and expected.grad is not None
            assert actual.grad.isfinite().all()
            grad_error += (actual.grad - expected.grad).square().sum()
            grad_reference += expected.grad.square().sum()
        assert (grad_error / grad_reference.clamp_min(1e-20)).sqrt().item() < 0.05

        # Compiled checkpoints use the eager format, so an eager run resumes them.
        saved = compiled.state_dict()["optimizer_state_dict"]
        for group in saved["param_groups"]:
            assert isinstance(group["lr"], float)
            assert group["capturable"] is False
        assert all(state["step"].device.type == "cpu" for state in saved["state"].values())
        restored = make("restored")
        manager = restored.checkpoint_manager
        assert manager is not None
        restored.load_state_dict(
            manager.load_checkpoint(
                compiled.output_dir / "checkpoint-8",
                restored.model,
                validate_state=restored.validate_state_dict,
            )
        )
        torch.testing.assert_close(
            restored.model.state_dict(), compiled.model.state_dict(), atol=0, rtol=0
        )
        torch.testing.assert_close(restored.state_dict()["optimizer_state_dict"], saved)

        # Changing the learning rate must not recompile the clipped update. Dynamo's own
        # patch restores its config; pytest 9.2's monkeypatch would hide the key.
        with torch._dynamo.config.patch(error_on_recompile=True):
            for value in (1e-5, 2e-5):
                compiled._mark_step()
                for parameter in compiled.model.parameters():
                    parameter.grad = torch.ones_like(parameter)
                for group in compiled.optimizer.param_groups:
                    group["lr"].fill_(value)
                compiled._train_update()
    finally:
        torch.compiler.reset()
        if previous is None and attention.current_flash_attention_impl() == "FA3":
            attention.restore_flash_attention_impl()
