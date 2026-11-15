from collections.abc import Iterable
from dataclasses import dataclass

from torch import Tensor

from .base import BaseOptimizer


@dataclass(frozen=True, kw_only=True)
class MuonConfig:
    """Describe Muon settings reserved for a future trainer implementation.

    Args:
        lr (`float`): Base learning rate.
        momentum (`float`): Momentum coefficient.

    Constructing a `Trainer` with this configuration raises `NotImplementedError`.
    """

    lr: float
    momentum: float


def muon(config: MuonConfig, params: Iterable[Tensor]) -> BaseOptimizer:
    """Reject Muon construction until its training implementation is available."""
    raise NotImplementedError("Muon support is not added yet")
