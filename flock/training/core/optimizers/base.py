from collections import defaultdict
from collections.abc import Callable
from typing import Any, Protocol

import torch


class BaseOptimizer(Protocol):
    """Describe the PyTorch optimizer interface used by training.

    Native PyTorch optimizers satisfy this protocol without a wrapper or explicit
    inheritance. The trainer clears gradients before backpropagation and steps
    after gradient clipping. The scheduler reads and updates `param_groups`, and
    checkpoints save and restore optimizer state through the state-dict methods.
    Compiled training also reads `defaults` and moves step counts in `state`.
    """

    param_groups: list[dict[str, Any]]
    defaults: dict[str, Any]
    state: defaultdict[torch.Tensor, Any]

    def zero_grad(self, set_to_none: bool = True) -> None:
        """Clear parameter gradients before backpropagation."""
        ...

    def step(self, closure: Callable[[], float] | None = None) -> float | None:
        """Update parameters using their gradients, optionally recomputing loss."""
        ...

    def state_dict(self) -> dict[str, Any]:
        """Return parameter groups and optimizer state for checkpointing."""
        ...

    def load_state_dict(self, state_dict: dict[str, Any]) -> None:
        """Restore parameter groups and optimizer state from a checkpoint."""
        ...
