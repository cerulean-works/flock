import logging
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, cast, overload

import torch
import torch.nn.functional as F
import wandb
from lance.torch.data import SafeLanceDataset
from torch.optim import AdamW, Muon, Optimizer
from torch.utils.data import DataLoader
from transformers import TokenizersBackend

from flock.hub.models import save_to_hub
from flock.hub.models.files import write_model
from flock.training.core.data_utils import Batch, LanceBatchCollator
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
        self.device = (
            torch.device("cuda:0")
            if torch.cuda.is_available()
            else torch.device("mps:0")
            if torch.mps.is_available()
            else torch.device("cpu")
        )

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
        if any(self.output_dir.iterdir()):
            if self.overwrite_output_dir:
                self.logger.warning(
                    f"Save directory {self.output_dir} is not empty. Overwriting existing files."
                )
            else:
                raise RuntimeError(
                    f"Save directory {self.output_dir} is not empty. Please specify an empty directory."
                )

    def _get_training_dataloader(self) -> DataLoader:
        collator = LanceBatchCollator(self.tokenizer, self.ds_input_column)

        if self.dataloader_num_workers < 0:
            raise ValueError("dataloader_num_workers must be non-negative")
        if self.dataloader_num_workers == 0 and self.dataloader_prefetch_factor is not None:
            raise ValueError(
                "dataloader_prefetch_factor must be None when dataloader_num_workers is 0"
            )
        elif self.dataloader_prefetch_factor is not None and self.dataloader_prefetch_factor < 1:
            raise ValueError("dataloader_prefetch_factor must be >= 1")

        return DataLoader(
            self.dataset,
            batch_size=self.batch_size,
            shuffle=True,
            num_workers=self.dataloader_num_workers,
            collate_fn=collator.collate_fn,
            # lance multi-threads internally, so its best we not use fork
            multiprocessing_context="spawn" if self.dataloader_num_workers > 0 else None,
            # accelerate batch host to device transfers
            pin_memory=True,
            prefetch_factor=self.dataloader_prefetch_factor,
            persistent_workers=self.dataloader_num_workers > 0,
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

    def train(self):
        self.logger.info("Setting up trainer...")

        # Set random state at beginning of training to ensure reproducibility
        self._set_seed()

        loss_sum = torch.zeros((), dtype=torch.float, device=self.device)
        loss_max = torch.zeros((), dtype=torch.float, device=self.device)
        grad_norm_sum = torch.zeros((), dtype=torch.float, device=self.device)
        grad_norm_max = torch.zeros((), dtype=torch.float, device=self.device)

        window_tokens = torch.zeros((), dtype=torch.int64, device=self.device)
        window_steps = torch.zeros((), dtype=torch.int64, device=self.device)

        self.logger.info("Initializing training dataloader")
        dataloader = self._get_training_dataloader()
        self.logger.info(f"Dataloader with {len(dataloader)} steps loaded")
        self.model.train()
        total_tokens = torch.zeros((), dtype=torch.int64, device=self.device)

        self.logger.info("Starting training")
        for idx, batch in enumerate(dataloader):
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

            loss_sum += loss.detach() * num_tokens
            loss_max = torch.maximum(loss_max, loss.detach())
            grad_norm_sum += grad_norm.detach()
            grad_norm_max = torch.maximum(grad_norm_max, grad_norm.detach())

            total_tokens += num_tokens
            window_tokens += num_tokens
            window_steps += 1

            if (idx + 1) % self.logging_steps == 0 or (idx + 1) == self.total_steps:
                mean_loss, max_loss, mean_grad, max_grad = torch.stack(
                    (
                        loss_sum / window_tokens.clamp_min(1),
                        loss_max,
                        grad_norm_sum / window_steps,
                        grad_norm_max,
                    )
                ).tolist()

                metrics_dict = {
                    "global_step": idx + 1,
                    "train/loss_mean": mean_loss,
                    "train/loss_window_max": max_loss,
                    "train/grad_norm_mean": mean_grad,
                    "train/grad_norm_window_max": max_grad,
                }

                self.logger.info(metrics_dict)
                self.run.log(metrics_dict)

                loss_sum.zero_()
                loss_max.zero_()
                grad_norm_sum.zero_()
                grad_norm_max.zero_()
                window_tokens.zero_()
                window_steps.zero_()

            if self.save_steps is None:
                continue

            if (idx + 1) % self.save_steps == 0 or (idx + 1) == self.total_steps:
                self.logger.info(f"Saving model at step {idx + 1}")
                self.save_model(idx + 1)

    def save_model(self, step: int):
        self.logger.info(f"Saving model at step {step}")
        save_path = self.output_dir.joinpath(f"checkpoint-{step}")
        if self.save_to_hub_repo is not None:
            save_to_hub(self.model, self.save_to_hub_repo, run=self.run, step=step)
        else:
            write_model(
                self.model,
                save_path,
                shard_size=5 * 2**30,
                run=f"{self.run.entity}/{self.run.project}/{self.run.id}",
                step=step,
                training_state=None,
            )
        if self.save_total_limit is not None:
            checkpoints = sorted(
                self.output_dir.glob("checkpoint-*"), key=lambda p: p.stat().st_mtime
            )
            while len(checkpoints) > self.save_total_limit:
                checkpoint_to_delete = checkpoints.pop(0)
                self.logger.info(f"Deleting old checkpoint {checkpoint_to_delete}")
                for file in checkpoint_to_delete.glob("*"):
                    file.unlink()
                checkpoint_to_delete.rmdir()
