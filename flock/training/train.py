import math
import random
from dataclasses import asdict
from typing import cast, overload

import torch
import torch.nn.functional as F
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
    ) -> tuple[torch.Tensor, int]:
        # output is [B, T, V], labels is [B, T]
        num_tokens: int = int((labels != -100).sum().item())
        if num_tokens == 0:
            raise ValueError("Batch contains no valid next-token targets")

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

        total_loss = 0
        total_tokens = 0
        log_period_losses: list[float] = []

        dataloader = self._get_training_dataloader()
        self.model.train()

        for idx, batch in enumerate(dataloader):
            self.optimizer.zero_grad()

            batch = cast(Batch, batch)
            batch = {k: v.to(self.device) for k, v in batch.items()}

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
