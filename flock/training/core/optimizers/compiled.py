from typing import Any

import torch

from .base import BaseOptimizer


def prepare_for_compile(optimizer: BaseOptimizer, device: torch.device) -> None:
    """Prepare an optimizer for a compiled step.

    Learning rates become tensors on `device`, so schedule changes do not recompile,
    and CUDA enables `capturable`. Checkpoints keep float learning rates; loading one
    restores this form.

    Args:
        optimizer (`BaseOptimizer`): Optimizer to prepare once, before training.
        device (`torch.device`): Device that holds the model parameters.
    """
    _use_compiled_form(optimizer, device)
    optimizer.register_state_dict_post_hook(_save_float_learning_rates)
    optimizer.register_load_state_dict_post_hook(lambda loaded: _use_compiled_form(loaded, device))


def _use_compiled_form(optimizer: BaseOptimizer, device: torch.device) -> None:
    """Hold tensor learning rates and, on CUDA, enable `capturable`."""
    for group in optimizer.param_groups:
        group["lr"] = torch.tensor(float(group["lr"]), device=device)
        if "capturable" in group:
            group["capturable"] = device.type == "cuda"


def _save_float_learning_rates(
    _optimizer: BaseOptimizer, state_dict: dict[str, Any]
) -> dict[str, Any]:
    """Return `state_dict` with float learning rates, as eager optimizers keep them."""
    for group in state_dict["param_groups"]:
        group["lr"] = float(group["lr"])
    return state_dict
