"""Save when a run reaches given token counts, and stop at its token budget."""

import json
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path

import numpy as np
from transformers import TokenizersBackend

from flock.training import TokenChunkDataset, Trainer, TrainerConfig
from flock.training.core.data_utils import Batch, ResumableBatchDataSampler, TokenBatchPlan
from flock.training.core.distributed import DistributedRuntime
from flock.training.core.models import BaseModel


def step_target_counts(
    config: TrainerConfig, dataset: TokenChunkDataset, world_size: int
) -> np.ndarray:
    """Return the targets each global update of one full pass trains on.

    The counts come from the same seeded plan the trainer's sampler follows, so
    they match `train/total_tokens` exactly without reading anything back from GPUs.
    """
    lengths = dataset.target_counts()
    if config.batch_tokens is not None:
        plan = TokenBatchPlan.build(lengths, config.batch_tokens, seed=config.seed)
        return plan.step_target_counts(world_size)
    assert config.batch_size is not None
    sampler = ResumableBatchDataSampler(
        batch_size=config.batch_size,
        dataset_size=len(dataset),
        seed=config.seed,
        world_size=world_size,
    )
    return sampler.step_target_counts(lengths)


def steps_for_tokens(counts: np.ndarray, targets: Sequence[int]) -> list[int]:
    """Return, for each target, the first update after which the run has trained on it.

    Raises:
        `ValueError`: If a target exceeds the tokens in one pass.
    """
    cumulative = np.cumsum(counts)
    available = int(cumulative[-1]) if len(cumulative) else 0
    if any(target > available for target in targets):
        raise ValueError(
            f"One pass over the dataset trains on {available} tokens, fewer than {max(targets)}"
        )
    return [int(np.searchsorted(cumulative, target, side="left")) + 1 for target in targets]


class TokenBudgetTrainer(Trainer):
    """Train until a token budget, saving a checkpoint whenever a token target is reached.

    Construction maps each target to the first update that reaches it, using the
    sampler's seeded plan. The run plans `max_steps` as the update reaching
    `stop_tokens`, so fractional learning-rate schedules end at the budget.
    With each save, rank zero writes `token_checkpoints.json` beside the
    checkpoints, recording each target, its update and the tokens trained by then.

    Args:
        config ([`training.TrainerConfig`]): Training settings. `max_steps` must be unset.
        model ([`training.core.models.BaseModel`]): Model to train.
        tokenizer (`TokenizersBackend`): Tokenizer that built the cache.
        dataset ([`training.TokenChunkDataset`]): Token cache; its lengths fix the plan.
        stop_tokens (`int`): Train on at least this many targets, save, then stop.
        save_tokens (`Sequence[int]`, *optional*): Further counts to save at.
        runtime ([`training.core.distributed.DistributedRuntime`], *optional*):
            Distributed runtime. Detected from the environment when omitted.
    """

    def __init__(
        self,
        *,
        config: TrainerConfig,
        model: BaseModel,
        tokenizer: TokenizersBackend,
        dataset: TokenChunkDataset,
        stop_tokens: int,
        save_tokens: Sequence[int] = (),
        runtime: DistributedRuntime | None = None,
    ) -> None:
        """Map token targets to updates, then build the trainer capped at the budget."""
        if config.max_steps is not None:
            raise ValueError("TokenBudgetTrainer sets max_steps from stop_tokens")
        runtime = runtime if runtime is not None else DistributedRuntime.from_env()
        targets = sorted({*save_tokens, stop_tokens})
        counts = step_target_counts(config, dataset, runtime.world_size)
        steps = steps_for_tokens(counts, targets)
        cumulative = np.cumsum(counts)
        self.token_checkpoints = [
            {"tokens": target, "step": step, "trained_tokens": int(cumulative[step - 1])}
            for target, step in zip(targets, steps, strict=True)
        ]
        self.token_save_steps = set(steps)
        self._saved_steps: set[int] = set()
        super().__init__(
            config=config.model_copy(update={"max_steps": steps[-1]}),
            model=model,
            tokenizer=tokenizer,
            dataset=dataset,
            runtime=runtime,
        )

    def _inner_training_loop(self, dataloader: Iterable[Batch], start_step: int) -> None:
        """Run the normal loop, saving after each update that reaches a token target."""
        super()._inner_training_loop(self._save_at_targets(dataloader, start_step), start_step)

    def _save_at_targets(self, dataloader: Iterable[Batch], start_step: int) -> Iterator[Batch]:
        step = start_step
        for batch in dataloader:
            yield batch
            # The loop asks for the next batch only after finishing this update.
            step += 1
            if step in self.token_save_steps:
                self._save_model(step)

    def _save_model(self, step: int, blocking: bool = False) -> None:
        """Save each update at most once, whichever rule asks first."""
        if step in self._saved_steps:
            return
        self._saved_steps.add(step)
        super()._save_model(step, blocking=blocking)
        if self.runtime.is_master:
            # Written with a checkpoint, so a fresh run's directory stays empty until then.
            record = Path(self.output_dir) / "token_checkpoints.json"
            record.write_text(json.dumps(self.token_checkpoints, indent=2) + "\n")
