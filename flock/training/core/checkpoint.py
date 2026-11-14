"""Schemas for the training state stored alongside model checkpoints."""

from typing import Any, Self

import torch
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator


class CheckpointSchema(BaseModel):
    """Validate checkpoint fields strictly, allowing tensors and rejecting extras."""

    model_config = ConfigDict(arbitrary_types_allowed=True, strict=True, extra="forbid")


class CheckpointDataConfig(CheckpointSchema):
    """Record the data settings compared before restoring training state.

    Attributes:
        dataset_size (`int`): Nonnegative row count; dataset contents are not hashed.
        batch_size (`int`): Positive rows per optimizer update.
        seed (`int`): Shuffle and training seed.
        input_column (`str`): Text field read by the collator.
    """

    dataset_size: int = Field(ge=0)
    batch_size: int = Field(gt=0)
    seed: int
    input_column: str


class CheckpointTrainerState(CheckpointSchema):
    """Validate metric buffers and nonnegative scalar int64 training counters.

    Attributes:
        loss_sum (`torch.Tensor`): Token-weighted loss sum in the unfinished window.
        loss_max (`torch.Tensor`): Largest batch mean loss in the window.
        grad_norm_sum (`torch.Tensor`): Sum of pre-clipping norms in the window.
        grad_norm_max (`torch.Tensor`): Largest pre-clipping norm in the window.
        step (`torch.Tensor`): Completed optimizer updates.
        total_tokens (`torch.Tensor`): Valid targets processed over the full run.
        window_tokens (`torch.Tensor`): Valid targets processed in the window.
        window_steps (`torch.Tensor`): Updates accumulated in the window.
    """

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
        """Require a nonnegative scalar int64 tensor for each progress counter."""
        if value.ndim != 0 or value.dtype != torch.int64 or value.item() < 0:
            raise ValueError("Training counters must be nonnegative scalar int64 tensors")
        return value


class CheckpointSchedulerState(CheckpointSchema):
    """Describe the scheduler state persisted separately from the optimizer.

    Attributes:
        config (`dict[str, Any]`): Schedule discriminator and configuration fields.
        total_steps (`int`): Nonnegative planned update count.
        base_lrs (`list[float]`): Original rate for each optimizer parameter group.
        completed_steps (`int`): Nonnegative completed update count.
    """

    config: dict[str, Any]
    total_steps: int = Field(ge=0)
    base_lrs: list[float]
    completed_steps: int = Field(ge=0)


class TrainerCheckpoint(CheckpointSchema):
    """Validate resumable training state stored alongside model weights.

    Attributes:
        trainer_state_dict (`CheckpointTrainerState`): Metric buffers and counters.
        optimizer_state_dict (`dict[str, Any]`): Optimizer groups and parameter state.
        lr_scheduler_state_dict (`CheckpointSchedulerState`): Schedule and progress.
        cpu_rng_state (`torch.Tensor`): PyTorch CPU RNG state.
        cuda_rng_state (`list[torch.Tensor]` or `None`): Per-device CUDA RNG states.
        python_rng_state (`tuple`): Python `random` state.
        data_config (`CheckpointDataConfig`): Settings defining the sampler contract.

    Model validation checks that trainer and scheduler progress agree. With a
    validation context containing `data_config` and `total_steps`, it also checks
    compatibility with the receiving run before state restoration.
    """

    trainer_state_dict: CheckpointTrainerState
    optimizer_state_dict: dict[str, Any]
    lr_scheduler_state_dict: CheckpointSchedulerState
    cpu_rng_state: torch.Tensor
    cuda_rng_state: list[torch.Tensor] | None
    python_rng_state: tuple[int, tuple[int, ...], float | None]
    data_config: CheckpointDataConfig

    @model_validator(mode="after")
    def validate_run(self, info: ValidationInfo) -> Self:
        """Check progress consistency and optional receiving-run compatibility."""
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
                raise ValueError(
                    f"Checkpoint plans {schedule.total_steps} updates, but this run plans "
                    f"{info.context['total_steps']}. The count follows the dataset size, "
                    "batch size and max_steps."
                )
        return self
