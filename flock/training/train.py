import math
from dataclasses import asdict
from typing import overload

import torch
from torch.nn import Module
from torch.optim import AdamW, Muon, Optimizer
from torch.utils.data import DataLoader

from flock.training import Dataset
from flock.training.lr_scheduler import LRSchedulerConfig, StepLRScheduler
from flock.training.optimization import AdamWConfig, MuonConfig, OptimizerConfig


class Trainer:
    def __init__(
        self,
        model: Module,
        dataset: Dataset,
        batch_size: int,
        optimizer_config: OptimizerConfig,
        lr_scheduler_config: LRSchedulerConfig,
        logging_steps: int = 10,
    ):
        self.model = model
        self.dataset = dataset
        self.batch_size = batch_size
        self.logging_steps = logging_steps

        self.total_steps = math.ceil(len(self.dataset) / self.batch_size)
        self.optimizer = self._get_optimizer(optimizer_config)
        self.lr_scheduler = self._get_lr_scheduler(lr_scheduler_config, self.total_steps)

    def _get_training_dataloader(self) -> DataLoader:
        return DataLoader(self.dataset, batch_size=self.batch_size)

    @overload
    def _get_optimizer(self: Trainer, config: AdamWConfig) -> AdamW: ...

    @overload
    def _get_optimizer(self: Trainer, config: MuonConfig) -> Muon: ...

    def _get_optimizer(self, config: OptimizerConfig) -> Optimizer:
        if isinstance(config, AdamWConfig):
            return AdamW(self.model.parameters(), **asdict(config))
        if isinstance(config, MuonConfig):
            return Muon(self.model.parameters(), **asdict(config))
        raise TypeError(
            f"Unsupported OptimizerConfig type {type(OptimizerConfig)}, supported types are: {OptimizerConfig.evaluate_value()}"
        )

    def _get_lr_scheduler(self, config: LRSchedulerConfig, total_steps: int) -> StepLRScheduler:
        return StepLRScheduler(self.optimizer, config, total_steps)

    def _calc_batch_loss(
        self, output: torch.Tensor, ground_truth: torch.Tensor
    ) -> tuple[torch.Tensor, int]:
        raise NotImplementedError

    def train(self):
        total_loss = 0
        total_tokens = 0
        log_period_losses: list[float] = []

        dataloader = self._get_training_dataloader()
        self.model.train()

        for idx, batch in enumerate(dataloader):
            self.optimizer.zero_grad()

            output: torch.Tensor = self.model(batch)
            loss, num_tokens = self._calc_batch_loss(output, batch["labels"])
            loss.backward()
            self.optimizer.step()
            self.lr_scheduler.step()

            total_loss += loss.item() * num_tokens
            total_tokens += num_tokens
            log_period_losses.append(total_loss)

            if idx % self.logging_steps == 0:
                print({"loss": sum(log_period_losses) / self.logging_steps})
                log_period_losses.clear()
