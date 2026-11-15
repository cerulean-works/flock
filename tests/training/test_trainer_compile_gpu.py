"""Exercise the actual Trainer with Inductor, FA3 and resumable checkpoints."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest
import torch
from torch.nn import attention

from flock.eval.tasks import TASKS, Dataset, PerplexityTask
from flock.training.core.lr_scheduler import LRSchedulerConfig, LRScheduleSegment
from flock.training.core.models.olmo2 import Olmo2
from flock.training.core.optimizers import AdamWConfig
from flock.training.core.trainer import Trainer, TrainerConfig

if TYPE_CHECKING:
    from pathlib import Path

    from lance.torch.data import SafeLanceDataset
    from transformers import TokenizersBackend

    from flock.training.core.models.olmo2 import Olmo2Config

pytestmark = [pytest.mark.gpu, pytest.mark.fa3]


@pytest.mark.parametrize("mode", ["default", "reduce-overhead"])
def test_compiled_trainer_fa3_resume_and_evaluate(
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
    task = PerplexityTask(
        "test_compile_text", Dataset("offline/compile", "0" * 40, None, "text"), window=8
    )
    monkeypatch.setitem(TASKS, task.name, task)
    monkeypatch.setattr(Dataset, "load", lambda self: rows)
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
        compiled.train(resume_from_checkpoint=eager.output_dir / "checkpoint-1")
        assert compiled._compiled_loss is not None
        assert compiled.state.step.item() == compiled.lr_scheduler.completed_steps == 8
        assert compiled.model.training
        assert any(
            key.startswith("eval/")
            for call in cast(Mock, compiled.run).log.call_args_list
            for key in call.args[0]
        )
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
        restored = make("restored")
        restored.checkpoint_manager.load_checkpoint(compiled.output_dir / "checkpoint-8")
        torch.testing.assert_close(
            restored.model.state_dict(), compiled.model.state_dict(), atol=0, rtol=0
        )
        torch.testing.assert_close(
            restored.optimizer.state_dict(), compiled.optimizer.state_dict(), atol=0, rtol=0
        )
    finally:
        torch.compiler.reset()
        if previous is None and attention.current_flash_attention_impl() == "FA3":
            attention.restore_flash_attention_impl()
