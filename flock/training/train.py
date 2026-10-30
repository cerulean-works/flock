import math
import random
from dataclasses import asdict
from typing import cast, overload

import torch
import torch.nn.functional as F
import wandb
from lance.torch.data import SafeLanceDataset
from torch.nn import Module
from torch.optim import AdamW, Muon, Optimizer
from torch.utils.data import DataLoader
from transformers import TokenizersBackend

from flock.training.data_utils import Batch, LanceBatchCollator
from flock.training.lr_scheduler import LRSchedulerConfig, StepLRScheduler
from flock.training.optimization import AdamWConfig, MuonConfig, OptimizerConfig


class Trainer:
    def __init__(
        self,
        model: Module,
        tokenizer: TokenizersBackend,
        dataset: SafeLanceDataset,
        input_column: str,
        batch_size: int,
        optimizer_config: OptimizerConfig,
        lr_scheduler_config: LRSchedulerConfig,
        max_norm: float = 1.0,
        logging_steps: int = 10,
        seed=42,
    ):
        self.seed = seed
        self.device = (
            torch.device("cuda:0")
            if torch.cuda.is_available()
            else torch.device("mps:0")
            if torch.mps.is_available()
            else torch.device("cpu")
        )

        self.model = model.to(self.device)
        self.tokenizer = tokenizer
        self.dataset = dataset
        self.ds_input_column = input_column
        self.batch_size = batch_size

        self.max_norm = max_norm
        self.logging_steps = logging_steps

        self.total_steps = math.ceil(len(self.dataset) / self.batch_size)
        self.optimizer = self._get_optimizer(optimizer_config)
        self.lr_scheduler = self._get_lr_scheduler(lr_scheduler_config, self.total_steps)

    def _get_training_dataloader(self) -> DataLoader:
        collator = LanceBatchCollator(self.tokenizer, self.ds_input_column)

        return DataLoader(
            self.dataset,
            batch_size=self.batch_size,
            shuffle=True,
            num_workers=4,
            collate_fn=collator.collate_fn,
            # lance multi-threads internally, so its best we not use fork
            multiprocessing_context="spawn",
            # accelerate batch host to device transfers
            pin_memory=True,
            prefetch_factor=8,
            persistent_workers=True,
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

        loss = F.cross_entropy(
            output.reshape(-1, output.shape[-1]),
            labels.reshape(-1),
            ignore_index=-100,
            reduction="mean",
        )

        return loss, num_tokens

    def _set_seed(self):
        random.seed(self.seed)
        torch.manual_seed(self.seed)

    def train(self):
        # Set random state at beginning of training to ensure reproducibility
        self._set_seed()

        loss_sum = torch.zeros((), dtype=torch.float, device=self.device)
        loss_max = torch.zeros((), dtype=torch.float, device=self.device)
        grad_norm_sum = torch.zeros((), dtype=torch.float, device=self.device)
        grad_norm_max = torch.zeros((), dtype=torch.float, device=self.device)

        window_tokens = torch.zeros((), dtype=torch.int64, device=self.device)
        window_steps = torch.zeros((), dtype=torch.int64, device=self.device)

        dataloader = self._get_training_dataloader()
        self.model.train()
        total_tokens = torch.zeros((), dtype=torch.int64, device=self.device)

        for idx, batch in enumerate(dataloader):
            self.optimizer.zero_grad()

            batch = cast(Batch, batch)
            batch = {k: v.to(self.device, non_blocking=True) for k, v in batch.items()}

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

            if (idx + 1) % self.logging_steps == 0:
                mean_loss, max_loss, mean_grad, max_grad = torch.stack(
                    (
                        loss_sum / window_tokens,
                        loss_max,
                        grad_norm_sum / window_steps,
                        grad_norm_max,
                    )
                ).tolist()

                wandb.log(
                    {
                        "train/step": idx + 1,
                        "train/loss_mean": mean_loss,
                        "train/loss_window_max": max_loss,
                        "train/grad_norm_mean": mean_grad,
                        "train/grad_norm_window_max": max_grad,
                    }
                )

                loss_sum.zero_()
                loss_max.zero_()
                grad_norm_sum.zero_()
                grad_norm_max.zero_()
                window_tokens.zero_()
                window_steps.zero_()

        if self.total_steps % self.logging_steps:
            mean_loss, max_loss, mean_grad, max_grad = torch.stack(
                (
                    loss_sum / window_tokens,
                    loss_max,
                    grad_norm_sum / window_steps,
                    grad_norm_max,
                )
            ).tolist()

            wandb.log(
                {
                    "train/step": idx + 1,
                    "train/loss_mean": mean_loss,
                    "train/loss_window_max": max_loss,
                    "train/grad_norm_mean": mean_grad,
                    "train/grad_norm_window_max": max_grad,
                }
            )
