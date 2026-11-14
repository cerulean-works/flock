from __future__ import annotations

import copy
import io
from typing import Any

import pytest
import torch
from transformers.optimization import get_cosine_with_min_lr_schedule_with_warmup

from flock.training.core.lr_scheduler import (
    LRSchedulerConfig,
    LRScheduleSegment,
    StepLRScheduler,
    lr_multiplier,
)


def make_optimizer() -> torch.optim.SGD:
    return torch.optim.SGD(
        [
            {"params": [torch.nn.Parameter(torch.ones(1))], "lr": 0.2},
            {"params": [torch.nn.Parameter(torch.ones(1))], "lr": 0.05},
        ]
    )


def cosine_schedule(warmup_steps: int, minimum: float) -> LRSchedulerConfig:
    segments = []
    if warmup_steps:
        segments.append(LRScheduleSegment(start=0, end=warmup_steps, start_ratio=0, end_ratio=1))
    segments.append(
        LRScheduleSegment(
            start=warmup_steps, end=1.0, start_ratio=1, end_ratio=minimum, interpolation="cosine"
        )
    )
    return LRSchedulerConfig(schedule=segments)


@pytest.mark.parametrize("warmup_steps", [0, 2])
@pytest.mark.parametrize("minimum", [0.0, 0.1])
def test_cosine_matches_huggingface_through_last_update(warmup_steps: int, minimum: float) -> None:
    optimizer, reference_optimizer = make_optimizer(), make_optimizer()
    scheduler = StepLRScheduler(optimizer, cosine_schedule(warmup_steps, minimum), 8)
    reference = get_cosine_with_min_lr_schedule_with_warmup(
        reference_optimizer,
        num_warmup_steps=warmup_steps,
        num_training_steps=8,
        min_lr_rate=minimum,
    )
    for step in range(9):
        assert scheduler.completed_steps == step
        assert scheduler.get_last_lr() == pytest.approx(reference.get_last_lr())
        assert scheduler.get_last_lr() == [group["lr"] for group in optimizer.param_groups]
        if step < 8:
            optimizer.step()
            reference_optimizer.step()
            scheduler.step()
            reference.step()


@pytest.mark.parametrize(
    "step, expected",
    [(0, 0.0), (1, 0.5), (2, 1.0), (5, 1.0), (6, 1.0), (7, 0.6), (8, 0.2), (12, 0.2)],
)
def test_wsd_boundaries_against_hand_calculated_values(step: int, expected: float) -> None:
    config = LRSchedulerConfig(
        schedule=[
            LRScheduleSegment(start=0, end=2, start_ratio=0.0, end_ratio=1.0),
            LRScheduleSegment(start=2, end=6, start_ratio=1, end_ratio=1),
            LRScheduleSegment(
                start=6, end=1.0, start_ratio=1.0, end_ratio=0.2, interpolation="cosine"
            ),
        ]
    )
    assert lr_multiplier(step, config, total_steps=8) == pytest.approx(expected)


@pytest.mark.parametrize(
    "config",
    [
        LRSchedulerConfig(
            schedule=[
                LRScheduleSegment(
                    start=0, end=1.0, start_ratio=1.0, end_ratio=0.1, interpolation="cosine"
                )
            ]
        ),
        LRSchedulerConfig(
            schedule=[
                LRScheduleSegment(
                    start=6, end=1.0, start_ratio=1.0, end_ratio=0.1, interpolation="cosine"
                )
            ]
        ),
    ],
)
def test_completed_schedules_stay_at_the_floor(config: LRSchedulerConfig) -> None:
    for step in (8, 9, 16):
        assert lr_multiplier(step, config, total_steps=8) == pytest.approx(0.1)


