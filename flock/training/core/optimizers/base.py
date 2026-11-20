from collections.abc import Callable
from typing import Any, Protocol

from torch.utils.hooks import RemovableHandle


class BaseOptimizer(Protocol):
    """Describe the PyTorch optimizer interface used by training.

    Native PyTorch optimizers satisfy this protocol without a wrapper or explicit
    inheritance. The trainer clears gradients before backpropagation and steps
    after gradient clipping. The scheduler reads and updates `param_groups`, and
    checkpoints save and restore optimizer state through the state-dict methods.
    Compiled training also registers state-dict hooks that save float learning
    rates and restore its form after loading.
    """

    param_groups: list[dict[str, Any]]

    def register_state_dict_post_hook(
        self,
        hook: Callable[[Any, dict[str, Any]], dict[str, Any] | None],
        prepend: bool = False,
    ) -> RemovableHandle:
        """Run `hook` on the result of every `state_dict()` call."""
        ...

    def register_load_state_dict_post_hook(
        self, hook: Callable[[Any], None], prepend: bool = False
    ) -> RemovableHandle:
        """Run `hook` after every `load_state_dict()` call."""
        ...

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
