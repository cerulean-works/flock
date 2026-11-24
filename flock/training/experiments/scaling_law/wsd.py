"""Warmup-stable-decay schedules in tokens, and where a WSD run and its decay branches save.

A WSD run warms up linearly over `warmup_tokens`, holds the peak rate, then decays over the
last `decay_fraction` of its tokens to `final_ratio` of the peak. Every boundary is a token
count, rounded to the nearest optimizer update the same way saves are, so a decay branch that
starts from a checkpoint saved at `(1 - decay_fraction) * T` starts its decay exactly at that
checkpoint's update.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from flock.training import LRSchedulerConfig, LRScheduleSegment

type TokenRounding = Literal["first_reaching", "nearest"]
"""How a token count maps to an update: the first update that reaches it, or the nearest one."""


def tokens_to_step(tokens: int, per_update: int, rounding: TokenRounding) -> int:
    """Return the update after which a run that trains `per_update` targets per update has
    trained about `tokens` targets: the first one reaching it, or the nearest (ties go up).
    Never less than one.
    """
    if tokens < 0 or per_update < 1:
        raise ValueError("tokens must be nonnegative and per_update positive")
    if rounding == "first_reaching":
        step = -(-tokens // per_update)
    else:
        step = (2 * tokens + per_update) // (2 * per_update)
    return max(1, step)


class WSDSettings(BaseModel):
    """Warmup-stable-decay schedule, with boundaries in tokens.

    Args:
        warmup_tokens (`int`): Linear warmup from zero to the peak rates.
        decay_fraction (`float`, *optional*, defaults to `0.1`): Final share of the run's
            tokens spent decaying.
        final_ratio (`float`, *optional*, defaults to `0.01`): Rate at the end of the decay,
            as a share of the peak, for every optimizer group.
        decay (`str`, *optional*, defaults to `"cosine"`): `"cosine"` or `"linear"` decay.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    warmup_tokens: int = Field(gt=0)
    decay_fraction: float = Field(default=0.1, gt=0, lt=1)
    final_ratio: float = Field(default=0.01, ge=0, le=1)
    decay: Literal["cosine", "linear"] = "cosine"

    def decay_start_tokens(self, stop_tokens: int) -> int:
        """Return the token count where the decay of a run ending at `stop_tokens` begins."""
        return round(stop_tokens * (1 - self.decay_fraction))

    def schedule(
        self, stop_tokens: int, per_update: int, rounding: TokenRounding = "nearest"
    ) -> LRSchedulerConfig:
        """Return the schedule in whole updates for a run that stops at `stop_tokens`.

        Raises:
            `ValueError`: If warmup does not end before the decay starts.
        """
        warmup = tokens_to_step(self.warmup_tokens, per_update, rounding)
        decay = tokens_to_step(self.decay_start_tokens(stop_tokens), per_update, rounding)
        stop = tokens_to_step(stop_tokens, per_update, rounding)
        if not warmup < decay < stop:
            raise ValueError(
                f"WSD needs warmup ({warmup}) < decay start ({decay}) < stop ({stop}) updates"
            )
        return LRSchedulerConfig(
            schedule=(
                LRScheduleSegment(start=0, end=warmup, start_ratio=0.0, end_ratio=1.0),
                LRScheduleSegment(start=warmup, end=decay, start_ratio=1.0, end_ratio=1.0),
                LRScheduleSegment(
                    start=decay,
                    end=stop,
                    start_ratio=1.0,
                    end_ratio=self.final_ratio,
                    interpolation=self.decay,
                ),
            )
        )


@dataclass(frozen=True)
class SavePoint:
    """One planned save: its token target, the update it lands on and the tokens trained by then.

    Args:
        tokens (`int`): Target token count.
        step (`int`): Update the save happens after.
        trained_tokens (`int`): Targets trained by the end of `step`.
        reasons (`tuple[str, ...]`): Why it is saved, such as `"T=40"`, `"branch T=40"`,
            `"recovery 15%"` or `"stop"`.
    """

    tokens: int
    step: int
    trained_tokens: int
    reasons: tuple[str, ...]


