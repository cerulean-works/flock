"""Train causal language models with evaluation and resumable local checkpoints."""

import json
import logging
import math
import random
import shutil
import tempfile
from dataclasses import asdict
from itertools import islice
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Literal, Self, cast, overload

import torch
import torch.nn.functional as F
import wandb
from lance.torch.data import SafeLanceDataset
from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, field_validator, model_validator
from safetensors.torch import load_model
from torch import nn
from torch.optim import AdamW, Muon, Optimizer
from torch.utils.data import DataLoader
from transformers import TokenizersBackend

from flock.eval.model import DEFAULT_BATCH_TOKENS, FlockCausalLM, ScoringModel
from flock.eval.run import PreparedTask, prepare_task, score_prepared
from flock.eval.tasks import TASKS
from flock.hub.models import PendingUpload, save_to_hub
from flock.hub.models.files import import_path, read_training_state, weight_files, write_model
from flock.training.core.checkpoint import TrainerCheckpoint
from flock.training.core.data_utils import Batch, LanceBatchCollator, ResumableBatchDataSampler
from flock.training.core.lr_scheduler import LRSchedulerConfig, StepLRScheduler
from flock.training.core.models import BaseModel
from flock.training.core.optimization import AdamWConfig, MuonConfig, OptimizerConfig


