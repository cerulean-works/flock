"""Schemas for the training state stored alongside model checkpoints."""

from typing import Any, Self

import torch
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator


class CheckpointSchema(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, strict=True, extra="forbid")


class CheckpointDataConfig(CheckpointSchema):
    dataset_size: int = Field(ge=0)
    batch_size: int = Field(gt=0)
    seed: int
    input_column: str


class CheckpointTrainerState(CheckpointSchema):
    loss_sum: torch.Tensor
    loss_max: torch.Tensor
    grad_norm_sum: torch.Tensor
    grad_norm_max: torch.Tensor
    step: torch.Tensor
    total_tokens: torch.Tensor
    window_tokens: torch.Tensor
    window_steps: torch.Tensor

    @field_validator("step", "total_tokens", "window_tokens", "window_steps")
    @classmethod
    def validate_counter(cls, value: torch.Tensor) -> torch.Tensor:
        if value.ndim != 0 or value.dtype != torch.int64 or value.item() < 0:
            raise ValueError("Training counters must be nonnegative scalar int64 tensors")
        return value


class CheckpointSchedulerState(CheckpointSchema):
    config: dict[str, Any]
    total_steps: int = Field(ge=0)
    base_lrs: list[float]
    completed_steps: int = Field(ge=0)


class TrainerCheckpoint(CheckpointSchema):
    trainer_state_dict: CheckpointTrainerState
    optimizer_state_dict: dict[str, Any]
    lr_scheduler_state_dict: CheckpointSchedulerState
    cpu_rng_state: torch.Tensor
    cuda_rng_state: list[torch.Tensor] | None
    python_rng_state: tuple[int, tuple[int, ...], float | None]
    data_config: CheckpointDataConfig

    @model_validator(mode="after")
    def validate_run(self, info: ValidationInfo) -> Self:
        step = int(self.trainer_state_dict.step.item())
        schedule = self.lr_scheduler_state_dict
        if step > schedule.total_steps or schedule.completed_steps != step:
            raise ValueError("Checkpoint trainer and scheduler progress do not match")

        if info.context is not None:
            if self.data_config.model_dump() != info.context["data_config"]:
                raise ValueError(
                    "Checkpoint dataset size, batch size, seed or input column changed"
                )
            if schedule.total_steps != info.context["total_steps"]:
                raise ValueError("Checkpoint trainer and scheduler progress do not match this run")
        return self
