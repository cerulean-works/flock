from dataclasses import dataclass
from typing import Any

from torch import nn
from torch.optim import AdamW
from torch.optim.optimizer import ParamsT

from .groups import EMBEDDINGS_GROUP, split_embedding_parameters


@dataclass(frozen=True, kw_only=True)
class AdamWConfig:
    """Configure PyTorch AdamW, with decoupled weight decay except on embeddings.

    Defaults follow OLMo 2 (arXiv 2501.00656, sec. 3.2) and OLMo-core: weight decay
    0.1 on every parameter, normalization weights included, except the embeddings.

    Args:
        lr (`float`): Base learning rate before the scheduler multiplier.
        betas (`tuple[float, float]`): First- and second-moment decay coefficients.
        eps (`float`, *optional*, defaults to `1e-8`): Denominator term for stability.
        weight_decay (`float`, *optional*, defaults to `0.1`): Decoupled weight decay.
        no_decay_embeddings (`bool`, *optional*, defaults to `True`): Put the weights of
            `nn.Embedding` modules, and any output projection tied to them, in a separate
            group without weight decay. Applies when the factory receives the model.

    PyTorch validates these values when the trainer constructs the optimizer.
    """

    lr: float
    betas: tuple[float, float]
    eps: float = 1e-8
    weight_decay: float = 0.1
    no_decay_embeddings: bool = True


def adamw(config: AdamWConfig, params: nn.Module | ParamsT) -> AdamW:
    """Construct PyTorch AdamW with the configured settings.

    Args:
        config (`AdamWConfig`): Optimizer settings.
        params (`torch.nn.Module` or parameters): The model, or parameters or groups as
            `torch.optim.AdamW` accepts them. A model gets an `"adamw"` group and, with
            `no_decay_embeddings`, an `"adamw_embeddings"` group without weight decay.
            Groups that name their own settings keep them.

    Returns:
        `torch.optim.AdamW`: The optimizer.
    """
    if isinstance(params, nn.Module):
        embeddings, others = (
            split_embedding_parameters(params)
            if config.no_decay_embeddings
            else ([], [p for p in params.parameters() if p.requires_grad])
        )
        groups: list[dict[str, Any]] = [{"params": others, "name": "adamw"}]
        if embeddings:
            groups.append(
                {"params": embeddings, "weight_decay": 0.0, "name": f"adamw_{EMBEDDINGS_GROUP}"}
            )
        params = [group for group in groups if group["params"]]
    return AdamW(
        params,
        lr=config.lr,
        betas=config.betas,
        eps=config.eps,
        weight_decay=config.weight_decay,
    )