def test_scheduler_prepares_the_next_update_without_changing_weights() -> None:
    optimizer = make_optimizer()
    weight = optimizer.param_groups[0]["params"][0]
    scheduler = StepLRScheduler(
        optimizer,
        LRSchedulerConfig(
            schedule=[
                LRScheduleSegment(start=0, end=2, start_ratio=0.0, end_ratio=1.0),
                LRScheduleSegment(
                    start=2, end=1.0, start_ratio=1.0, end_ratio=0.0, interpolation="cosine"
                ),
            ]
        ),
        total_steps=6,
    )
    assert scheduler.get_last_lr() == [0.0, 0.0]
    weight.grad = torch.ones_like(weight)
    optimizer.step()
    assert weight.item() == 1.0  # Update zero uses the initial warmup LR.
    scheduler.step()
    assert scheduler.get_last_lr() == pytest.approx([0.1, 0.025])
    assert weight.item() == 1.0  # Advancing the schedule is not an optimizer update.
    optimizer.step()
    assert weight.item() == pytest.approx(0.9)


def test_scheduler_rejects_parameter_groups_added_after_creation() -> None:
    optimizer = make_optimizer()
    scheduler = StepLRScheduler(
        optimizer,
        LRSchedulerConfig(
            schedule=[
                LRScheduleSegment(
                    start=0, end=1.0, start_ratio=1.0, end_ratio=0.0, interpolation="cosine"
                )
            ]
        ),
        total_steps=4,
    )
    optimizer.add_param_group({"params": [torch.nn.Parameter(torch.ones(1))], "lr": 0.3})
    with pytest.raises(ValueError, match="parameter groups changed"):
        scheduler.step()


def test_negative_step_is_rejected() -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        lr_multiplier(
            -1,
            LRSchedulerConfig(
                schedule=[
                    LRScheduleSegment(
                        start=0, end=1.0, start_ratio=1.0, end_ratio=0.0, interpolation="cosine"
                    )
                ]
            ),
            total_steps=4,
        )


@pytest.mark.parametrize(
    "config",
    [
        LRSchedulerConfig(
            schedule=[
                LRScheduleSegment(start=0, end=2, start_ratio=0.0, end_ratio=1.0),
                LRScheduleSegment(
                    start=2, end=1.0, start_ratio=1.0, end_ratio=0.1, interpolation="cosine"
                ),
            ]
        ),
        LRSchedulerConfig(
            schedule=[
                LRScheduleSegment(start=0, end=2, start_ratio=0.0, end_ratio=1.0),
                LRScheduleSegment(start=2, end=6, start_ratio=1, end_ratio=1),
                LRScheduleSegment(
                    start=6, end=1.0, start_ratio=1.0, end_ratio=0.2, interpolation="cosine"
                ),
            ]
        ),
    ],
)
@pytest.mark.parametrize("completed_steps", [1, 4, 7])
def test_saved_scheduler_resumes_like_uninterrupted_training(
    config: LRSchedulerConfig, completed_steps: int
) -> None:
    optimizer = make_optimizer()
    scheduler = StepLRScheduler(optimizer, config, total_steps=8)
    for _ in range(completed_steps):
        optimizer.step()
        scheduler.step()

    checkpoint = io.BytesIO()
    torch.save(scheduler.state_dict(), checkpoint)
    checkpoint.seek(0)
    state = torch.load(checkpoint, weights_only=True)

    resumed_optimizer = make_optimizer()
    resumed = StepLRScheduler(resumed_optimizer, config, total_steps=8)
    resumed_optimizer.load_state_dict(optimizer.state_dict())
    resumed.load_state_dict(state)

    assert resumed.config == config
    assert resumed.total_steps == 8
    assert resumed.completed_steps == completed_steps
    assert resumed.get_last_lr() == pytest.approx(scheduler.get_last_lr())
    for _ in range(completed_steps, 10):
        optimizer.step()
        resumed_optimizer.step()
        scheduler.step()
        resumed.step()
        assert resumed.get_last_lr() == pytest.approx(scheduler.get_last_lr())


def test_scheduler_state_does_not_share_mutable_values() -> None:
    scheduler = StepLRScheduler(
        make_optimizer(),
        LRSchedulerConfig(
            schedule=[
                LRScheduleSegment(start=0, end=2, start_ratio=0.0, end_ratio=1.0),
                LRScheduleSegment(
                    start=2, end=1.0, start_ratio=1.0, end_ratio=0.1, interpolation="cosine"
                ),
            ]
        ),
        total_steps=8,
    )
    state = scheduler.state_dict()
    state["base_lrs"][0] = 10.0
    state["config"]["schedule"][0]["end"] = 7
    assert scheduler.base_lrs == [0.2, 0.05]
    assert scheduler.config.schedule[0].end == 2

    state = scheduler.state_dict()
    scheduler.load_state_dict(state)
    state["base_lrs"][0] = 10.0
    assert scheduler.base_lrs == [0.2, 0.05]


