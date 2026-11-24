"""Save when a run reaches given token counts, and stop at its token budget."""

import json
import math
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path

import numpy as np
from transformers import TokenizersBackend

from flock.training import TokenChunkDataset, Trainer, TrainerConfig
from flock.training.core.data_utils import Batch, ResumableBatchDataSampler, TokenBatchPlan
from flock.training.core.distributed import DistributedRuntime
from flock.training.core.models import BaseModel
from flock.training.core.streaming import TextStream

from .evals import SaveEvaluator, append_record
from .wsd import TokenRounding, WSDSettings


def step_target_counts(
    config: TrainerConfig,
    dataset: TokenChunkDataset | TextStream,
    world_size: int,
    *,
    up_to: int = 0,
) -> np.ndarray:
    """Return the targets each global update trains on.

    For a token cache, these cover one full pass and come from the same seeded plan the
    trainer's sampler follows. A text stream fills every row, so each update trains on
    exactly `batch_tokens * world_size` targets; the counts then run until `up_to`
    targets. Either way they match `train/total_tokens` without reading back from GPUs.
    """
    if isinstance(dataset, TextStream):
        if config.batch_tokens is None:
            raise ValueError("A text stream needs batch_tokens")
        per_update = config.batch_tokens * world_size
        return np.full(max(1, math.ceil(up_to / per_update)), per_update, dtype=np.int64)
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


def steps_for_tokens(
    counts: np.ndarray, targets: Sequence[int], rounding: TokenRounding = "first_reaching"
) -> list[int]:
    """Return, for each target, the update after which the run has trained on it.

    With `"first_reaching"`, that is the first update reaching the target. With `"nearest"`,
    it is the update whose cumulative count is closest to it (ties go to the later update),
    never before the first.

    Raises:
        `ValueError`: If a target exceeds the tokens in one pass.
    """
    cumulative = np.cumsum(counts)
    available = int(cumulative[-1]) if len(cumulative) else 0
    if any(target > available for target in targets):
        raise ValueError(
            f"One pass over the dataset trains on {available} tokens, fewer than {max(targets)}"
        )
    steps = []
    for target in targets:
        index = int(np.searchsorted(cumulative, target, side="left"))
        if rounding == "nearest" and index > 0:
            below = int(cumulative[index - 1])
            if target - below < int(cumulative[index]) - target:
                index -= 1
        steps.append(index + 1)
    return steps


