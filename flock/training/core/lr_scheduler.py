"""Apply update-indexed warmup and cosine decay to optimizer learning rates."""

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from math import cos, pi
from typing import Any

from torch.optim import Optimizer


@dataclass(frozen=True, kw_only=True)
class CosineConfig:
    """Configure linear warmup followed by cosine decay to the run endpoint.

    Args:
        warmup_steps (`int`): Warmup updates. Use zero to start at the base rate.
        min_lr_ratio (`float`): Endpoint learning rate divided by the base rate.

    Choose `0 <= warmup_steps < total_steps` and `0 <= min_lr_ratio <= 1`.
    `Trainer.train` rejects warmup that does not fit the run; the ratio is not validated.
    """

    warmup_steps: int
    min_lr_ratio: float


@dataclass(frozen=True, kw_only=True)
class WSDConfig:
    """Configure linear warmup, a stable base rate and final cosine decay.

    Args:
        warmup_steps (`int`): Warmup updates, starting at zero learning rate.
        decay_steps (`int`): Number of final updates assigned to cosine decay.
        min_lr_ratio (`float`, *optional*, defaults to `0.0`): Endpoint/base rate ratio.

    Choose nonnegative warmup, positive decay, `warmup_steps + decay_steps <=
    total_steps` and a ratio between zero and one. `Trainer.train` rejects
    warmup and decay that do not fit the run; the ratio is not validated.
    """

    warmup_steps: int
    decay_steps: int
    min_lr_ratio: float = 0.0


type LRSchedulerConfig = CosineConfig | WSDConfig


def check_schedule_fits(config: LRSchedulerConfig, total_steps: int) -> None:
    """Reject warmup and decay lengths that do not fit `total_steps` updates.

    Args:
        config (`LRSchedulerConfig`): Warmup and decay settings.
        total_steps (`int`): Total planned optimizer updates.

    Raises:
        `ValueError`: If cosine warmup does not end before the last update, or WSD warmup and decay do not fit the run.
    """
    if config.warmup_steps < 0:
        raise ValueError("warmup_steps must be nonnegative")
    if isinstance(config, WSDConfig):
        if config.decay_steps <= 0 or config.warmup_steps + config.decay_steps > total_steps:
            raise ValueError(
                f"WSD needs positive decay_steps and warmup_steps + decay_steps <= {total_steps} "
                f"planned updates, got {config.warmup_steps} + {config.decay_steps}"
            )
    elif config.warmup_steps >= total_steps:
        raise ValueError(
            f"Cosine warmup_steps must be below the {total_steps} planned updates, "
            f"got {config.warmup_steps}"
        )


def lr_multiplier(step: int, config: LRSchedulerConfig, total_steps: int) -> float:
    """Return the learning-rate multiplier after `step` completed updates.

    Args:
        step (`int`): Nonnegative number of completed optimizer updates.
        config (`LRSchedulerConfig`): Warmup and decay settings.
        total_steps (`int`): Total planned optimizer updates.

    Returns:
        `float`: Factor applied to every group's original learning rate. Step
        zero has a zero factor when warmup is enabled. Decay stops at `min_lr_ratio`.

    Raises:
        `ValueError`: If `step` is negative.
        `TypeError`: If the scheduler configuration is unsupported.
    """
    if step < 0:
        raise ValueError("step must be nonnegative")

    if isinstance(config, CosineConfig):
        decay_start = config.warmup_steps
    elif isinstance(config, WSDConfig):
        decay_start = total_steps - config.decay_steps
    else:
        raise TypeError(f"Unsupported scheduler config: {type(config).__name__}")

    if step < config.warmup_steps:
        return step / config.warmup_steps

    if step <= decay_start:
        return 1.0

    progress = min(1.0, (step - decay_start) / (total_steps - decay_start))
    cosine = 0.5 * (1.0 + cos(pi * progress))
    return config.min_lr_ratio + (1.0 - config.min_lr_ratio) * cosine