def test_loading_scheduler_rejects_different_parameter_group_counts() -> None:
    scheduler = StepLRScheduler(
        make_optimizer(),
        LRSchedulerConfig(
            schedule=[
                LRScheduleSegment(
                    start=0, end=1.0, start_ratio=1.0, end_ratio=0.1, interpolation="cosine"
                )
            ]
        ),
        total_steps=8,
    )
    resumed_optimizer = torch.optim.SGD([torch.nn.Parameter(torch.ones(1))], lr=0.3)
    resumed = StepLRScheduler(resumed_optimizer, scheduler.config, total_steps=8)
    with pytest.raises(ValueError, match="parameter groups"):
        resumed.load_state_dict(scheduler.state_dict())
    assert resumed.get_last_lr() == [0.3]


@pytest.mark.parametrize(
    "field, value, error",
    [
        ("config", cosine_schedule(1, 0.1).model_dump(mode="json"), "config"),
        ("config", cosine_schedule(2, 0.2).model_dump(mode="json"), "config"),
        ("config", {"schedule": []}, "schedule"),
        ("total_steps", 10, "total_steps"),
        ("base_lrs", [0.3, 0.05], "base_lrs"),
        ("base_lrs", [0.2, 0.3], "base_lrs"),
        ("base_lrs", [0.2], "parameter groups"),
        ("completed_steps", -1, "nonnegative"),
    ],
)
def test_incompatible_scheduler_state_is_rejected_without_mutation(
    field: str,
    value: Any,
    error: str,
) -> None:
    scheduler = StepLRScheduler(
        make_optimizer(),
        LRSchedulerConfig(
            schedule=[
                LRScheduleSegment(start=0, end=2, start_ratio=0.0, end_ratio=1.0),
                LRScheduleSegment(
                    start=2, end=1.0, start_ratio=1.0, end_ratio=0.1, interpolation="cosine"
                ),
            ]
        ),
        total_steps=8,
    )
    expected = scheduler.state_dict()
    expected_optimizer = copy.deepcopy(scheduler.optimizer.state_dict())
    invalid = copy.deepcopy(expected)
    invalid[field] = value
    for check in (scheduler.validate_state_dict, scheduler.load_state_dict):
        with pytest.raises(ValueError, match=error):
            check(invalid)
        assert scheduler.state_dict() == expected
        assert scheduler.optimizer.state_dict() == expected_optimizer


@pytest.mark.parametrize(
    "config",
    [
        LRSchedulerConfig(
            schedule=[
                LRScheduleSegment(start=0, end=2, start_ratio=0.0, end_ratio=1.0),
                LRScheduleSegment(
                    start=2, end=1.0, start_ratio=1.0, end_ratio=0.1, interpolation="cosine"
                ),
            ]
        ),
        LRSchedulerConfig(
            schedule=[
                LRScheduleSegment(start=0, end=2, start_ratio=0.0, end_ratio=1.0),
                LRScheduleSegment(start=2, end=6, start_ratio=1, end_ratio=1),
                LRScheduleSegment(
                    start=6, end=1.0, start_ratio=1.0, end_ratio=0.1, interpolation="cosine"
                ),
            ]
        ),
    ],
    ids=["cosine", "wsd"],
)
def test_scheduler_preflight_does_not_apply_saved_progress(config: LRSchedulerConfig) -> None:
    scheduler = StepLRScheduler(make_optimizer(), config, total_steps=8)
    expected = scheduler.state_dict()
    saved = {**expected, "completed_steps": 4}
    scheduler.validate_state_dict(saved)
    assert scheduler.state_dict() == expected
    assert scheduler.get_last_lr() == [0.0, 0.0]