class WSDPlan(BaseModel):
    """Saves of a WSD main run, and the decay branches that start from them.

    Args:
        nominal_params (`int`): Parameter count that tokens per parameter are measured in.
        stop_tokens (`int`): Main run's budget.
        targets (`list[float]`): Tokens per parameter `T`. Each `T * nominal_params` within the
            budget is saved at `T` and at the start of its decay branch. Every such branch
            starts at or before the main run's own decay, so from a checkpoint still at peak.
        recovery_tokens (`int`): Also save every this many tokens, for crash recovery.
        per_update (`int`): Targets each update trains, across all ranks.
        decay_fraction (`float`, *optional*, defaults to `0.1`): Share of `T` a branch decays over.
        rounding (`str`, *optional*, defaults to `"nearest"`): Token-to-update rounding.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    nominal_params: int = Field(gt=0)
    stop_tokens: int = Field(gt=0)
    targets: list[float] = Field(min_length=1)
    recovery_tokens: int = Field(gt=0)
    per_update: int = Field(gt=0)
    decay_fraction: float = Field(default=0.1, gt=0, lt=1)
    rounding: TokenRounding = "nearest"

    def target_tokens(self, target: float) -> int:
        """Return `T` in tokens."""
        return round(target * self.nominal_params)

    def branch_start_tokens(self, target: float) -> int:
        """Return where a branch decaying to `T` starts: `(1 - decay_fraction) * T` tokens."""
        return round(self.target_tokens(target) * (1 - self.decay_fraction))

    def branch_targets(self) -> list[float]:
        """Return the targets within the budget, ascending."""
        return sorted(t for t in self.targets if self.target_tokens(t) <= self.stop_tokens)

    def dropped_targets(self) -> list[float]:
        """Return the targets beyond the budget."""
        return sorted(t for t in self.targets if self.target_tokens(t) > self.stop_tokens)

    def step(self, tokens: int) -> int:
        """Return the update a token count rounds to."""
        return tokens_to_step(tokens, self.per_update, self.rounding)

    def saves(self) -> list[SavePoint]:
        """Return every save of the main run, by update, merging saves that share one."""
        wanted: list[tuple[int, str]] = []
        for target in self.branch_targets():
            label = f"{target:g}"
            wanted.append((self.branch_start_tokens(target), f"branch T={label}"))
            wanted.append((self.target_tokens(target), f"T={label}"))
        recoveries = math.ceil(self.stop_tokens / self.recovery_tokens)
        for index in range(1, recoveries):
            wanted.append((index * self.recovery_tokens, f"recovery {index}"))
        wanted.append((self.stop_tokens, "stop"))
        by_step: dict[int, tuple[int, list[str]]] = {}
        for tokens, reason in sorted(wanted):
            step = self.step(tokens)
            _, reasons = by_step.setdefault(step, (tokens, []))
            reasons.append(reason)
        return [
            SavePoint(tokens, step, step * self.per_update, tuple(reasons))
            for step, (tokens, reasons) in sorted(by_step.items())
        ]

    def save_tokens(self) -> list[int]:
        """Return the token targets to pass as `save_tokens`, excluding the stop."""
        return [point.tokens for point in self.saves() if point.step != self.step(self.stop_tokens)]

    def branches(self) -> list[tuple[float, int, int]]:
        """Return `(T, parent checkpoint update, branch stop tokens)` for each kept target."""
        return [
            (target, self.step(self.branch_start_tokens(target)), self.target_tokens(target))
            for target in self.branch_targets()
        ]


def budget_tokens(one_pass_tokens: int, per_update: int, cap_tokens: int | None = None) -> int:
    """Return the largest whole number of updates' tokens within one pass and the cap."""
    limit = one_pass_tokens if cap_tokens is None else min(one_pass_tokens, cap_tokens)
    return (limit // per_update) * per_update


def describe(points: Sequence[SavePoint], nominal_params: int) -> str:
    """Return a table of saves: update, exact tokens, tokens per parameter and reasons."""
    lines = [f"{'step':>9} {'tokens':>16} {'tok/param':>10}  reasons"]
    for point in points:
        lines.append(
            f"{point.step:>9} {point.trained_tokens:>16,} "
            f"{point.trained_tokens / nominal_params:>10.2f}  {', '.join(point.reasons)}"
        )
    return "\n".join(lines)
