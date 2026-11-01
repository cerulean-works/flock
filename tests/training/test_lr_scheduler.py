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
