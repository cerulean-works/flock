from dataclasses import dataclass
from math import cos, pi

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
