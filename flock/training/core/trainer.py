import json
import logging
import math
import random
import shutil
import tempfile
from dataclasses import asdict, dataclass
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Literal, cast, overload

import torch
import torch.nn.functional as F
import wandb
from lance.torch.data import SafeLanceDataset
from safetensors.torch import load_model
from torch import nn
from torch.optim import AdamW, Muon, Optimizer
from torch.utils.data import DataLoader
from transformers import TokenizersBackend

from flock.hub.models import save_to_hub
from flock.hub.models.files import import_path, read_training_state, weight_files, write_model
from flock.training.core.checkpoint import TrainerCheckpoint
from flock.training.core.data_utils import Batch, LanceBatchCollator, ResumableBatchDataSampler
from flock.training.core.lr_scheduler import LRSchedulerConfig, StepLRScheduler
from flock.training.core.models import BaseModel
from flock.training.core.optimization import AdamWConfig, MuonConfig, OptimizerConfig


@dataclass(frozen=True, kw_only=True)
class TrainerConfig:
    run_name: str

    input_column: str
    batch_size: int

    optimizer_config: OptimizerConfig
    lr_scheduler_config: LRSchedulerConfig

    dataloader_prefetch_factor: int | None = 4
    dataloader_num_workers: int = 4

    wandb_project: str
    wandb_mode: Literal["online", "offline", "disabled", "shared"] = "online"

    output_dir: str | None = None
    save_steps: int | None = None
    save_total_limit: int | None = None
    save_to_hub_repo: str | None = None
    overwrite_output_dir: bool = False

    # Master weights are always stored in FP32, but the model can be trained in BF16.
    model_dtype: torch.dtype = torch.bfloat16
    max_norm: float = 1.0
    logging_steps: int = 10
    seed: int = 42


class TrainerState(nn.Module):
    loss_sum: torch.Tensor
    loss_max: torch.Tensor
    grad_norm_sum: torch.Tensor
    grad_norm_max: torch.Tensor

    step: torch.Tensor
    total_tokens: torch.Tensor
    window_tokens: torch.Tensor
    window_steps: torch.Tensor

    def __init__(self):
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
    ):
        self.loss_sum.add_(loss.detach() * tokens)
        self.loss_max = torch.maximum(loss.detach(), self.loss_max)

        self.grad_norm_sum.add_(grad_norm.detach())
        self.grad_norm_max = torch.maximum(grad_norm.detach(), self.grad_norm_max)

        self.step.add_(add_step)
        self.total_tokens.add_(tokens)
        self.window_tokens.add_(tokens)
        self.window_steps.add_(1)

    def get_logging_metrics(self) -> dict[str, int | float]:
        step, mean_loss, max_loss, mean_grad, max_grad = torch.stack(
            (
                self.step,
                self.loss_sum / self.window_tokens.clamp_min(1),
                self.loss_max,
                self.grad_norm_sum / self.window_steps,
                self.grad_norm_max,
            )
        ).tolist()

        return {
            "global_step": step,
            "train/loss_mean": mean_loss,
            "train/loss_window_max": max_loss,
            "train/grad_norm_mean": mean_grad,
            "train/grad_norm_window_max": max_grad,
        }

    @torch.no_grad()
    def reset_window(self):
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
    def reset_global(self):
        self.reset_window()
        self.step.zero_()
        self.total_tokens.zero_()


