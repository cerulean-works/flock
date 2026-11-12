from collections.abc import Mapping
from dataclasses import asdict, dataclass
from math import cos, pi
from typing import Any

from torch.optim import Optimizer


@dataclass(frozen=True, kw_only=True)
class CosineConfig:
    warmup_steps: int
    min_lr_ratio: float


@dataclass(frozen=True, kw_only=True)
class WSDConfig:
    warmup_steps: int
    decay_steps: int
    min_lr_ratio: float = 0.0


type LRSchedulerConfig = CosineConfig | WSDConfig


def lr_multiplier(step: int, config: LRSchedulerConfig, total_steps: int) -> float:
    """Return a multiplier for each parameter group's original learning rate."""
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
    """LR schedulers that apply directly to an existing PyTorch optimizer."""

    def __init__(
        self,
        optimizer: Optimizer,
        config: LRSchedulerConfig,
        total_steps: int,
    ) -> None:
        self.optimizer = optimizer
        self.config = config
        self.total_steps = total_steps
        self.base_lrs = [float(group["lr"]) for group in optimizer.param_groups]
        self.completed_steps = 0
        self._apply_lr()

    def _apply_lr(self) -> None:
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
        """Check saved schedule settings without changing the scheduler or optimizer."""
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
        """
        self.validate_state_dict(state_dict)
        self.completed_steps = state_dict["completed_steps"]
        self._apply_lr()
