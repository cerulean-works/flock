import logging
from typing import cast

import pytest
import torch
from lance.torch.data import SafeLanceDataset

from flock.training.core.lr_scheduler import CosineConfig
from flock.training.core.models.olmo2 import Olmo2
from flock.training.core.optimization import AdamWConfig
from flock.training.core.trainer import Trainer, TrainerConfig
from flock.training.experiments.baselines.throughput import StepClock, TrainerMetrics


@pytest.mark.parametrize("batch_size", [1, 2, 3])
def test_step_clock_counts_every_real_input_token_once(
    config, tokenizer, monkeypatch, caplog, batch_size
):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(torch.mps, "is_available", lambda: False)
    monkeypatch.setattr("flock.training.core.trainer.wandb.init", lambda **kwargs: _NoRun())
    texts = ["a b c d", "b c", "c d a b c d a", "a b c", "d a", "c a", "b c d a b"]
    trainer = Trainer(
        config=TrainerConfig(
            input_column="text",
            batch_size=batch_size,
            optimizer_config=AdamWConfig(lr=0.01, betas=(0.8, 0.9)),
            lr_scheduler_config=CosineConfig(warmup_steps=0, min_lr_ratio=0.1),
            dataloader_num_workers=0,
            dataloader_prefetch_factor=None,
            wandb_project="offline-test",
            wandb_mode="disabled",
            logging_steps=2,
        ),
        model=Olmo2(config),
        tokenizer=tokenizer,
        dataset=cast(SafeLanceDataset, [{"text": text} for text in texts]),
    )
    clock = StepClock(trainer, warmup=1)
    metrics = TrainerMetrics()
    caplog.set_level(logging.INFO, logger="flock.training.core.trainer")
    logging.getLogger("flock.training.core.trainer").addHandler(metrics)
    try:
        trainer.train()
    finally:
        logging.getLogger("flock.training.core.trainer").removeHandler(metrics)

    # The collator drops each row's last position, which left padding keeps real.
    real_inputs = [len(tokenizer(text)["input_ids"]) - 1 for text in texts]
    assert len(clock.step_seconds) == len(clock.tokens) == trainer.total_steps
    assert clock.events == []  # CUDA events only exist on CUDA; the CPU clock is the wall.
    assert int(torch.stack(clock.tokens).sum()) == sum(real_inputs)
    assert sum(rows for rows, _ in clock.shapes) == len(texts)

    first_batch = int(clock.tokens[0])
    assert clock.window_tokens == sum(real_inputs) - first_batch
    assert clock.window_seconds == clock.step_seconds[1:]
    assert clock.tokens_per_second == pytest.approx(clock.window_tokens / sum(clock.window_seconds))
    assert clock.window_padded_tokens >= clock.window_tokens
    assert clock.peak_bytes == []  # Memory is only read on CUDA.
    assert [window["global_step"] for window in metrics.windows] == list(
        range(2, trainer.total_steps + 1, 2)
    ) + ([trainer.total_steps] if trainer.total_steps % 2 else [])


class _NoRun:
    def define_metric(self, *args, **kwargs):
        pass

    def log(self, metrics):
        pass
