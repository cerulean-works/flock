from collections.abc import Mapping
from math import cos, floor, pi
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from torch.optim import Optimizer

type TrainingTime = (
    Annotated[int, Field(strict=True, ge=0)]
    | Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
)


class LRScheduleSegment(BaseModel):
    """Interpolate between two learning-rate ratios over a timeframe.

    Args:
        start (`int` or `float`): Completed updates, or a fraction of the run.
        end (`int` or `float`): Completed updates, or a fraction of the run.
            In particular, `1` means update one and `1.0` means the run endpoint.
        start_ratio (`float`): Initial ratio of each optimizer group's base rate.
        end_ratio (`float`): Final ratio of each optimizer group's base rate.
        interpolation (`str`, *optional*, defaults to `"linear"`):
            `"linear"` or `"cosine"`, for either increasing or decreasing rates.

    Fractions are rounded down to whole updates when the run length is known.
    Ratios must be finite and between zero and one. Fields are immutable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: TrainingTime
    end: TrainingTime
    start_ratio: float = Field(ge=0, le=1, allow_inf_nan=False)
    end_ratio: float = Field(ge=0, le=1, allow_inf_nan=False)
    interpolation: Literal["linear", "cosine"] = "linear"

    @model_validator(mode="after")
    def validate_timeframe(self) -> Self:
        """Reject reversed or empty timeframes whose units are already comparable."""
        if type(self.start) is type(self.end) and self.end <= self.start:
            raise ValueError("Segment end must be greater than start")
        return self


class LRSchedulerConfig(BaseModel):
    """Configure one ordered sequence of learning-rate segments.

    Args:
        schedule (`tuple[LRScheduleSegment, ...]`): Nonempty, ordered timeframes.
            Lists are accepted and stored as an immutable tuple.

    Before the first segment, hold its starting ratio. After the last segment,
    hold its ending ratio. Adjacent segments must share a time boundary;
    use equal starting and ending ratios for an explicit stable phase.
    At a shared boundary, the later segment's starting ratio takes precedence.
    Segments may jump between ratios but must not overlap or leave gaps. Bounds involving
    mixed units are checked once the total update count is known.
    The optimizer configuration supplies the base learning rates.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schedule: tuple[LRScheduleSegment, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        """Reject overlaps and gaps detectable without the run length."""
        for previous, current in zip(self.schedule, self.schedule[1:]):
            if type(previous.end) is type(current.start):
                if current.start < previous.end:
                    raise ValueError("Schedule segments must be sorted and non-overlapping")
                if current.start > previous.end:
                    raise ValueError(
                        f"Schedule gap between {previous.end} and {current.start}: "
                        "adjacent segments must share a time boundary"
                    )
        return self


type _NormalizedSchedule = tuple[tuple[int, int, LRScheduleSegment], ...]


def normalize_training_schedule(config: LRSchedulerConfig, total_steps: int) -> _NormalizedSchedule:
    """Resolve fractional timeframes without changing the configuration.

    Args:
        config (`LRSchedulerConfig`): Ordered timeframes and learning-rate ratios.
        total_steps (`int`): Positive planned optimizer-update count.

    Returns:
        `tuple`: Start update, end update and original segment for each timeframe.

    Raises:
        `ValueError`: If the run length is invalid, timeframes overlap or leave a gap, a segment
            becomes empty after rounding, or an endpoint exceeds the run length.
    """
    if type(total_steps) is not int or total_steps <= 0:
        raise ValueError("total_steps must be a positive integer")
    normalized = []
    last_end = 0
    for segment in config.schedule:
        start = (
            segment.start if isinstance(segment.start, int) else floor(segment.start * total_steps)
        )
        end = segment.end if isinstance(segment.end, int) else floor(segment.end * total_steps)
        if end <= start:
            raise ValueError("Segment end must be greater than start after rounding to steps")
        if start < last_end:
            raise ValueError("Schedule segments must be sorted and non-overlapping")
        if normalized and start > last_end:
            raise ValueError(
                f"Schedule gap between steps {last_end} and {start}: "
                "adjacent segments must share a time boundary"
            )
        if end > total_steps:
            raise ValueError("Schedule endpoint exceeds total_steps")
        normalized.append((start, end, segment))
        last_end = end
    return tuple(normalized)


def lr_multiplier(step: int, config: LRSchedulerConfig, total_steps: int) -> float:
    """Return the learning-rate ratio after `step` completed optimizer updates.

    Args:
        step (`int`): Nonnegative count of completed optimizer updates.
        config (`LRSchedulerConfig`): Ordered timeframes and learning-rate ratios.
        total_steps (`int`): Positive planned optimizer-update count.

    Returns:
        `float`: Multiplier applied to each optimizer group's original rate.

    Raises:
        `ValueError`: If progress or the resolved schedule is invalid.
    """
    if type(step) is not int or step < 0:
        raise ValueError("step must be a nonnegative integer")
    schedule = normalize_training_schedule(config, total_steps)
    ratio = schedule[0][2].start_ratio
    for start, end, segment in schedule:
        if step < start:
            return ratio
        if step < end:
            progress = (step - start) / (end - start)
            if segment.interpolation == "cosine":
                progress = 0.5 * (1.0 - cos(pi * progress))
            return segment.start_ratio + (segment.end_ratio - segment.start_ratio) * progress
        ratio = segment.end_ratio
    return ratio


class LRSchedulerState(BaseModel):
    """Describe the scheduler state persisted separately from the optimizer.

    Attributes:
        config (`dict[str, Any]`): Learning-rate timeframes and interpolation fields.
        total_steps (`int`): Nonnegative planned update count.
        base_lrs (`list[float]`): Original rate for each optimizer parameter group.
        completed_steps (`int`): Nonnegative completed update count.
    """

    model_config = ConfigDict(strict=True, extra="forbid", revalidate_instances="always")

    config: dict[str, Any]
    total_steps: int = Field(ge=0)
    base_lrs: list[float]
    completed_steps: int = Field(ge=0)

    @field_validator("completed_steps", mode="before")
    @classmethod
    def validate_progress(cls, value: Any) -> int:
        """Require an integer update count without accepting booleans or coercion."""
        if type(value) is not int or value < 0:
            raise ValueError("completed_steps must be a nonnegative integer")
        return value


class StepLRScheduler:
    """Apply a shared timeframe schedule to each optimizer group's base rate.

    Construction immediately sets the rate for zero completed updates. Call
    `step()` after each optimizer update to prepare the next update's rate.

    Args:
        optimizer (`torch.optim.Optimizer`): Optimizer whose current rates become base rates.
        config (`LRSchedulerConfig`): Ordered timeframes and learning-rate ratios.
        total_steps (`int`): Positive planned number of optimizer updates.
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
        """Advance after one optimizer update; prepare LR for the next update."""
        self.completed_steps += 1
        self._apply_lr()

    def get_last_lr(self) -> list[float]:
        """Return the current learning rate of each optimizer parameter group."""
        return [float(group["lr"]) for group in self.optimizer.param_groups]

    def state_dict(self) -> dict[str, Any]:
        """Return independent, plain schedule values and progress, without the optimizer."""
        return {
            "config": self.config.model_dump(mode="json"),
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
                steps differ, or completed steps are invalid.
        """
        state = LRSchedulerState.model_validate(dict(state_dict))
        config = LRSchedulerConfig.model_validate(state.config)
        base_lrs = state.base_lrs
        if len(base_lrs) != len(self.optimizer.param_groups):
            raise ValueError("Optimizer parameter groups do not match the saved scheduler")
        # JSON preserves integer versus fractional timestamps; numeric equality alone does not.
        if config.model_dump_json() != self.config.model_dump_json():
            raise ValueError("Scheduler config does not match the saved scheduler")
        if state.total_steps != self.total_steps:
            raise ValueError("Scheduler total_steps does not match the saved scheduler")
        if base_lrs != self.base_lrs:
            raise ValueError("Scheduler base_lrs do not match the saved scheduler")

    def load_state_dict(self, state_dict: Mapping[str, Any]) -> None:
        """Restore progress and apply learning rates for the next update.

        Restore the optimizer's state before calling this method.

        Args:
            state_dict (`Mapping[str, Any]`): Mapping produced by `state_dict()`.

        Raises:
            `ValueError`: If saved settings fail `validate_state_dict`.
        """
        self.validate_state_dict(state_dict)
        self.completed_steps = state_dict["completed_steps"]
        self._apply_lr()
