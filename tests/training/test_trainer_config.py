from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from flock.eval.tasks import TASKS
from flock.training.core.lr_scheduler import CosineConfig
from flock.training.core.optimization import AdamWConfig
from flock.training.core.trainer import TrainerConfig

if TYPE_CHECKING:
    from typing import Any


@pytest.fixture
def config_values() -> dict[str, Any]:
    return {
        "run_name": "test-run",
        "input_column": "text",
        "batch_size": 2,
        "optimizer_config": AdamWConfig(lr=0.01, betas=(0.9, 0.95)),
        "lr_scheduler_config": CosineConfig(warmup_steps=0, min_lr_ratio=0.1),
        "wandb_project": "test",
    }


@pytest.mark.parametrize(
    "field",
    [
        "batch_size",
        "max_steps",
        "logging_steps",
        "validation_steps",
        "validation_limit",
        "validation_batch_tokens",
        "save_steps",
        "save_total_limit",
        "dataloader_prefetch_factor",
    ],
)
@pytest.mark.parametrize("value", [0, -1])
def test_positive_config_fields(config_values: dict[str, Any], field: str, value: int) -> None:
    with pytest.raises(ValidationError) as exc:
        TrainerConfig.model_validate(config_values | {field: value})
    assert exc.value.errors()[0]["loc"] == (field,)


@pytest.mark.parametrize(
    "field, value",
    [
        ("dataloader_num_workers", -1),
        ("max_norm", -1),
        ("max_norm", float("inf")),
        ("max_norm", float("nan")),
    ],
)
def test_nonnegative_finite_config_fields(
    config_values: dict[str, Any], field: str, value: float
) -> None:
    with pytest.raises(ValidationError) as exc:
        TrainerConfig.model_validate(config_values | {field: value})
    assert exc.value.errors()[0]["loc"] == (field,)


def test_config_accepts_boundaries_and_disabled_intervals(config_values: dict[str, Any]) -> None:
    config = TrainerConfig.model_validate(
        {
            **config_values,
            "batch_size": 1,
            "dataloader_num_workers": 0,
            "dataloader_prefetch_factor": None,
            "max_norm": 0,
            "logging_steps": 1,
        }
    )
    assert config.validation_steps is None
    assert config.validation_limit is None
    assert config.validation_batch_tokens == 32768
    assert config.save_steps is None
    assert config.save_total_limit is None


def test_zero_workers_reject_default_prefetch(config_values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="must be None"):
        TrainerConfig(**config_values, dataloader_num_workers=0)


def test_config_accepts_registered_validation_tasks(config_values: dict[str, Any]) -> None:
    tasks = [next(iter(TASKS))]
    config = TrainerConfig(**config_values, validation_tasks=tasks, validation_steps=1)
    assert config.validation_tasks == tasks


def test_config_rejects_unknown_validation_tasks(config_values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="Invalid validation task"):
        TrainerConfig(**config_values, validation_tasks=["nonexistent-validation-task"])
