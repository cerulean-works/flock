import logging
import math
import random
from collections.abc import Callable, Iterable, Mapping
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Literal, Self

import torch
import torch._functorch.config as functorch_config
import torch.distributed as dist
import torch.nn.functional as F
import wandb
from lance.torch.data import SafeLanceDataset
from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, ValidationInfo, field_validator, model_validator
from torch import nn
from torch.nn.parallel import DistributedDataParallel as DDP
from torch.utils.data import DataLoader
from transformers import TokenizersBackend
from wandb import Run

from flock.eval.model import DEFAULT_BATCH_TOKENS, FlockCausalLM, ScoringModel
from flock.eval.run import PreparedTask, prepare_task, score_prepared
from flock.eval.tasks import TASKS
from flock.training.core.checkpoint import CheckpointManager
from flock.training.core.chunks import TokenChunkDataset
from flock.training.core.data_utils import Batch, LanceBatchCollator, ResumableBatchDataSampler
from flock.training.core.distributed import DistributedRuntime, master_broadcast, master_only
from flock.training.core.lr_scheduler import LRSchedulerConfig, LRSchedulerState, StepLRScheduler
from flock.training.core.models import BaseModel
from flock.training.core.optimizers import BaseOptimizer, OptimizerConfig, OptimizerInterface