class StepLRScheduler:
    """Apply a shared schedule multiplier to each optimizer group's base rate.

    Construction immediately sets the rate for zero completed updates. Call
    `step()` after each optimizer update to prepare the next update's rate.

    Args:
        optimizer (`Optimizer`): Optimizer whose current rates become base rates.
        config (`LRSchedulerConfig`): Cosine or warmup-stable-decay settings.
        total_steps (`int`): Planned number of optimizer updates.
    """

    def __init__(
        self,
        optimizer: Optimizer,
        config: LRSchedulerConfig,
        total_steps: int,
    ) -> None:
        """Capture base rates and apply the initial schedule multiplier."""
        self.optimizer = optimizer
        self.config = config
        self.total_steps = total_steps
        self.base_lrs = [float(group["lr"]) for group in optimizer.param_groups]
        self.completed_steps = 0
        self._apply_lr()

    def _apply_lr(self) -> None:
        """Set group rates for completed progress, rejecting changed group counts."""
        if len(self.optimizer.param_groups) != len(self.base_lrs):
            raise ValueError("Optimizer parameter groups changed after scheduler creation")
        factor = lr_multiplier(self.completed_steps, self.config, self.total_steps)
        for group, base_lr in zip(self.optimizer.param_groups, self.base_lrs):
            group["lr"] = base_lr * factor

    def step(self) -> None:
        """Advance after one optimizer update; prepares LR for the next update."""
        self.completed_steps += 1
        self._apply_lr()

    def get_last_lr(self) -> list[float]:
        """Return the current learning rate of each optimizer parameter group."""
        return [float(group["lr"]) for group in self.optimizer.param_groups]

    def state_dict(self) -> dict[str, Any]:
        """Return the schedule and progress as plain values, without the optimizer."""
        return {
            "config": {
                "type": "cosine" if isinstance(self.config, CosineConfig) else "wsd",
                **asdict(self.config),
            },
            "total_steps": self.total_steps,
            "base_lrs": self.base_lrs.copy(),
            "completed_steps": self.completed_steps,
        }

    def validate_state_dict(self, state_dict: Mapping[str, Any]) -> None:
        """Check saved settings without changing the scheduler or optimizer.

        Args:
            state_dict (`Mapping[str, Any]`): Mapping produced by `state_dict()`.

        Raises:
            `ValueError`: If the saved schedule, base rates, group count or total
                steps differ, or completed steps are negative.
        """
        config_fields = dict(state_dict["config"])
        config_type = config_fields.pop("type")
        if config_type == "cosine":
            config = CosineConfig(**config_fields)
        elif config_type == "wsd":
            config = WSDConfig(**config_fields)
        else:
            raise ValueError(f"Unsupported scheduler config type: {config_type}")

        base_lrs = list(state_dict["base_lrs"])
        if len(base_lrs) != len(self.optimizer.param_groups):
            raise ValueError("Optimizer parameter groups do not match the saved scheduler")
        completed_steps = state_dict["completed_steps"]
        if completed_steps < 0:
            raise ValueError("completed_steps must be nonnegative")

        if config != self.config:
            raise ValueError(
                f"Scheduler config does not match the saved scheduler: {config} != {self.config}"
            )
        if state_dict["total_steps"] != self.total_steps:
            raise ValueError("Scheduler total_steps does not match the saved scheduler")
        if base_lrs != self.base_lrs:
            raise ValueError("Scheduler base_lrs do not match the saved scheduler")

    def load_state_dict(self, state_dict: Mapping[str, Any]) -> None:
        """Restore progress and apply the learning rates for the next update.

        Restore the optimizer's state before calling this method.

        Args:
            state_dict (`Mapping[str, Any]`): Mapping produced by `state_dict()`.

        Raises:
            `ValueError`: If saved settings fail `validate_state_dict`.
        """
        self.validate_state_dict(state_dict)
        self.completed_steps = state_dict["completed_steps"]
        self._apply_lr()
