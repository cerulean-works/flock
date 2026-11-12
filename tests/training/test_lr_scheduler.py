import copy
import io
from typing import cast

import pytest
import torch
from transformers.optimization import get_cosine_with_min_lr_schedule_with_warmup

from flock.training.core.lr_scheduler import (
    CosineConfig,
    LRSchedulerConfig,
    StepLRScheduler,
    WSDConfig,
    lr_multiplier,
)


def make_optimizer():
    return torch.optim.SGD(
        [
            {"params": [torch.nn.Parameter(torch.ones(1))], "lr": 0.2},
            {"params": [torch.nn.Parameter(torch.ones(1))], "lr": 0.05},
        ]
    )


@pytest.mark.parametrize("warmup_steps", [0, 2])
@pytest.mark.parametrize("minimum", [0.0, 0.1])
def test_cosine_matches_huggingface_through_last_update(warmup_steps, minimum):
    optimizer, reference_optimizer = make_optimizer(), make_optimizer()
    scheduler = StepLRScheduler(
        optimizer, CosineConfig(warmup_steps=warmup_steps, min_lr_ratio=minimum), 8
    )
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
def test_wsd_boundaries_against_hand_calculated_values(step, expected):
    config = WSDConfig(warmup_steps=2, decay_steps=2, min_lr_ratio=0.2)
    assert lr_multiplier(step, config, total_steps=8) == pytest.approx(expected)


@pytest.mark.parametrize(
    "config",
    [
        CosineConfig(warmup_steps=0, min_lr_ratio=0.1),
        WSDConfig(warmup_steps=0, decay_steps=2, min_lr_ratio=0.1),
    ],
)
def test_completed_schedules_stay_at_the_floor(config):
    for step in (8, 9, 16):
        assert lr_multiplier(step, config, total_steps=8) == pytest.approx(0.1)


def test_scheduler_prepares_the_next_update_without_changing_weights():
    optimizer = make_optimizer()
    weight = optimizer.param_groups[0]["params"][0]
    scheduler = StepLRScheduler(
        optimizer, CosineConfig(warmup_steps=2, min_lr_ratio=0.0), total_steps=6
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


def test_scheduler_rejects_parameter_groups_added_after_creation():
    optimizer = make_optimizer()
    scheduler = StepLRScheduler(
        optimizer, CosineConfig(warmup_steps=0, min_lr_ratio=0.0), total_steps=4
    )
    optimizer.add_param_group({"params": [torch.nn.Parameter(torch.ones(1))], "lr": 0.3})
    with pytest.raises(ValueError, match="parameter groups changed"):
        scheduler.step()


def test_negative_step_is_rejected():
    with pytest.raises(ValueError, match="nonnegative"):
        lr_multiplier(-1, CosineConfig(warmup_steps=0, min_lr_ratio=0.0), total_steps=4)


def test_unknown_schedule_is_rejected():
    with pytest.raises(TypeError, match="Unsupported scheduler config"):
        lr_multiplier(0, cast(LRSchedulerConfig, object()), total_steps=4)


@pytest.mark.parametrize(
    "config",
    [
        CosineConfig(warmup_steps=2, min_lr_ratio=0.1),
        WSDConfig(warmup_steps=2, decay_steps=2, min_lr_ratio=0.2),
    ],
)
@pytest.mark.parametrize("completed_steps", [1, 4, 7])
def test_saved_scheduler_resumes_like_uninterrupted_training(config, completed_steps):
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


def test_scheduler_state_does_not_share_mutable_values():
    scheduler = StepLRScheduler(
        make_optimizer(), CosineConfig(warmup_steps=2, min_lr_ratio=0.1), total_steps=8
    )
    state = scheduler.state_dict()
    state["base_lrs"][0] = 10.0
    state["config"]["warmup_steps"] = 7
    assert scheduler.base_lrs == [0.2, 0.05]
    assert scheduler.config.warmup_steps == 2

    state = scheduler.state_dict()
    scheduler.load_state_dict(state)
    state["base_lrs"][0] = 10.0
    assert scheduler.base_lrs == [0.2, 0.05]


def test_loading_scheduler_rejects_different_parameter_group_counts():
    scheduler = StepLRScheduler(
        make_optimizer(), CosineConfig(warmup_steps=0, min_lr_ratio=0.1), total_steps=8
    )
    resumed_optimizer = torch.optim.SGD([torch.nn.Parameter(torch.ones(1))], lr=0.3)
    resumed = StepLRScheduler(resumed_optimizer, scheduler.config, total_steps=8)
    with pytest.raises(ValueError, match="parameter groups"):
        resumed.load_state_dict(scheduler.state_dict())
    assert resumed.get_last_lr() == [0.3]


@pytest.mark.parametrize(
    "field, value, error",
    [
        ("config", {"type": "cosine", "warmup_steps": 1, "min_lr_ratio": 0.1}, "config"),
        ("config", {"type": "wsd", "warmup_steps": 2, "decay_steps": 2}, "config"),
        ("total_steps", 10, "total_steps"),
        ("base_lrs", [0.3, 0.05], "base_lrs"),
        ("completed_steps", -1, "nonnegative"),
    ],
)
def test_incompatible_scheduler_state_is_rejected_without_mutation(field, value, error):
    scheduler = StepLRScheduler(
        make_optimizer(), CosineConfig(warmup_steps=2, min_lr_ratio=0.1), total_steps=8
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


def test_scheduler_preflight_does_not_apply_saved_progress():
    scheduler = StepLRScheduler(
        make_optimizer(), CosineConfig(warmup_steps=2, min_lr_ratio=0.1), total_steps=8
    )
    expected = scheduler.state_dict()
    saved = {**expected, "completed_steps": 4}
    scheduler.validate_state_dict(saved)
    assert scheduler.state_dict() == expected
    assert scheduler.get_last_lr() == [0.0, 0.0]