class TrainerConfig(PydanticBaseModel):
    """Configure one shuffled pass, or its first `max_steps` updates, with tracking and saves.

    Fields are frozen after validation; nested lists are still mutable. Unknown
    fields are rejected. Positive intervals count optimizer updates, not tokens.

    Args:
        run_name (`str`):
            Name used for the default checkpoint directory and evaluation labels.
            This is not passed to `wandb.init` as the tracking run's name.
        input_column (`str`): Text column read from each Lance row.
        batch_size (`int`): Positive number of dataset items per rank per optimizer update; chunks when using a token cache.
        max_steps (`int`, *optional*):
            Positive cap on optimizer updates. The run ends at the cap, or at the end of
            the pass if that comes first. Fractional schedule times are fractions of the
            capped run; a schedule whose update counts exceed it is rejected when the
            trainer is built.
        optimizer_config (`OptimizerConfig`):
            Optimizer settings. Only `AdamWConfig` is currently implemented.
        lr_scheduler_config (`LRSchedulerConfig`): Learning-rate timeframes and interpolation.
        dataloader_prefetch_factor (`int`, *optional*, defaults to `4`):
            Positive batches prefetched per worker. Must be `None` with zero workers.
            With workers, `None` uses PyTorch's default.
        dataloader_num_workers (`int`, *optional*, defaults to `4`):
            Nonnegative number of spawned loader processes.
        validation_tasks (`list[str]`, *optional*, defaults to `[]`):
            Registered evaluation task keys. An empty list skips evaluation.
        validation_steps (`int`, *optional*):
            Positive evaluation interval. Also evaluates at the final update when set.
        validation_limit (`int`, *optional*): Positive document limit per task.
        validation_batch_tokens (`int`, *optional*, defaults to `32768`):
            Positive padded-token budget per scoring batch. One request can exceed it.
        wandb_project (`str`): Project passed to `wandb.init` during construction.
        wandb_mode (`str`, *optional*, defaults to `"online"`):
            Tracking mode: `"online"`, `"offline"`, `"disabled"` or `"shared"`.
        output_dir (`str`, *optional*):
            Checkpoint root. Uses `.training-checkpoints/<run_name>` in the current
            directory when omitted. The trainer creates it during construction.
        save_steps (`int`, *optional*):
            Positive checkpoint interval. Also saves at the final update and during
            loop cleanup when the current step is between intervals.
        save_total_limit (`int`, *optional*):
            Positive local retention limit, ordered by `flock.json`'s `saved_at`.
            Applied during final cleanup after all writes and uploads finish.
        save_to_hub_repo (`str`, *optional*):
            Hub destination `org/model`. Each new checkpoint starts an asynchronous
            upload tagged `checkpoint-<step>`.
        overwrite_output_dir (`bool`, *optional*, defaults to `False`):
            Allow fresh training in a nonempty directory and replacement of a step's
            checkpoint. Blocking saves reuse an existing step without rewriting it.
        model_dtype (`torch.dtype`, *optional*, defaults to `torch.bfloat16`):
            Autocast dtype. The trainer moves the model without changing its weight
            dtype; supply FP32 weights to retain FP32 master parameters.
        torch_compile (`bool`, *optional*, defaults to `False`):
            Compile the training model and token loss when training starts, after
            checkpoint restoration. Under DDP the DDP wrapper is compiled, so
            gradient communication overlaps the compiled backward; evaluation then
            runs eagerly. Loss collectives, optimizer, scheduler and loop
            management remain eager.
        torch_compile_mode (`str`, *optional*, defaults to `"default"`):
            PyTorch compilation mode: `"default"`, `"reduce-overhead"`,
            `"max-autotune"` or `"max-autotune-no-cudagraphs"`.
        max_norm (`float`, *optional*, defaults to `1.0`):
            Finite, nonnegative gradient clipping threshold. Zero clips gradients to zero.
        logging_steps (`int`, *optional*, defaults to `10`):
            Positive logging interval. Also logs at the final update.
        seed (`int`, *optional*, defaults to `42`):
            Python/PyTorch training seed and deterministic sampler seed. Model
            initialization is controlled separately by the model configuration.

    Raises:
        `pydantic.ValidationError`: If fields or the worker/prefetch combination are invalid.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True, extra="forbid")

    run_name: str

    input_column: str
    batch_size: int = Field(gt=0)
    max_steps: int | None = Field(default=None, gt=0)

    optimizer_config: OptimizerConfig
    lr_scheduler_config: LRSchedulerConfig

    dataloader_prefetch_factor: int | None = Field(default=4, gt=0)
    dataloader_num_workers: int = Field(default=4, ge=0)

    validation_tasks: list[str] = Field(default_factory=list)
    validation_steps: int | None = Field(default=None, gt=0)
    validation_limit: int | None = Field(default=None, gt=0)
    validation_batch_tokens: int = Field(default=DEFAULT_BATCH_TOKENS, gt=0)

    wandb_project: str
    wandb_mode: Literal["online", "offline", "disabled", "shared"] = "online"

    output_dir: str | None = None
    save_steps: int | None = Field(default=None, gt=0)
    save_total_limit: int | None = Field(default=None, gt=0)
    save_to_hub_repo: str | None = None
    overwrite_output_dir: bool = False

    # Master weights are always stored in FP32, but the model can be trained in BF16.
    model_dtype: torch.dtype = torch.bfloat16
    torch_compile: bool = False
    # This four mode can be find in the https://docs.pytorch.org/docs/2.15/generated/torch.compile.html
    torch_compile_mode: Literal[
        "default", "reduce-overhead", "max-autotune", "max-autotune-no-cudagraphs"
    ] = "default"
    max_norm: float = Field(default=1.0, ge=0, allow_inf_nan=False)
    logging_steps: int = Field(default=10, gt=0)
    seed: int = 42

    @field_validator("validation_tasks")
    @classmethod
    def validate_tasks(cls, tasks: list[str]) -> list[str]:
        """Reject task keys absent from the evaluation registry."""
        if any(task not in TASKS for task in tasks):
            raise ValueError(
                f"Invalid validation task(s) specified. Supported tasks are: {list(TASKS.keys())}"
            )
        return tasks

    @model_validator(mode="after")
    def validate_dataloader(self) -> Self:
        """Require disabled prefetching when loading in the training process."""
        if self.dataloader_num_workers == 0 and self.dataloader_prefetch_factor is not None:
            raise ValueError(
                "dataloader_prefetch_factor must be None when dataloader_num_workers is 0"
            )
        return self


class CheckpointTrainerState(PydanticBaseModel):
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

    model_config = ConfigDict(
        arbitrary_types_allowed=True, strict=True, extra="forbid", revalidate_instances="always"
    )

    loss_sum: torch.Tensor
    loss_max: torch.Tensor
    grad_norm_sum: torch.Tensor
    grad_norm_max: torch.Tensor
    step: torch.Tensor
    total_tokens: torch.Tensor
    window_tokens: torch.Tensor
    window_steps: torch.Tensor

    @field_validator("loss_sum", "loss_max", "grad_norm_sum", "grad_norm_max")
    @classmethod
    def validate_metric(cls, value: torch.Tensor) -> torch.Tensor:
        """Require scalar floating-point metric buffers before copying any state."""
        if value.ndim != 0 or not value.is_floating_point():
            raise ValueError("Training metrics must be scalar floating-point tensors")
        return value

    @field_validator("step", "total_tokens", "window_tokens", "window_steps")
    @classmethod
    def validate_counter(cls, value: torch.Tensor) -> torch.Tensor:
        """Require a nonnegative scalar int64 tensor for each progress counter."""
        if value.ndim != 0 or value.dtype != torch.int64 or value.item() < 0:
            raise ValueError("Training counters must be nonnegative scalar int64 tensors")
        return value


class CheckpointRankState(PydanticBaseModel):
    """Store one rank's RNG streams and deterministic sampler cursor.

    Attributes:
        cpu_rng_state (`torch.Tensor`): PyTorch CPU RNG state.
        cuda_rng_state (`list[torch.Tensor]` or `None`): This process's CUDA RNG states.
        python_rng_state (`tuple`): Python `random` state.
        data_state (`dict[str, int]`): Completed global batches in `batch_cursor`.
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True, strict=True, extra="forbid", revalidate_instances="always"
    )

    cpu_rng_state: torch.Tensor
    cuda_rng_state: list[torch.Tensor] | None
    python_rng_state: tuple[int, tuple[int, ...], float | None]
    data_state: dict[str, int]

    @field_validator("cpu_rng_state")
    @classmethod
    def validate_cpu_rng(cls, value: torch.Tensor) -> torch.Tensor:
        """Check RNG bytes on a private generator before changing live state."""
        try:
            torch.Generator().set_state(value)
        except (RuntimeError, TypeError) as error:
            raise ValueError("Invalid CPU RNG state") from error
        return value

    @field_validator("python_rng_state")
    @classmethod
    def validate_python_rng(
        cls, value: tuple[int, tuple[int, ...], float | None]
    ) -> tuple[int, tuple[int, ...], float | None]:
        """Check Python RNG state without consuming the process's RNG stream."""
        random.Random(0).setstate(value)
        return value

    @field_validator("cuda_rng_state")
    @classmethod
    def validate_cuda_rng(cls, value: list[torch.Tensor] | None) -> list[torch.Tensor] | None:
        """Require CUDA RNG snapshots to contain CPU byte vectors."""
        if value is not None and any(
            item.device.type != "cpu" or item.dtype != torch.uint8 or item.ndim != 1
            for item in value
        ):
            raise ValueError("Invalid CUDA RNG state")
        return value