class TokenBudgetTrainer(Trainer):
    """Train until a token budget, saving a checkpoint whenever a token target is reached.

    Construction maps each target to the first update that reaches it (or the nearest
    update), using the sampler's seeded plan. The run plans `max_steps` as the update for
    `stop_tokens`, so fractional learning-rate schedules end at the budget.
    With each save, rank zero writes `token_checkpoints.json` beside the
    checkpoints, recording each target, its update and the tokens trained by then.
    An optional `evaluator` scores the weights on every rank just before each save and
    logs `eval/*` at that update; a failed evaluation is logged and the save still happens.

    Args:
        config ([`training.TrainerConfig`]): Training settings. `max_steps` must be unset.
        model ([`training.core.models.BaseModel`]): Model to train.
        tokenizer (`TokenizersBackend`): Tokenizer that built the cache.
        dataset ([`training.TokenChunkDataset`] or `TextStream`): Token cache, whose lengths
            fix the plan, or a text stream, whose updates each train on `batch_tokens` per rank.
        stop_tokens (`int`): Train on at least this many targets, save, then stop.
        save_tokens (`Sequence[int]`, *optional*): Further counts to save at.
        runtime ([`training.core.distributed.DistributedRuntime`], *optional*):
            Distributed runtime. Detected from the environment when omitted.
        rounding (`str`, *optional*, defaults to `"first_reaching"`): `"first_reaching"` or
            `"nearest"` update for each token target.
        wsd ([`WSDSettings`], *optional*): Replace the configured schedule with a
            warmup-stable-decay schedule whose boundaries are token counts.
        one_pass_tokens (`int`, *optional*): Refuse a budget that trains on more targets.
            Also the denominator of `train/epoch_pct`.
        nominal_params (`int`, *optional*): Denominator of `train/tokens_per_param`.
        evaluator ([`SaveEvaluator`], *optional*): Scores the weights before each save.

    Raises:
        `ValueError`: If the budget exceeds `one_pass_tokens`, or the WSD schedule does
            not fit the run.
    """

    def __init__(
        self,
        *,
        config: TrainerConfig,
        model: BaseModel,
        tokenizer: TokenizersBackend,
        dataset: TokenChunkDataset | TextStream,
        stop_tokens: int,
        save_tokens: Sequence[int] = (),
        runtime: DistributedRuntime | None = None,
        rounding: TokenRounding = "first_reaching",
        wsd: WSDSettings | None = None,
        one_pass_tokens: int | None = None,
        nominal_params: int | None = None,
        evaluator: SaveEvaluator | None = None,
    ) -> None:
        """Map token targets to updates, then build the trainer capped at the budget."""
        if config.max_steps is not None:
            raise ValueError("TokenBudgetTrainer sets max_steps from stop_tokens")
        runtime = runtime if runtime is not None else DistributedRuntime.from_env()
        targets = sorted({*save_tokens, stop_tokens})
        counts = step_target_counts(config, dataset, runtime.world_size, up_to=targets[-1])
        steps = steps_for_tokens(counts, targets, rounding)
        cumulative = np.cumsum(counts)
        self.token_checkpoints = [
            {"tokens": target, "step": step, "trained_tokens": int(cumulative[step - 1])}
            for target, step in zip(targets, steps, strict=True)
        ]
        final_tokens = self.token_checkpoints[-1]["trained_tokens"]
        if one_pass_tokens is not None and final_tokens > one_pass_tokens:
            raise ValueError(
                f"The run would train on {final_tokens} tokens, more than one pass "
                f"({one_pass_tokens}); lower stop_tokens"
            )
        if wsd is not None:
            if not isinstance(dataset, TextStream):
                raise ValueError("A token-based WSD schedule needs a text stream's fixed updates")
            assert config.batch_tokens is not None
            schedule = wsd.schedule(stop_tokens, config.batch_tokens * runtime.world_size, rounding)
            if schedule.schedule[-1].end != steps[-1]:
                raise ValueError("The WSD schedule must end at the final update")
            config = config.model_copy(update={"lr_scheduler_config": schedule})
        self.one_pass_tokens = one_pass_tokens
        self.nominal_params = nominal_params
        self.evaluator = evaluator
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

    def _log_metrics(self, metrics_dict: dict[str, int | float]) -> None:
        """Add the share of one pass and tokens per parameter to training metrics."""
        total = metrics_dict.get("train/total_tokens")
        if total is not None:
            if self.one_pass_tokens is not None:
                metrics_dict["train/epoch_pct"] = 100 * total / self.one_pass_tokens
            if self.nominal_params is not None:
                metrics_dict["train/tokens_per_param"] = total / self.nominal_params
        super()._log_metrics(metrics_dict)

    def _evaluate_before_save(self, step: int) -> None:
        """Score the weights about to be saved; log and record the outcome, never raise."""
        if self.evaluator is None:
            return
        try:
            outcome = self.evaluator.evaluate(
                self.model, self.tokenizer, name=f"{self.run_name}@step-{step}"
            )
        except Exception:  # An eval must never stop training.
            self.logger.exception("Save-time evaluation failed at step %d; saving anyway", step)
            return
        if not self.runtime.is_master:
            return
        trained = int(self.state.total_tokens.item())
        metrics: dict[str, int | float] = {"global_step": step, **outcome.metrics}
        metrics["eval/total_tokens"] = trained
        if self.nominal_params is not None:
            metrics["eval/tokens_per_param"] = trained / self.nominal_params
        if outcome.error is not None:
            self.logger.error("Save-time evaluation failed at step %d: %s", step, outcome.error)
        try:
            if self.run is not None:
                self.run.config.update(
                    {"eval_flock_commit": outcome.record["flock_commit"]}, allow_val_change=True
                )
            self._log_metrics(metrics)
            append_record(
                Path(self.output_dir) / "evals.jsonl",
                {"step": step, "trained_tokens": trained, **outcome.record},
            )
        except Exception:
            self.logger.exception("Could not log the save-time evaluation at step %d", step)

    def _save_model(self, step: int, blocking: bool = False) -> None:
        """Save each update at most once, whichever rule asks first, evaluating just before."""
        if step in self._saved_steps:
            return
        self._saved_steps.add(step)
        self._evaluate_before_save(step)
        super()._save_model(step, blocking=blocking)
        if self.runtime.is_master:
            # Written with a checkpoint, so a fresh run's directory stays empty until then.
            record = Path(self.output_dir) / "token_checkpoints.json"
            record.write_text(json.dumps(self.token_checkpoints, indent=2) + "\n")
