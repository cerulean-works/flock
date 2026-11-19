from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from torch import nn
from torch.optim import AdamW, Muon


@dataclass(frozen=True, kw_only=True)
class MuonAdamWConfig:
    """Configure Muon for hidden weight matrices and AdamW for every other parameter.

    Muon updates the weights of `nn.Linear` layers inside the network. AdamW
    updates embeddings, the output projection, normalization weights and
    biases. A linear layer counts as the output projection when its weight has
    the same shape as an embedding table, as `lm_head` does.

    Both optimizers use PyTorch's implementations. The learning-rate schedule
    scales both base rates by the same ratio.

    Args:
        lr (`float`): AdamW base learning rate.
        betas (`tuple[float, float]`): AdamW first- and second-moment decay coefficients.
        weight_decay (`float`, *optional*, defaults to `0.0`): AdamW decoupled weight decay.
        eps (`float`, *optional*, defaults to `1e-8`): AdamW denominator epsilon.
        muon_lr (`float`): Muon base learning rate.
        muon_momentum (`float`, *optional*, defaults to `0.95`): Muon momentum coefficient.
        muon_weight_decay (`float`, *optional*, defaults to `0.1`): Muon decoupled weight decay.
        muon_nesterov (`bool`, *optional*, defaults to `True`): Use Nesterov momentum.
        muon_ns_steps (`int`, *optional*, defaults to `5`): Newton-Schulz iterations.
        muon_adjust_lr_fn (`str`, *optional*): How Muon scales its rate per matrix shape.
            `None` or `"original"` scales by `sqrt(max(1, rows / columns))`.
            `"match_rms_adamw"` scales by `0.2 * sqrt(max(rows, columns))`, so Muon
            updates have AdamW's typical size and one rate can suit both.
    """

    lr: float
    betas: tuple[float, float]
    weight_decay: float = 0.0
    eps: float = 1e-8
    muon_lr: float
    muon_momentum: float = 0.95
    muon_weight_decay: float = 0.1
    muon_nesterov: bool = True
    muon_ns_steps: int = 5
    muon_adjust_lr_fn: Literal["original", "match_rms_adamw"] | None = None


class MuonAdamW:
    """Step a Muon and an AdamW optimizer together behind one optimizer interface.

    `param_groups` lists Muon's groups first, then AdamW's, as the same dictionaries,
    so scheduler changes reach both optimizers. `state_dict()` keeps that group order
    and stores each optimizer's parameter state under `"muon"` and `"adamw"`.

    Args:
        muon (`torch.optim.Muon`): Optimizer for hidden weight matrices.
        adamw (`torch.optim.AdamW`): Optimizer for all other parameters.
    """

    param_groups: list[dict[str, Any]]
    """Muon's parameter groups followed by AdamW's."""

    def __init__(self, muon: Muon, adamw: AdamW) -> None:
        """Hold both optimizers and list their groups."""
        self.muon = muon
        self.adamw = adamw
        self.param_groups = [*muon.param_groups, *adamw.param_groups]

    def zero_grad(self, set_to_none: bool = True) -> None:
        """Clear gradients in both optimizers."""
        self.muon.zero_grad(set_to_none=set_to_none)
        self.adamw.zero_grad(set_to_none=set_to_none)

    def step(self, closure: Callable[[], float] | None = None) -> float | None:
        """Evaluate `closure` once if given, then update every parameter."""
        loss = closure() if closure is not None else None
        self.muon.step()
        self.adamw.step()
        return loss

    def state_dict(self) -> dict[str, Any]:
        """Return both optimizers' state with their parameter groups in one list."""
        muon, adamw = self.muon.state_dict(), self.adamw.state_dict()
        return {
            "state": {"muon": muon["state"], "adamw": adamw["state"]},
            "param_groups": [*muon["param_groups"], *adamw["param_groups"]],
        }

    def load_state_dict(self, state_dict: dict[str, Any]) -> None:
        """Restore both optimizers from a `state_dict()` result."""
        split = len(self.muon.param_groups)
        groups = state_dict["param_groups"]
        if len(groups) != split + len(self.adamw.param_groups):
            raise ValueError("MuonAdamW state has a different number of parameter groups")
        self.muon.load_state_dict(
            {"state": state_dict["state"]["muon"], "param_groups": groups[:split]}
        )
        self.adamw.load_state_dict(
            {"state": state_dict["state"]["adamw"], "param_groups": groups[split:]}
        )
        # Loading replaces each optimizer's group dictionaries.
        self.param_groups = [*self.muon.param_groups, *self.adamw.param_groups]


def split_muon_parameters(model: nn.Module) -> tuple[list[nn.Parameter], list[nn.Parameter]]:
    """Return the hidden weight matrices for Muon and all other trainable parameters.

    Shared parameters appear once, in model order.
    """
    embedding_shapes = {
        module.weight.shape for module in model.modules() if isinstance(module, nn.Embedding)
    }
    hidden = {
        id(module.weight)
        for module in model.modules()
        if isinstance(module, nn.Linear) and module.weight.shape not in embedding_shapes
    }
    muon: list[nn.Parameter] = []
    adamw: list[nn.Parameter] = []
    for parameter in model.parameters():
        if parameter.requires_grad:
            (muon if id(parameter) in hidden else adamw).append(parameter)
    return muon, adamw


def muon_adamw(config: MuonAdamWConfig, model: nn.Module) -> MuonAdamW:
    """Construct Muon for hidden weight matrices and AdamW for the rest.

    Raises:
        `ValueError`: If the model has no hidden matrices or no other parameters.
    """
    muon_params, adamw_params = split_muon_parameters(model)
    if not muon_params or not adamw_params:
        raise ValueError("MuonAdamW needs both hidden weight matrices and other parameters")
    return MuonAdamW(
        Muon(
            muon_params,
            lr=config.muon_lr,
            weight_decay=config.muon_weight_decay,
            momentum=config.muon_momentum,
            nesterov=config.muon_nesterov,
            ns_steps=config.muon_ns_steps,
            adjust_lr_fn=config.muon_adjust_lr_fn,
        ),
        AdamW(
            adamw_params,
            lr=config.lr,
            betas=config.betas,
            weight_decay=config.weight_decay,
            eps=config.eps,
        ),
    )
