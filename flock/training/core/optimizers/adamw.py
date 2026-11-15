from collections.abc import Iterable
from dataclasses import asdict, dataclass

from torch import Tensor
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


def adamw(config: AdamWConfig, params: Iterable[Tensor]) -> AdamW:
    """Construct PyTorch AdamW with one parameter group and configured settings."""
    return AdamW(params, **asdict(config))
