from dataclasses import asdict, dataclass

from torch import nn
from torch.optim import AdamW


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


def adamw(config: AdamWConfig, model: nn.Module) -> AdamW:
    """Construct PyTorch AdamW over all model parameters in one group."""
    return AdamW(model.parameters(), **asdict(config))