@pytest.mark.parametrize("interpolation, quarter", [("linear", 0.25), ("cosine", 0.1464466094)])
@pytest.mark.parametrize("start_ratio, end_ratio", [(0.0, 1.0), (1.0, 0.2)])
def test_interpolation_in_both_directions(
    interpolation: str, quarter: float, start_ratio: float, end_ratio: float
) -> None:
    config = LRSchedulerConfig.model_validate(
        {
            "schedule": [
                {
                    "start": 0,
                    "end": 8,
                    "start_ratio": start_ratio,
                    "end_ratio": end_ratio,
                    "interpolation": interpolation,
                }
            ]
        }
    )
    assert lr_multiplier(0, config, 8) == start_ratio
    assert lr_multiplier(2, config, 8) == pytest.approx(
        start_ratio + (end_ratio - start_ratio) * quarter
    )
    assert lr_multiplier(4, config, 8) == pytest.approx((start_ratio + end_ratio) / 2)
    assert lr_multiplier(8, config, 8) == end_ratio


def test_explicit_stable_phase_endpoint_holds_and_shared_boundary_jumps() -> None:
    config = LRSchedulerConfig(
        schedule=[
            LRScheduleSegment(start=2, end=4, start_ratio=0.2, end_ratio=0.6),
            LRScheduleSegment(start=4, end=6, start_ratio=0.6, end_ratio=0.6),
            LRScheduleSegment(start=6, end=8, start_ratio=0.4, end_ratio=0.8),
            LRScheduleSegment(start=8, end=10, start_ratio=1, end_ratio=0),
        ]
    )
    expected = [0.2, 0.2, 0.2, 0.4, 0.6, 0.6, 0.4, 0.6, 1, 0.5, 0, 0]
    assert [lr_multiplier(step, config, 12) for step in range(12)] == pytest.approx(expected)


def test_fractional_and_mixed_timeframes_normalize_without_mutation() -> None:
    from flock.training.core.lr_scheduler import normalize_training_schedule

    config = LRSchedulerConfig(
        schedule=[
            LRScheduleSegment(start=0, end=0.29, start_ratio=0, end_ratio=1),
            LRScheduleSegment(start=0.29, end=0.6, start_ratio=1, end_ratio=1),
            LRScheduleSegment(start=0.6, end=9, start_ratio=1, end_ratio=0.1),
        ]
    )
    saved = config.model_dump_json()
    assert [(start, end) for start, end, _ in normalize_training_schedule(config, 10)] == [
        (0, 2),
        (2, 6),
        (6, 9),
    ]
    assert lr_multiplier(1, config, 10) == 0.5
    assert lr_multiplier(5, config, 10) == 1
    assert config.model_dump_json() == saved
    assert LRSchedulerConfig.model_validate_json(saved).model_dump_json() == saved


@pytest.mark.parametrize("end, start", [(0.3, 0.4), (0.3, 0.31), (2, 4)])
def test_config_rejects_gaps_in_matching_units(end: float, start: float) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="Schedule gap"):
        LRSchedulerConfig(
            schedule=[
                LRScheduleSegment(start=0, end=end, start_ratio=0, end_ratio=1),
                LRScheduleSegment(start=start, end=1.0, start_ratio=1, end_ratio=0),
            ]
        )


@pytest.mark.parametrize("end, start", [(0.3, 4), (2, 0.4), (1, 0.8)])
def test_mixed_unit_gaps_are_rejected_when_run_length_is_known(
    end: float,
    start: float,
) -> None:
    config = LRSchedulerConfig(
        schedule=[
            LRScheduleSegment(start=0, end=end, start_ratio=0, end_ratio=1),
            LRScheduleSegment(start=start, end=1.0, start_ratio=1, end_ratio=0),
        ]
    )
    optimizer = make_optimizer()
    with pytest.raises(ValueError, match="Schedule gap"):
        StepLRScheduler(optimizer, config, total_steps=10)
    assert [group["lr"] for group in optimizer.param_groups] == [0.2, 0.05]


def test_mixed_unit_segments_may_share_a_resolved_boundary() -> None:
    config = LRSchedulerConfig(
        schedule=[
            LRScheduleSegment(start=0, end=0.3, start_ratio=0, end_ratio=1),
            LRScheduleSegment(start=3, end=1.0, start_ratio=1, end_ratio=0),
        ]
    )
    assert lr_multiplier(3, config, total_steps=10) == 1


