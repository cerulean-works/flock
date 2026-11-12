"""Define immutable optimizer settings accepted by the training configuration."""

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class AdamWConfig:
    """Configure PyTorch AdamW for all model parameters in one group.

    Args:
        lr (`float`): Base learning rate before the scheduler multiplier.
        betas (`tuple[float, float]`): First- and second-moment decay coefficients.

    Other arguments use PyTorch's defaults, including weight decay. PyTorch
    validates these values when the trainer constructs the optimizer.
    """

    lr: float
    betas: tuple[float, float]


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


type OptimizerConfig = AdamWConfig | MuonConfig