class TrainerCheckpoint(PydanticBaseModel):
    """Validate resumable training state stored alongside model weights.

    Attributes:
        trainer_state_dict (`CheckpointTrainerState`): Metric buffers and counters.
        optimizer_state_dict (`dict[str, Any]`): Optimizer groups and parameter state.
        lr_scheduler_state_dict (`LRSchedulerState`): Schedule and progress.
        rank_states (`dict[int, CheckpointRankState]`): Per-rank RNG and data state.
        config (`dict[str, int | str | None]`): Settings defining the sampler contract.

    Model validation checks that trainer and scheduler progress agree. With a
    validation context containing `config` and `total_steps`, it also checks
    compatibility with the receiving run before state restoration.
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True, strict=True, extra="forbid", revalidate_instances="always"
    )

    trainer_state_dict: CheckpointTrainerState
    optimizer_state_dict: dict[str, Any]
    lr_scheduler_state_dict: LRSchedulerState
    rank_states: dict[int, CheckpointRankState]
    config: dict[str, int | str | None]

    @model_validator(mode="after")
    def validate_run(self, info: ValidationInfo) -> Self:
        """Check progress consistency and optional receiving-run compatibility."""
        step = int(self.trainer_state_dict.step.item())
        schedule = self.lr_scheduler_state_dict
        if step > schedule.total_steps or schedule.completed_steps != step:
            raise ValueError("Checkpoint trainer and scheduler progress do not match")

        world_size = self.config.get("world_size")
        if type(world_size) is not int or world_size < 1:
            raise ValueError("Checkpoint world size must be a positive integer")
        if not self.rank_states or any(rank < 0 or rank >= world_size for rank in self.rank_states):
            raise ValueError("Checkpoint contains invalid rank states")
        for rank_state in self.rank_states.values():
            if rank_state.data_state != {"batch_cursor": step}:
                raise ValueError("Checkpoint sampler cursor and trainer progress do not match")

        if info.context is not None:
            if self.config != info.context["config"]:
                raise ValueError(
                    "Checkpoint dataset size, chunk cache, batch size, seed, input column or world size changed"
                )
            if schedule.total_steps != info.context["total_steps"]:
                raise ValueError(
                    f"Checkpoint plans {schedule.total_steps} updates, but this run plans "
                    f"{info.context['total_steps']}. Set max_steps so the run plans "
                    f"{schedule.total_steps} updates."
                )
            if info.context["rank"] not in self.rank_states:
                raise ValueError("Checkpoint has no state for this rank")
        return self


class TrainerState(nn.Module):
    """Accumulate device-resident counters and metrics as checkpointable buffers.

    Loss means are weighted by valid target tokens. Gradient norm means are
    weighted by updates. Window resets preserve global step and token counts.
    The trainer supplies synchronized losses and token counts, and the norm of
    DDP-synchronized gradients. State updates perform no communication.
    """

    loss_sum: torch.Tensor
    loss_max: torch.Tensor
    grad_norm_sum: torch.Tensor
    grad_norm_max: torch.Tensor

    step: torch.Tensor
    total_tokens: torch.Tensor
    window_tokens: torch.Tensor
    window_steps: torch.Tensor

    def __init__(self) -> None:
        """Initialize scalar FP32 metric buffers and int64 counters to zero."""
        super().__init__()
        self.register_buffer("loss_sum", torch.zeros((), dtype=torch.float))
        self.register_buffer("loss_max", torch.zeros((), dtype=torch.float))
        self.register_buffer("grad_norm_sum", torch.zeros((), dtype=torch.float))
        self.register_buffer("grad_norm_max", torch.zeros((), dtype=torch.float))

        self.register_buffer("step", torch.zeros((), dtype=torch.int64))
        self.register_buffer("total_tokens", torch.zeros((), dtype=torch.int64))
        self.register_buffer("window_tokens", torch.zeros((), dtype=torch.int64))
        self.register_buffer("window_steps", torch.zeros((), dtype=torch.int64))

    def validate_state_dict(self, state_dict: Mapping[str, Any]) -> None:
        """Reject malformed counters and metric buffers without changing live state."""
        CheckpointTrainerState.model_validate(dict(state_dict))

    def load_state_dict(
        self, state_dict: Mapping[str, Any], strict: bool = True, assign: bool = False
    ) -> None:
        """Validate all buffers before restoring them with PyTorch's loading semantics."""
        self.validate_state_dict(state_dict)
        super().load_state_dict(state_dict, strict=strict, assign=assign)

    @torch.no_grad()
    def update(
        self,
        add_step: int,
        loss: torch.Tensor,
        grad_norm: torch.Tensor,
        tokens: torch.Tensor,
    ) -> None:
        """Record one batch's detached metrics and increment progress.

        Args:
            add_step (`int`): Increment added to the global optimizer-step count.
            loss (`torch.Tensor`): Detached mean loss per rank, or a scalar locally.
            grad_norm (`torch.Tensor`): Scalar gradient norm before clipping.
            tokens (`torch.Tensor`): Valid target counts matching the loss shape.
        """
        loss, grad_norm = loss.detach(), grad_norm.detach()
        self.loss_sum.add_((loss * tokens).sum())
        self.loss_max.copy_(torch.maximum(loss.max(), self.loss_max))

        self.grad_norm_sum.add_(grad_norm)
        self.grad_norm_max.copy_(torch.maximum(grad_norm, self.grad_norm_max))

        self.step.add_(add_step)
        world_tokens = tokens.sum()
        self.total_tokens.add_(world_tokens)
        self.window_tokens.add_(world_tokens)
        self.window_steps.add_(1)

    def get_logging_metrics(self) -> dict[str, int | float]:
        """Return global step, window means/maxima and valid-target counts as host values.

        Call after at least one update: the gradient mean divides by window steps.
        Converting the stacked tensors to a list synchronizes accelerator work.
        """
        step, mean_loss, max_loss, mean_grad, max_grad = torch.stack(
            (
                self.step,
                self.loss_sum / self.window_tokens.clamp_min(1),
                self.loss_max,
                self.grad_norm_sum / self.window_steps,
                self.grad_norm_max,
            )
        ).tolist()
        # Stacking with the FP32 means would make counts above 2**24 inexact; keep them int64.
        window_tokens, total_tokens = torch.stack((self.window_tokens, self.total_tokens)).tolist()

        return {
            "global_step": step,
            "train/loss_mean": mean_loss,
            "train/loss_window_max": max_loss,
            "train/grad_norm_mean": mean_grad,
            "train/grad_norm_window_max": max_grad,
            "train/tokens": window_tokens,
            "train/total_tokens": total_tokens,
        }

    @torch.no_grad()
    def reset_window(self) -> None:
        """Clear window metrics and counts while preserving global progress."""
        for name in (
            "loss_sum",
            "loss_max",
            "grad_norm_sum",
            "grad_norm_max",
            "window_tokens",
            "window_steps",
        ):
            getattr(self, name).zero_()

    @torch.no_grad()
    def reset_global(self) -> None:
        """Clear all metrics, the global step and the total target-token count."""
        self.reset_window()
        self.step.zero_()
        self.total_tokens.zero_()