class Trainer:
    def __init__(
        self,
        *,
        config: TrainerConfig,
        model: BaseModel,
        tokenizer: TokenizersBackend,
        dataset: SafeLanceDataset,
    ):
        self.run_name = config.run_name
        self.seed = config.seed
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

        self.state = TrainerState().to(self.device)

        self.logger = logging.getLogger(__name__)
        self.run = wandb.init(
            project=config.wandb_project, mode=config.wandb_mode, config=asdict(config)
        )
        self.run.define_metric("global_step")
        self.run.define_metric("train/*", step_metric="global_step")

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
        self.optimizer = self._get_optimizer(config.optimizer_config)
        self.lr_scheduler = self._get_lr_scheduler(config.lr_scheduler_config, self.total_steps)

        self.output_dir: Path = (
            Path(config.output_dir)
            if config.output_dir is not None
            else Path.cwd().joinpath(".training-checkpoints").joinpath(self.run_name)
        )
        self.save_steps = config.save_steps
        self.save_total_limit = config.save_total_limit
        self.save_to_hub_repo = config.save_to_hub_repo
        self.overwrite_output_dir = config.overwrite_output_dir

        if self.save_steps is not None and self.save_steps <= 0:
            raise ValueError("save_steps must be a positive integer or None")
        if self.save_total_limit is not None and self.save_total_limit <= 0:
            raise ValueError("save_total_limit must be a positive integer or None")

        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _get_training_dataloader(self, skip_batches: int = 0) -> DataLoader:
        if self.dataloader_num_workers < 0:
            raise ValueError("dataloader_num_workers must be non-negative")
        if self.dataloader_num_workers == 0 and self.dataloader_prefetch_factor is not None:
            raise ValueError(
                "dataloader_prefetch_factor must be None when dataloader_num_workers is 0"
            )
        elif self.dataloader_prefetch_factor is not None and self.dataloader_prefetch_factor < 1:
            raise ValueError("dataloader_prefetch_factor must be >= 1")

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
        if isinstance(config, AdamWConfig):
            return AdamW(self.model.parameters(), **asdict(config))
        if isinstance(config, MuonConfig):
            raise NotImplementedError("Muon supported is not added yet")
            return Muon(self.model.parameters(), **asdict(config))
        raise TypeError(
            f"Unsupported OptimizerConfig type {type(OptimizerConfig)}, supported types are: {OptimizerConfig.evaluate_value()}"
        )

    def _get_lr_scheduler(self, config: LRSchedulerConfig, total_steps: int) -> StepLRScheduler:
        return StepLRScheduler(self.optimizer, config, total_steps)

    def _calc_batch_loss(
        self, output: torch.Tensor, labels: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor]:
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

    def _set_seed(self):
        random.seed(self.seed)
        torch.manual_seed(self.seed)

    def train(self, resume_from_checkpoint: bool | str | Path = False):
        """Train one pass, optionally restoring the latest or a specified local checkpoint.

        Args:
            resume_from_checkpoint (`bool`, `str` or `Path`, *optional*, defaults to `False`):
                With `True`, use the highest numbered checkpoint in `output_dir`.
                A path selects a checkpoint folder explicitly.
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

        self.model.train()

        self.logger.info("Starting training")
        for step, batch in enumerate(dataloader, start=start_step + 1):
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

            if self.save_steps is None:
                continue

            if step % self.save_steps == 0 or step == self.total_steps:
                self.save_model()

    def save_model(self):
        step = int(self.state.step.item())

        self.logger.info(f"Saving model at step {step}")
        save_path = self.output_dir.joinpath(f"checkpoint-{step}")
        if save_path.exists() and not self.overwrite_output_dir:
            raise FileExistsError(f"Checkpoint already exists: {save_path}")
        # Publish the directory only after weights and training state are fully written.
        with tempfile.TemporaryDirectory(prefix=".checkpoint-", dir=self.output_dir) as temporary:
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
                # Make non-blocking
                save_to_hub(
                    save_path,
                    self.save_to_hub_repo,
                    run=self.run,
                    step=step,
                    tags=[f"checkpoint-{step}"],
                )
            except Exception:
                self.logger.exception(
                    f"Failed to save checkpoint {save_path} to hub {self.save_to_hub_repo}"
                )
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

            while len(checkpoints) > self.save_total_limit:
                checkpoint_to_delete = checkpoints.pop(0)
                self.logger.info(f"Deleting old checkpoint {checkpoint_to_delete}")
                shutil.rmtree(checkpoint_to_delete)

    def load_checkpoint(self, checkpoint: str | Path):
        """Restore model weights and training state from a local checkpoint folder."""

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

        checkpoint_state = TrainerCheckpoint.model_validate(
            state,
            context={"data_config": self.data_config, "total_steps": self.total_steps},
        )

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
        return {
            "dataset_size": len(self.dataset),
            "batch_size": self.batch_size,
            "seed": self.seed,
            "input_column": self.ds_input_column,
        }

    def get_trainer_state(self) -> dict[str, Any]:
        return {
            "trainer_state_dict": self.state.state_dict(),
            "optimizer_state_dict": self.optimizer.state_dict(),
            "lr_scheduler_state_dict": self.lr_scheduler.state_dict(),
            "cpu_rng_state": torch.get_rng_state(),
            "cuda_rng_state": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
            "python_rng_state": random.getstate(),
            "data_config": self.data_config,
        }

    def load_trainer_state(self, state: dict[str, Any] | TrainerCheckpoint):
        checkpoint = TrainerCheckpoint.model_validate(
            state,
            context={"data_config": self.data_config, "total_steps": self.total_steps},
        )

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
        return sorted(
            (
                path
                for path in self.output_dir.glob("checkpoint-*")
                if path.is_dir() and path.name.removeprefix("checkpoint-").isdigit()
            ),
            key=lambda path: int(path.name.removeprefix("checkpoint-")),
        )
