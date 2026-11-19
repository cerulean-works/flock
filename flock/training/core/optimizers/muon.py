from dataclasses import dataclass

from torch import nn

from .base import BaseOptimizer


@dataclass(frozen=True, kw_only=True)
class MuonConfig:
    """Describe Muon settings reserved for a future trainer implementation.

    Args:
        lr (`float`): Base learning rate.
        momentum (`float`): Momentum coefficient.

    Constructing a `Trainer` with this configuration raises `NotImplementedError`.
    Muon alone cannot update embeddings or normalization weights; use
    `MuonAdamWConfig` to train with Muon.
    """

    lr: float
    momentum: float


def muon(config: MuonConfig, model: nn.Module) -> BaseOptimizer:
    """Reject Muon construction until its training implementation is available."""
    raise NotImplementedError("Muon support is not added yet")