@pytest.mark.parametrize(
    "field, value",
    [
        ("start", -1),
        ("start", True),
        ("start", "2"),
        ("start", 1.5),
        ("end", float("nan")),
        ("end", float("inf")),
        ("start_ratio", -0.1),
        ("end_ratio", 1.1),
        ("start_ratio", float("nan")),
        ("end_ratio", float("inf")),
        ("interpolation", "cubic"),
        ("typo", 1),
    ],
)
def test_segment_rejects_invalid_fields(field: str, value: Any) -> None:
    from pydantic import ValidationError

    fields = {"start": 0, "end": 8, "start_ratio": 0, "end_ratio": 1, field: value}
    with pytest.raises(ValidationError):
        LRScheduleSegment.model_validate(fields)


@pytest.mark.parametrize(
    "timeframes",
    [
        [(0, 0)],
        [(3, 2)],
        [(0.5, 0.2)],
        [(0, 4), (3, 8)],
        [(4, 8), (0, 2)],
        [(0, 0.01)],
        [(0, 9)],
        [(0.8, 2)],
        [(0, 0.6), (3, 1.0)],
    ],
)
def test_invalid_timeframes_are_rejected_before_changing_optimizer(
    timeframes: list[tuple[int | float, int | float]],
) -> None:
    optimizer = make_optimizer()
    with pytest.raises(ValueError):
        config = LRSchedulerConfig(
            schedule=[
                LRScheduleSegment(start=start, end=end, start_ratio=0, end_ratio=1)
                for start, end in timeframes
            ]
        )
        StepLRScheduler(optimizer, config, total_steps=8)
    assert [group["lr"] for group in optimizer.param_groups] == [0.2, 0.05]


@pytest.mark.parametrize("total_steps", [0, -1, 2.5, True])
def test_invalid_run_lengths_are_rejected(total_steps: Any) -> None:
    with pytest.raises(ValueError, match="total_steps"):
        lr_multiplier(0, cosine_schedule(0, 0.1), total_steps)


def test_config_is_nonempty_and_immutable() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        LRSchedulerConfig(schedule=[])
    config = cosine_schedule(0, 0.1)
    assert isinstance(config.schedule, tuple)
    with pytest.raises(ValidationError, match="frozen"):
        config.schedule[0].end_ratio = 0.2  # ty: ignore[invalid-assignment]


def test_integer_and_fractional_endpoints_are_distinct_on_restore() -> None:
    scheduler = StepLRScheduler(make_optimizer(), cosine_schedule(0, 0.1), total_steps=8)
    saved = scheduler.state_dict()
    saved["config"]["schedule"][0]["end"] = 1
    with pytest.raises(ValueError, match="config"):
        scheduler.load_state_dict(saved)
    assert scheduler.completed_steps == 0


def test_one_update_segment_and_constant_segment() -> None:
    config = LRSchedulerConfig(
        schedule=[
            LRScheduleSegment(start=0, end=1, start_ratio=0, end_ratio=0.5),
            LRScheduleSegment(start=1, end=1.0, start_ratio=0.5, end_ratio=0.5),
        ]
    )
    assert [lr_multiplier(step, config, 2) for step in range(4)] == [0, 0.5, 0.5, 0.5]


@pytest.mark.parametrize(
    "change",
    ["missing", "extra", "boolean-progress", "string-progress", "invalid-base-lrs"],
)
def test_scheduler_owns_strict_state_validation(change: str) -> None:
    scheduler = StepLRScheduler(make_optimizer(), cosine_schedule(2, 0.1), total_steps=8)
    expected = scheduler.state_dict()
    expected_optimizer = copy.deepcopy(scheduler.optimizer.state_dict())
    saved = copy.deepcopy(expected)
    if change == "missing":
        del saved["config"]
    elif change == "extra":
        saved["unknown"] = 1
    elif change == "boolean-progress":
        saved["completed_steps"] = True
    elif change == "string-progress":
        saved["completed_steps"] = "2"
    else:
        saved["base_lrs"] = ["0.2", 0.05]
    for check in (scheduler.validate_state_dict, scheduler.load_state_dict):
        with pytest.raises(ValueError):
            check(saved)
        assert scheduler.state_dict() == expected
        assert scheduler.optimizer.state_dict() == expected_optimizer