class Trainer:
    """Train a causal model for one pass or `max_steps` updates on CPU or distributed devices.

    Construction starts a W&B run, moves the supplied model without casting its
    weights, constructs the optimizer/scheduler and creates the output directory.
    Each batch is one optimizer update, including a final partial batch. Finish
    tracking explicitly with `trainer.run.finish()` after training or evaluation.

    Args:
        config ([`training.TrainerConfig`]): Validated training and persistence settings.
        model ([`training.core.models.BaseModel`]):
            Model accepting a `Batch` and returning `[batch, sequence, vocabulary]`
            logits. Supply FP32 weights for the default BF16 autocast workflow.
        tokenizer (`TokenizersBackend`):
            Tokenizer configured with a padding token, padding side and maximum
            length. The collator applies padding, truncation and target shifting.
        dataset (`SafeLanceDataset` or `TokenChunkDataset`): Finite, indexable text rows or cached token chunks.
        runtime ([`training.core.distributed.DistributedRuntime`] or `None`, *optional*):
            Distributed runtime. If `None`, detect ranks and device from the environment.

    Raises:
        `NotImplementedError`: If the optimizer configuration selects Muon.
        `ValueError`: If the learning-rate schedule does not fit the planned updates.

    There is no gradient accumulation or automatic dtype
    conversion. Use a main-module guard when starting spawned loader workers.
    """

    # Set when training first compiles; class-level so partially built trainers stay eager.
    _compiled_loss: (
        Callable[[torch.Tensor, torch.Tensor], tuple[torch.Tensor, torch.Tensor]] | None
    ) = None

    def __init__(
        self,
        *,
        config: TrainerConfig,
        model: BaseModel,
        tokenizer: TokenizersBackend,
        dataset: SafeLanceDataset | TokenChunkDataset,
        runtime: DistributedRuntime | None = None,
    ) -> None:
        """Initialize tracking, device placement, optimization and checkpoint storage."""
        if isinstance(dataset, TokenChunkDataset):
            if dataset.chunk_length > tokenizer.model_max_length:
                raise ValueError("chunk_length exceeds tokenizer model_max_length")
            if dataset.manifest["input_column"] != config.input_column:
                raise ValueError("Chunk cache input column does not match Trainer config")
            digest = TokenChunkDataset.tokenizer_fingerprint(tokenizer)
            if dataset.manifest["tokenizer"] != digest:
                raise ValueError("Chunk cache tokenizer does not match Trainer tokenizer")
        self.run_name = config.run_name
        self.seed = config.seed

        self.runtime = runtime if runtime is not None else DistributedRuntime.from_env()
        self.device = self.runtime.device
        if self.device.type == "cuda":
            torch.cuda.set_device(self.device)
        self.state = TrainerState().to(self.device)

        if self.runtime.enabled and not dist.is_initialized():
            raise RuntimeError(
                "Distributed runtime is not initialized. Use `distributed_run` context manager."
            )

        logging.basicConfig(
            level=logging.INFO,
            format=f"%(asctime)s %(levelname)s %(name)s rank:{self.runtime.rank} %(message)s",
            datefmt="%m-%d %H:%M:%S",
        )
        self.logger = logging.getLogger(__name__)
        self.run: Run | None = None
        self._init_wandb_tracker(config)

        self.model = model.to(self.device)
        self.training_model = (
            DDP(self.model, device_ids=[self.device.index] if self.device.type == "cuda" else None)
            if self.runtime.enabled
            else self.model
        )

        self.tokenizer = tokenizer
        self.dataset = dataset
        self.ds_input_column = config.input_column
        self.batch_size = config.batch_size

        self.dataloader_prefetch_factor = config.dataloader_prefetch_factor
        self.dataloader_num_workers = config.dataloader_num_workers

        self.model_dtype = config.model_dtype
        self.torch_compile = config.torch_compile
        self.torch_compile_mode = config.torch_compile_mode
        self.max_norm = config.max_norm
        self.logging_steps = config.logging_steps

        self.total_steps = math.ceil(
            len(self.dataset) / (self.batch_size * self.runtime.world_size)
        )
        if config.max_steps is not None:
            # The schedule and the checkpoint check plan over the capped run.
            self.total_steps = min(self.total_steps, config.max_steps)
        optimizer_factory = OptimizerInterface.get_interface(type(config.optimizer_config))
        self.optimizer: BaseOptimizer = optimizer_factory(
            config.optimizer_config, self.model.parameters()
        )
        self.lr_scheduler = StepLRScheduler(
            self.optimizer, config.lr_scheduler_config, self.total_steps
        )

        self.validation_tasks = config.validation_tasks
        self.validation_steps = config.validation_steps
        self.validation_limit = config.validation_limit
        self.validation_batch_tokens = config.validation_batch_tokens
        self._prepared_validation_tasks: dict[str, PreparedTask] = {}

        self.output_dir: Path = (
            Path(config.output_dir)
            if config.output_dir is not None
            else Path.cwd().joinpath(".training-checkpoints").joinpath(self.run_name)
        )
        self.save_steps = config.save_steps
        self.save_total_limit = config.save_total_limit
        self.save_to_hub_repo = config.save_to_hub_repo
        self.overwrite_output_dir = config.overwrite_output_dir

        self.checkpoint_manager = CheckpointManager(
            self.output_dir,
            save_to_hub_repo=self.save_to_hub_repo,
            save_total_limit=self.save_total_limit,
            overwrite_output_dir=self.overwrite_output_dir,
            run=self.run,
            logger=self.logger,
        )
        if self.runtime.is_master:
            self.output_dir.mkdir(parents=True, exist_ok=True)

        self.runtime.barrier()
        if self.runtime.is_master:
            self.logger.info("Trainer initialized on %s", self.device)

    @master_broadcast
    def _init_wandb_tracker(self, config: TrainerConfig) -> None:
        self.run = wandb.init(
            project=config.wandb_project, mode=config.wandb_mode, config=config.model_dump()
        )
        self.run.define_metric("global_step")
        self.run.define_metric("train/*", step_metric="global_step")
        self.run.define_metric("eval/*", step_metric="global_step")

    def _get_training_dataloader(self, skip_batches: int = 0) -> DataLoader:
        """Build a seeded, resumable loader with spawned workers and CUDA pinning."""
        collator = LanceBatchCollator(
            self.tokenizer,
            self.ds_input_column,
            token_chunks=isinstance(self.dataset, TokenChunkDataset),
        )
        sampler = ResumableBatchDataSampler(
            batch_size=self.batch_size,
            dataset_size=len(self.dataset),
            shuffle=True,
            seed=self.seed,
            batch_cursor=skip_batches,
            rank=self.runtime.rank,
            world_size=self.runtime.world_size,
            yield_empty_last_batch=True if self.runtime.enabled else None,
            max_batches=self.total_steps,
        )

        return DataLoader(
            self.dataset,
            # this sampler does batching + shuffling, so we don't need to set batch_size or shuffle here
            batch_sampler=sampler,
            num_workers=self.dataloader_num_workers,
            collate_fn=collator.collate_fn,
            # lance multi-threads internally, so its best we not use fork
            multiprocessing_context="spawn" if self.dataloader_num_workers > 0 else None,
            # accelerate batch host to device transfers
            pin_memory=self.device.type == "cuda",
            prefetch_factor=self.dataloader_prefetch_factor,
            persistent_workers=self.dataloader_num_workers > 0,
            # Starting a resumed iterator must not consume the model's RNG stream.
            generator=torch.Generator().manual_seed(self.seed),
        )

    def _token_loss(
        self, output: torch.Tensor, labels: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """Return this rank's mean loss over valid targets and the valid-target count.

        Logits have shape `[batch, sequence, vocabulary]`; labels have shape
        `[batch, sequence]` and are already shifted. Ignore `-100` targets and
        clamp the denominator to one so an all-ignored batch has zero loss.
        This part has no collectives, so it compiles without graph breaks.
        """
        # output is [B, T, V], labels is [B, T]
        num_tokens = (labels != -100).sum()

        loss_sum = F.cross_entropy(
            output.float().reshape(-1, output.shape[-1]),
            labels.reshape(-1),
            ignore_index=-100,
            reduction="sum",
        )

        # Keep normalization on-device; an all-ignored batch has zero loss/gradient.
        return loss_sum / num_tokens.clamp_min(1), num_tokens

    def _calc_batch_loss(
        self, output: torch.Tensor, labels: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Return backward loss, detached per-rank mean losses and target counts.

        Uses the compiled token loss when compilation is active. In DDP, gather
        metrics once and scale the local loss so averaged gradients match the
        global token mean. Without DDP, metrics remain scalar tensors.
        """
        loss, num_tokens = (self._compiled_loss or self._token_loss)(output, labels)
        losses, tokens = loss.detach(), num_tokens
        if self.runtime.enabled:
            # FP64 preserves batch counts through 2**53; accumulate counts as int64.
            metrics = torch.stack((losses.double(), tokens.double()))
            metrics_list = [torch.empty_like(metrics) for _ in range(self.runtime.world_size)]
            dist.all_gather(metrics_list, metrics)

            gathered = torch.stack(metrics_list)
            losses, tokens = gathered[:, 0], gathered[:, 1].long()

            # DDP averages gradients over ranks; weight each rank by its valid targets.
            scale = self.runtime.world_size * num_tokens / tokens.sum().clamp_min(1)
            loss = loss * scale.to(loss.dtype)

        return loss, losses, tokens

    def _set_seed(self) -> None:
        """Seed Python and PyTorch before starting or restoring training."""
        random.seed(self.seed + self.runtime.rank)
        torch.manual_seed(self.seed + self.runtime.rank)

    def _inner_training_loop(self, dataloader: Iterable[Batch], start_step: int) -> None:
        """Update once per batch, then log, save and evaluate at configured intervals."""
        for step, batch in enumerate(dataloader, start=start_step + 1):
            if self._compiled_loss is not None:
                torch.compiler.cudagraph_mark_step_begin()
            self.optimizer.zero_grad()

            batch = {k: v.to(self.device, non_blocking=True) for k, v in batch.items()}

            with torch.autocast(self.device.type, dtype=self.model_dtype):
                output: torch.Tensor = self.training_model(batch)
                loss, losses, num_tokens = self._calc_batch_loss(output, batch["labels"])

            loss.backward()
            grad_norm = torch.nn.utils.clip_grad_norm_(
                self.model.parameters(),
                max_norm=self.max_norm,
            )

            self.optimizer.step()
            self.lr_scheduler.step()

            self.state.update(add_step=1, loss=losses, grad_norm=grad_norm, tokens=num_tokens)

            if step % self.logging_steps == 0 or step == self.total_steps:
                if self.runtime.is_master:
                    metrics_dict = self.state.get_logging_metrics()
                    self._log_metrics(metrics_dict)
                self.state.reset_window()

            # Cleanup handles the final model save, so we only save at intervals here.
            if self.save_steps is not None and (step % self.save_steps == 0):
                self._save_model(step)

            if self.validation_steps and (
                step % self.validation_steps == 0 or step == self.total_steps
            ):
                self.evaluate()

    def train(self, resume_from_checkpoint: bool | str | Path = False) -> None:
        """Train one pass or `max_steps` updates, optionally restoring a checkpoint first.

        Args:
            resume_from_checkpoint (`bool`, `str` or `Path`, *optional*, defaults to `False`):
                With `True`, select the checkpoint with the latest `saved_at` in
                `flock.json`. A path selects a checkpoint folder explicitly.
                With `False`, reset counters and start from the current model weights.

        Raises:
            `ValueError`: If the dataset is empty or checkpoint settings are incompatible.
            `RuntimeError`: If fresh training finds a nonempty output directory and
                `overwrite_output_dir` is false, or checkpoint weights do not match.
            `FileNotFoundError`: If automatic resume finds no checkpoint.

        Resume skips already consumed batches in the seeded permutation. Loop
        cleanup saves an off-interval step when saving is enabled and waits for
        outstanding uploads. Training errors propagate after cleanup. The W&B
        run remains open; call `run.finish()` in your own `finally` block.
        """
        if self.total_steps == 0:
            raise ValueError("Training requires at least one batch from the dataloader")
        self.logger.info("Setting up trainer...")
        # Set random state at beginning of training to ensure reproducibility
        self._set_seed()

        if not resume_from_checkpoint:
            if (
                self.runtime.is_master
                and any(self.output_dir.iterdir())
                and not self.overwrite_output_dir
            ):
                raise RuntimeError(
                    f"Save directory {self.output_dir} is not empty. "
                    "Resume from a checkpoint or specify an empty directory."
                )
            self.state.reset_global()
            start_step = 0
            self.logger.info("Initializing training dataloader")
            dataloader = self._get_training_dataloader()
            self.logger.info(f"Dataloader with {len(dataloader)} steps loaded")
        else:
            self.logger.info("Resuming training from checkpoint")
            if resume_from_checkpoint is True:
                checkpoint = self.checkpoint_manager.latest_checkpoint()
            else:
                checkpoint = Path(resume_from_checkpoint)
            self.load_state_dict(
                self.checkpoint_manager.load_checkpoint(
                    checkpoint, self.model, validate_state=self.validate_state_dict
                )
            )
            start_step = int(self.state.step.item())
            dataloader = self._get_training_dataloader(skip_batches=start_step)
            self.logger.info(
                f"Resuming from step {start_step}, with {self.total_steps - start_step} steps remaining"
            )

        training_completed: bool = False
        try:
            self.model.train()
            if self.torch_compile and self._compiled_loss is None:
                # Compile in place: checkpoints keep the plain module's state-dict keys.
                # Compiling the DDP wrapper lets Dynamo split graphs at gradient buckets.
                self.training_model.compile(mode=self.torch_compile_mode, fullgraph=False)
                self._compiled_loss = torch.compile(
                    self._token_loss, mode=self.torch_compile_mode, fullgraph=False
                )
            self.logger.info("Starting training")
            # We use autocast off for backward pass since the trainer also do not use autocast for the backward pass.
            with (
                functorch_config.patch(backward_pass_autocast="off")
                if self.torch_compile
                else nullcontext()
            ):
                self._inner_training_loop(dataloader, start_step)
            training_completed = True
        finally:
            if (
                self.save_steps
                and (not self.runtime.enabled or training_completed)
                and self.state.step % self.save_steps != 0
                # Ensure a crash-resumed checkpoint is not saved if no steps were completed
                and self.state.step > start_step
            ):
                self._save_model(int(self.state.step.item()))
            if self.runtime.is_master:
                self.checkpoint_manager.finish_checkpoint_uploads()

    @master_broadcast
    @torch.no_grad()
    def evaluate(self) -> None:
        """Score configured tasks and log metrics at the current global step.

        Prepare and cache tokenized requests on first use. Use the training
        autocast dtype, disabling autocast for FP32 evaluation. Restore every
        module's previous training mode even on failure. Return without logging
        if no tasks are configured. Log and skip `KeyError` or `FileExistsError`
        raised during preparation/scoring; propagate other errors. Evaluation
        runs eagerly when training uses a compilation mode with CUDA graphs.

        Metrics use `eval/<task>/<metric>` keys. No scores are returned.
        """
        if not self.validation_tasks:
            return

        step = int(self.state.step.item())
        scorer = ScoringModel(
            FlockCausalLM(self.model),
            self.tokenizer,
            name=f"{self.run_name}@step-{step}",
            batch_tokens=self.validation_batch_tokens,
        )

        # In eval mode, the model is not updated and dropout is disabled. We restore the original modes after evaluation.
        modes = [(module, module.training) for module in self.model.modules()]
        try:
            self.model.eval()
            # Prepare on the first evaluation, so disabled validation never loads data.
            # Keep token IDs on the host and reuse them at every subsequent step.
            for task in self.validation_tasks:
                if task not in self._prepared_validation_tasks:
                    self._prepared_validation_tasks[task] = prepare_task(
                        scorer, TASKS[task], limit=self.validation_limit
                    )
            # We make sure the model is evaluated eagerly so validation batches cannot extend that graph path.
            # For more details, please check https://github.com/cerulean-works/flock/pull/112
            # NOTE: If we are doing a really large training run, we may still use torch.compile for validation to improve performance.
            with (
                torch.compiler.set_stance("force_eager")
                if self.torch_compile
                and self.torch_compile_mode in ("reduce-overhead", "max-autotune")
                else nullcontext(),
                torch.autocast(
                    self.device.type,
                    dtype=self.model_dtype,
                    enabled=self.model_dtype != torch.float32,
                ),
            ):
                results = {
                    task: score_prepared(scorer, self._prepared_validation_tasks[task])
                    for task in self.validation_tasks
                }
        except (FileExistsError, KeyError) as e:
            self.logger.error(f"Error during evaluation, skipping: {e}")
            return
        finally:
            for module, training in modes:
                module.training = training

        log_dict = {"global_step": step}
        for task_key, score_dict in results.items():
            for metric_key, score in score_dict.metrics.items():
                log_dict[f"eval/{task_key}/{metric_key}"] = score

        self._log_metrics(log_dict)

    @master_only
    def _log_metrics(self, metrics_dict: dict[str, int | float]) -> None:
        """Log a message to the trainer's logger and W&B run if available."""
        self.logger.info(metrics_dict)
        if self.run is not None:
            self.run.log(metrics_dict)

    def _save_model(self, step: int, blocking: bool = False) -> None:
        """Gather rank-local resume state, then save the checkpoint on rank zero.

        Every rank must call at the same step. Only RNG and sampler state cross
        the process group; replicated weights and optimizer state are saved once.

        Args:
            step (`int`): Global step used for the checkpoint folder name.
        """
        state = None
        try:
            state = self.state_dict()
        except Exception:
            self.logger.exception("Error capturing checkpoint state")
        rank_state = state["rank_states"][self.runtime.rank] if state is not None else None
        rank_states = [rank_state]
        if self.runtime.enabled:
            rank_states = [None] * self.runtime.world_size if self.runtime.is_master else None
            dist.gather_object(rank_state, rank_states, dst=0)
        if self.runtime.is_master:
            if state is None or rank_states is None or any(value is None for value in rank_states):
                self.logger.error("Skipping checkpoint: a rank could not capture its resume state")
            else:
                state["rank_states"] = dict(enumerate(rank_states))
                self.checkpoint_manager.save_model(
                    step, self.model, training_state=state, blocking=blocking
                )
        self.runtime.barrier()

    def state_dict(self) -> dict[str, Any]:
        """Return shared training state and this rank's RNG and sampler state.

        Model weights are saved separately. State tensors can share storage
        with live objects; serialize immediately rather than keeping this as an
        immutable in-memory snapshot. This method performs no communication.
        Scheduled saves gather the rank-local records before rank zero writes.
        """
        return {
            "trainer_state_dict": self.state.state_dict(),
            "optimizer_state_dict": self.optimizer.state_dict(),
            "lr_scheduler_state_dict": self.lr_scheduler.state_dict(),
            "rank_states": {
                self.runtime.rank: {
                    "cpu_rng_state": torch.get_rng_state(),
                    "cuda_rng_state": (
                        torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None
                    ),
                    "python_rng_state": random.getstate(),
                    "data_state": {"batch_cursor": int(self.state.step.item())},
                }
            },
            "config": {
                "chunk_fingerprint": (
                    self.dataset.fingerprint
                    if isinstance(self.dataset, TokenChunkDataset)
                    else None
                ),
                "dataset_size": len(self.dataset),
                "batch_size": self.batch_size,
                "seed": self.seed,
                "input_column": self.ds_input_column,
                "world_size": self.runtime.world_size,
            },
        }

    def validate_state_dict(
        self, state: Mapping[str, Any] | TrainerCheckpoint
    ) -> TrainerCheckpoint:
        """Validate the run contract and delegate component checks without changing state.

        Args:
            state (`Mapping[str, Any]` or `TrainerCheckpoint`): Saved training state.

        Returns:
            `TrainerCheckpoint`: Validated state ready for restoration.

        Raises:
            `ValueError`: If data, progress, optimizer or scheduler settings differ.
        """
        if not state:
            raise ValueError("Checkpoint has no training state")
        checkpoint = TrainerCheckpoint.model_validate(
            state,
            context={
                "config": {
                    "chunk_fingerprint": (
                        self.dataset.fingerprint
                        if isinstance(self.dataset, TokenChunkDataset)
                        else None
                    ),
                    "dataset_size": len(self.dataset),
                    "batch_size": self.batch_size,
                    "seed": self.seed,
                    "input_column": self.ds_input_column,
                    "world_size": self.runtime.world_size,
                },
                "total_steps": self.total_steps,
                "rank": self.runtime.rank,
            },
        )
        cuda_rng = checkpoint.rank_states[self.runtime.rank].cuda_rng_state
        if (
            torch.cuda.is_available()
            and cuda_rng is not None
            and len(cuda_rng) != torch.cuda.device_count()
        ):
            raise ValueError("Checkpoint CUDA RNG device count changed for this rank")
        self.lr_scheduler.validate_state_dict(checkpoint.lr_scheduler_state_dict.model_dump())

        self.state.validate_state_dict(checkpoint.trainer_state_dict.model_dump())
        saved_groups = checkpoint.optimizer_state_dict["param_groups"]
        current_groups = self.optimizer.param_groups
        if len(saved_groups) != len(current_groups):
            raise ValueError("Checkpoint optimizer parameter groups do not match this run")
        for index, (saved, current) in enumerate(zip(saved_groups, current_groups, strict=True)):
            if len(saved["params"]) != len(current["params"]):
                raise ValueError(
                    f"Checkpoint optimizer parameter group {index} size does not match this run"
                )
            # The current LR depends on progress; original LRs are checked by the scheduler.
            saved_settings = {
                key: value for key, value in saved.items() if key not in ("params", "lr")
            }
            current_settings = {
                key: value for key, value in current.items() if key not in ("params", "lr")
            }
            if saved_settings != current_settings:
                raise ValueError(
                    f"Checkpoint optimizer parameter group {index} settings do not match this run"
                )
        return checkpoint

    def load_state_dict(self, state: Mapping[str, Any] | TrainerCheckpoint) -> None:
        """Validate and restore training state without loading model weights.

        Args:
            state (`dict[str, Any]` or `TrainerCheckpoint`): State from
                `state_dict` or a deserialized checkpoint.

        Raises:
            `ValueError`: If progress, data, optimizer or scheduler settings differ.

        Selects this rank's sampler and RNG record from a shared checkpoint.
        Loading performs no communication. Logs a warning when CUDA
        is available but the checkpoint has no CUDA RNG state.
        """
        checkpoint = self.validate_state_dict(state)

        self.state.load_state_dict(checkpoint.trainer_state_dict.model_dump())
        self.optimizer.load_state_dict(checkpoint.optimizer_state_dict)
        self.lr_scheduler.load_state_dict(checkpoint.lr_scheduler_state_dict.model_dump())

        rank_state = checkpoint.rank_states[self.runtime.rank]
        torch.set_rng_state(rank_state.cpu_rng_state)
        random.setstate(rank_state.python_rng_state)
        if torch.cuda.is_available():
            if rank_state.cuda_rng_state is not None:
                torch.cuda.set_rng_state_all(rank_state.cuda_rng_state)
            else:
                self.logger.warning(
                    "CUDA is available but no CUDA RNG state was found in the loaded trainer state."
                )