class TrainerConfig(PydanticBaseModel):
    """Configure one shuffled training pass and its tracking, evaluation and saves.

    Fields are frozen after validation; nested lists are still mutable. Unknown
    fields are rejected. Positive intervals count optimizer updates, not tokens.

    Args:
        run_name (`str`):
            Name used for the default checkpoint directory and evaluation labels.
            This is not passed to `wandb.init` as the tracking run's name.
        input_column (`str`): Text column read from each Lance row.
        batch_size (`int`): Positive number of dataset rows per optimizer update.
        max_steps (`int`, *optional*):
            Positive cap on optimizer updates. The run and its learning-rate schedule
            end at the cap, or at the end of the pass if that comes first.
        optimizer_config (`OptimizerConfig`):
            Optimizer settings. Only `AdamWConfig` is currently implemented.
        lr_scheduler_config (`LRSchedulerConfig`): Cosine or warmup-stable-decay settings.
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
            Older checkpoints with pending uploads are temporarily retained.
        save_to_hub_repo (`str`, *optional*):
            Hub destination `org/model`. Each new checkpoint starts an asynchronous
            upload tagged `checkpoint-<step>`.
        overwrite_output_dir (`bool`, *optional*, defaults to `False`):
            Allow fresh training in a nonempty directory and replacement of a step's
            checkpoint. Blocking saves reuse an existing step without rewriting it.
        model_dtype (`torch.dtype`, *optional*, defaults to `torch.bfloat16`):
            Autocast dtype. The trainer moves the model without changing its weight
            dtype; supply FP32 weights to retain FP32 master parameters.
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


class TrainerState(nn.Module):
    """Accumulate device-resident counters and metrics as checkpointable buffers.

    Loss means are weighted by valid target tokens. Gradient norm means are
    weighted by updates. Window resets preserve global step and token counts.
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

    @torch.no_grad()
    def update(
        self, add_step: int, loss: torch.Tensor, grad_norm: torch.Tensor, tokens: torch.Tensor
    ) -> None:
        """Record one batch's detached metrics and increment progress.

        Args:
            add_step (`int`): Increment added to the global optimizer-step count.
            loss (`torch.Tensor`): Scalar mean loss over valid targets.
            grad_norm (`torch.Tensor`): Scalar gradient norm before clipping.
            tokens (`torch.Tensor`): Scalar number of valid targets in the batch.
        """
        self.loss_sum.add_(loss.detach() * tokens)
        self.loss_max = torch.maximum(loss.detach(), self.loss_max)

        self.grad_norm_sum.add_(grad_norm.detach())
        self.grad_norm_max = torch.maximum(grad_norm.detach(), self.grad_norm_max)

        self.step.add_(add_step)
        self.total_tokens.add_(tokens)
        self.window_tokens.add_(tokens)
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
    """Train a causal model for one shuffled pass on CUDA device 0 or CPU.

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
        dataset (`SafeLanceDataset`): Finite, indexable dataset of text rows.

    Raises:
        `NotImplementedError`: If the optimizer configuration selects Muon.

    There is no gradient accumulation, distributed training or automatic dtype
    conversion. Use a main-module guard when starting spawned loader workers.
    """

    def __init__(
        self,
        *,
        config: TrainerConfig,
        model: BaseModel,
        tokenizer: TokenizersBackend,
        dataset: SafeLanceDataset,
    ) -> None:
        """Initialize tracking, device placement, optimization and checkpoint storage."""
        self.run_name = config.run_name
        self.seed = config.seed
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

        self.state = TrainerState().to(self.device)

        self.logger = logging.getLogger(__name__)
        self.run = wandb.init(
            project=config.wandb_project, mode=config.wandb_mode, config=config.model_dump()
        )
        self.run.define_metric("global_step")
        self.run.define_metric("train/*", step_metric="global_step")
        self.run.define_metric("eval/*", step_metric="global_step")

        self.model = model.to(self.device)
        self.tokenizer = tokenizer
        self.dataset = dataset
        self.ds_input_column = config.input_column
        self.batch_size = config.batch_size

        self.dataloader_prefetch_factor = config.dataloader_prefetch_factor
        self.dataloader_num_workers = config.dataloader_num_workers

        self.model_dtype = config.model_dtype
        self.max_norm = config.max_norm
        self.logging_steps = config.logging_steps

        self.total_steps = math.ceil(len(self.dataset) / self.batch_size)
        if config.max_steps is not None:
            # The schedules and the checkpoint check plan over the capped run.
            self.total_steps = min(self.total_steps, config.max_steps)
        self.optimizer = self._get_optimizer(config.optimizer_config)
        self.lr_scheduler = self._get_lr_scheduler(config.lr_scheduler_config, self.total_steps)

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

        self.pending_uploads: dict[Path, PendingUpload] = {}

        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _get_training_dataloader(self, skip_batches: int = 0) -> DataLoader:
        """Build a seeded, resumable loader with spawned workers and CUDA pinning."""
        collator = LanceBatchCollator(self.tokenizer, self.ds_input_column)
        sampler = ResumableBatchDataSampler(
            batch_size=self.batch_size,
            dataset_size=len(self.dataset),
            shuffle=True,
            seed=self.seed,
            skip_batches=skip_batches,
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

    @overload
    def _get_optimizer(self: Trainer, config: AdamWConfig) -> AdamW: ...

    @overload
    def _get_optimizer(self: Trainer, config: MuonConfig) -> Muon: ...

    def _get_optimizer(self, config: OptimizerConfig) -> Optimizer:
        """Construct AdamW from all model parameters; reject unimplemented optimizers."""
        if isinstance(config, AdamWConfig):
            return AdamW(self.model.parameters(), **asdict(config))
        if isinstance(config, MuonConfig):
            raise NotImplementedError("Muon supported is not added yet")
            return Muon(self.model.parameters(), **asdict(config))
        raise TypeError(
            f"Unsupported OptimizerConfig type {type(OptimizerConfig)}, supported types are: {OptimizerConfig.evaluate_value()}"
        )

    def _get_lr_scheduler(self, config: LRSchedulerConfig, total_steps: int) -> StepLRScheduler:
        """Apply the initial rate and return an optimizer-update scheduler."""
        return StepLRScheduler(self.optimizer, config, total_steps)

    def _calc_batch_loss(
        self, output: torch.Tensor, labels: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """Return FP32 token-mean cross-entropy and the nonignored target count.

        Logits have shape `[batch, sequence, vocabulary]`; labels have shape
        `[batch, sequence]` and are already shifted. Ignore `-100` targets and
        clamp the denominator to one so an all-ignored batch has zero loss.
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
        loss = loss_sum / num_tokens.clamp_min(1)
        return loss, num_tokens

    def _set_seed(self) -> None:
        """Seed Python and PyTorch before starting or restoring training."""
        random.seed(self.seed)
        torch.manual_seed(self.seed)

    def _inner_training_loop(self, dataloader: DataLoader, start_step: int) -> None:
        """Update once per batch, then log, save and evaluate at configured intervals."""
        # The loader yields the whole pass; a step cap stops it early.
        batches = islice(dataloader, self.total_steps - start_step)
        for step, batch in enumerate(batches, start=start_step + 1):
            self.optimizer.zero_grad()

            batch = cast(Batch, batch)
            batch = {k: v.to(self.device, non_blocking=True) for k, v in batch.items()}

            with torch.autocast(self.device.type, dtype=self.model_dtype):
                output: torch.Tensor = self.model(batch)
                loss, num_tokens = self._calc_batch_loss(output, batch["labels"])

            loss.backward()
            grad_norm = torch.nn.utils.clip_grad_norm_(
                self.model.parameters(),
                max_norm=self.max_norm,
            )

            self.optimizer.step()
            self.lr_scheduler.step()

            self.state.update(add_step=1, loss=loss, grad_norm=grad_norm, tokens=num_tokens)

            if step % self.logging_steps == 0 or step == self.total_steps:
                metrics_dict = self.state.get_logging_metrics()
                self.logger.info(metrics_dict)
                self.run.log(metrics_dict)
                self.state.reset_window()

            if self.save_steps is not None and (
                step % self.save_steps == 0 or step == self.total_steps
            ):
                self.save_model()

            if self.validation_steps and (
                step % self.validation_steps == 0 or step == self.total_steps
            ):
                self.evaluate()

    def train(self, resume_from_checkpoint: bool | str | Path = False) -> None:
        """Train one pass, optionally restoring a local checkpoint before iteration.

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
            raise ValueError("Training requires a non-empty dataset")
        self.logger.info("Setting up trainer...")
        # Set random state at beginning of training to ensure reproducibility
        self._set_seed()

        if not resume_from_checkpoint:
            if any(self.output_dir.iterdir()) and not self.overwrite_output_dir:
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
                checkpoints = self._checkpoints()
                checkpoint_save_time: dict[str, str] = {}

                for checkpoint in checkpoints:
                    try:
                        metadata = json.loads((checkpoint / "flock.json").read_text())
                        checkpoint_save_time[checkpoint.name] = metadata.get("saved_at", "")
                    except JSONDecodeError as e:
                        self.logger.warning(
                            f"Failed to read metadata for checkpoint {checkpoint}: {e}"
                        )
                        checkpoint_save_time[checkpoint.name] = ""

                checkpoints.sort(key=lambda path: checkpoint_save_time.get(path.name, ""))
                if not checkpoints:
                    raise FileNotFoundError(f"No checkpoints found in {self.output_dir}")
                checkpoint = checkpoints[-1]
            else:
                checkpoint = Path(resume_from_checkpoint)
            self.load_checkpoint(checkpoint)
            start_step = int(self.state.step.item())
            dataloader = self._get_training_dataloader(skip_batches=start_step)
            self.logger.info(
                f"Resuming from step {start_step}, with {self.total_steps - start_step} steps remaining"
            )

        try:
            self.model.train()
            self.logger.info("Starting training")
            self._inner_training_loop(dataloader, start_step)
        finally:
            if self.pending_uploads or (self.save_steps and self.state.step % self.save_steps != 0):
                self.save_model(blocking=True)

    @torch.no_grad()
    def evaluate(self) -> None:
        """Score configured tasks and log metrics at the current global step.

        Prepare and cache tokenized requests on first use. Use the training
        autocast dtype, disabling autocast for FP32 evaluation. Restore every
        module's previous training mode even on failure. Return without logging
        if no tasks are configured. Log and skip `KeyError` or `FileExistsError`
        raised during preparation/scoring; propagate other errors.

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
            # Match training precision without casting or copying the FP32 master weights.
            with torch.autocast(
                self.device.type,
                dtype=self.model_dtype,
                enabled=self.model_dtype != torch.float32,
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

        self.run.log(log_dict)

    def save_model(self, blocking: bool = False) -> None:
        """Write weights and complete resumable state to `checkpoint-<step>`.

        Stage files under the output root, then rename the completed directory
        into place. If configured, start a Hub upload tagged with the step.
        Upload errors are logged; local write errors propagate. Prune local
        checkpoints by `saved_at`, protecting folders still being uploaded.
        Local retention never deletes Hub versions.

        Args:
            blocking (`bool`, *optional*, defaults to `False`):
                Wait for all pending uploads. Reuse an existing current-step
                checkpoint without rewriting or starting another upload.

        Raises:
            `FileExistsError`: If a nonblocking save would replace an existing
                checkpoint and `overwrite_output_dir` is false.

        Returns `None`; asynchronous handles are held in `pending_uploads`.
        A pending older upload can temporarily exceed `save_total_limit`.
        """
        for checkpoint, upload in list(self.pending_uploads.items()):
            if upload.done():
                try:
                    upload.result()
                except Exception:
                    self.logger.exception(
                        f"Failed to upload checkpoint {checkpoint} to hub {self.save_to_hub_repo}"
                    )
                del self.pending_uploads[checkpoint]
        step = int(self.state.step.item())

        self.logger.info(f"Saving model at step {step}")
        save_path = self.output_dir.joinpath(f"checkpoint-{step}")
        if save_path.exists() and not self.overwrite_output_dir and not blocking:
            raise FileExistsError(f"Checkpoint already exists: {save_path}")

        if save_path in self.pending_uploads:
            # A folder upload reads in place; finish it before replacing those files.
            upload = self.pending_uploads[save_path]
            try:
                upload.result()
            except Exception:
                self.logger.exception(
                    f"Failed to upload checkpoint {save_path} to hub {self.save_to_hub_repo}"
                )
            del self.pending_uploads[save_path]

        # A blocking call for an already published step only needs to finish uploads.
        if not (blocking and save_path.exists()):
            # Publish the directory only after weights and training state are fully written.
            with tempfile.TemporaryDirectory(
                prefix=".checkpoint-", dir=self.output_dir
            ) as temporary:
                staged = Path(temporary) / "model"
                write_model(
                    self.model,
                    staged,
                    shard_size=5 * 2**30,
                    run=f"{self.run.entity}/{self.run.project}/{self.run.id}",
                    step=step,
                    training_state=self.get_trainer_state(),
                )
                if save_path.exists():
                    shutil.rmtree(save_path)
                staged.rename(save_path)
            if self.save_to_hub_repo is not None:
                try:
                    upload_promise = cast(
                        PendingUpload,
                        save_to_hub(
                            save_path,
                            self.save_to_hub_repo,
                            run=self.run,
                            step=step,
                            tags=[f"checkpoint-{step}"],
                            blocking=False,
                        ),
                    )
                    self.pending_uploads[save_path] = upload_promise
                except Exception:
                    self.logger.exception(
                        f"Failed to save checkpoint {save_path} to hub {self.save_to_hub_repo}"
                    )

        if blocking:
            self.logger.info("Waiting for pending uploads to finish...")
            for checkpoint in list(self.pending_uploads):
                upload = self.pending_uploads[checkpoint]
                try:
                    upload.result()
                except Exception:
                    self.logger.exception(
                        f"Failed to upload checkpoint {checkpoint} to hub {self.save_to_hub_repo}"
                    )
                del self.pending_uploads[checkpoint]
            self.logger.info("All pending uploads finished")

        if self.save_total_limit is not None:
            checkpoints = self._checkpoints()
            checkpoint_save_time: dict[str, str] = {}

            for checkpoint in checkpoints:
                try:
                    metadata = json.loads((checkpoint / "flock.json").read_text())
                    checkpoint_save_time[checkpoint.name] = metadata.get("saved_at", "")
                except JSONDecodeError as e:
                    self.logger.warning(f"Failed to read metadata for checkpoint {checkpoint}: {e}")
                    checkpoint_save_time[checkpoint.name] = ""

            checkpoints.sort(key=lambda path: checkpoint_save_time.get(path.name, ""))

            for checkpoint_to_delete in checkpoints[: -self.save_total_limit]:
                # Retain the newest checkpoints plus any older ones still being read.
                if checkpoint_to_delete in self.pending_uploads:
                    continue
                self.logger.info(f"Deleting old checkpoint {checkpoint_to_delete}")
                shutil.rmtree(checkpoint_to_delete)

    def load_checkpoint(self, checkpoint: str | Path) -> None:
        """Restore weights, optimizer, schedule, counters and RNGs from local files.

        Args:
            checkpoint (`str` or `Path`): Folder containing the model and training state.

        Raises:
            `ValueError`: If the model configuration or resume settings differ, or
                training state is absent or invalid.
            `RuntimeError`: If the combined weight shards have missing or extra keys.
            `FileNotFoundError`: If required checkpoint files are absent.

        This method does not run training or construct a resumed loader. Use
        `train(resume_from_checkpoint=checkpoint)` to continue the pass. Dataset
        contents and tokenizer identity are not checked.
        """
        checkpoint = Path(checkpoint)
        metadata = json.loads((checkpoint / "flock.json").read_text())
        config = json.loads((checkpoint / "config.json").read_text())

        if metadata["architecture"] != import_path(type(self.model)) or config != json.loads(
            json.dumps(asdict(self.model.config))
        ):
            raise ValueError("Checkpoint model architecture or config does not match the trainer")

        state = {
            path.stem: read_training_state(path)
            for path in (checkpoint / "training_state").glob("*.safetensors")
        }
        if not state:
            raise ValueError(f"Checkpoint has no training state: {checkpoint}")

        checkpoint_state = self._validate_trainer_state(state)

        missing = set(self.model.state_dict())
        unexpected = set()
        for path in weight_files(checkpoint):
            # Each shard contains only part of the model; check strictness over all shards.
            shard_missing, shard_unexpected = load_model(self.model, path, strict=False)
            missing.intersection_update(shard_missing)
            unexpected.update(shard_unexpected)
        if missing or unexpected:
            raise RuntimeError(
                f"Checkpoint weights do not match the model: "
                f"missing keys {sorted(missing)}, unexpected keys {sorted(unexpected)}"
            )

        self.load_trainer_state(checkpoint_state)

    @property
    def data_config(self) -> dict[str, Any]:
        """Return dataset size, batch size, seed and input column used for resume checks."""
        return {
            "dataset_size": len(self.dataset),
            "batch_size": self.batch_size,
            "seed": self.seed,
            "input_column": self.ds_input_column,
        }

    def get_trainer_state(self) -> dict[str, Any]:
        """Return optimizer, schedule, metric, RNG and data state for persistence.

        Model weights are saved separately. State tensors can share storage
        with live objects; serialize immediately rather than keeping this as an
        immutable in-memory snapshot.
        """
        return {
            "trainer_state_dict": self.state.state_dict(),
            "optimizer_state_dict": self.optimizer.state_dict(),
            "lr_scheduler_state_dict": self.lr_scheduler.state_dict(),
            "cpu_rng_state": torch.get_rng_state(),
            "cuda_rng_state": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
            "python_rng_state": random.getstate(),
            "data_config": self.data_config,
        }

    def _validate_trainer_state(
        self, state: dict[str, Any] | TrainerCheckpoint
    ) -> TrainerCheckpoint:
        """Reject incompatible resume settings before loading any checkpoint state."""
        checkpoint = TrainerCheckpoint.model_validate(
            state,
            context={"data_config": self.data_config, "total_steps": self.total_steps},
        )
        self.lr_scheduler.validate_state_dict(checkpoint.lr_scheduler_state_dict.model_dump())

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

    def load_trainer_state(self, state: dict[str, Any] | TrainerCheckpoint) -> None:
        """Validate and restore training state without loading model weights.

        Args:
            state (`dict[str, Any]` or `TrainerCheckpoint`): State from
                `get_trainer_state` or a deserialized checkpoint.

        Raises:
            `ValueError`: If progress, data, optimizer or scheduler settings differ.

        Restores Python, CPU and saved CUDA RNG states. Logs a warning when CUDA
        is available but the checkpoint has no CUDA RNG state.
        """
        checkpoint = self._validate_trainer_state(state)

        self.state.load_state_dict(checkpoint.trainer_state_dict.model_dump())
        self.optimizer.load_state_dict(checkpoint.optimizer_state_dict)
        self.lr_scheduler.load_state_dict(checkpoint.lr_scheduler_state_dict.model_dump())

        torch.set_rng_state(checkpoint.cpu_rng_state)
        random.setstate(checkpoint.python_rng_state)
        if torch.cuda.is_available():
            if checkpoint.cuda_rng_state is not None:
                torch.cuda.set_rng_state_all(checkpoint.cuda_rng_state)
            else:
                self.logger.warning(
                    "CUDA is available but no CUDA RNG state was found in the loaded trainer state."
                )

    def _checkpoints(self) -> list[Path]:
        """List numbered checkpoint folders in numeric step order, ignoring staging paths."""
        return sorted(
            (
                path
                for path in self.output_dir.glob("checkpoint-*")
                if path.is_dir() and path.name.removeprefix("checkpoint-").isdigit()
            ),
            key=lambda path: int(path.name.removeprefix("checkpoint-")),
        )
