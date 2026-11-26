from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import partial
from typing import Any, Literal, cast, overload

import torch
from torch import Tensor, nn
from torch.optim import Muon, Optimizer
from torch.optim.optimizer import ParamsT

from .adamw import AdamWConfig
from .groups import EMBEDDINGS_GROUP, split_embedding_parameters

type _CompanionFactory[ConfigT] = Callable[[ConfigT, ParamsT], Optimizer]


@dataclass(frozen=True, kw_only=True)
class MuonConfig:
    """Configure Muon for hidden matrices and a companion for the other parameters.

    Args:
        lr (`float`): Base learning rate.
        momentum (`float`): Momentum coefficient.
        companion_optimizer_config (`AdamWConfig`): Config for the registered companion factory.
        weight_decay (`float`, *optional*, defaults to `0.1`): Muon's decoupled weight decay.
        nesterov (`bool`, *optional*, defaults to `True`): Enable Nesterov momentum in Muon.
        adjust_lr_fn (`str`, *optional*, defaults to `"original"`): How PyTorch's Muon
            scales the rate for each matrix's shape. `"original"` multiplies it by
            `sqrt(max(1, rows / cols))`. `"match_rms_adamw"` multiplies it by
            `0.2 * sqrt(max(rows, cols))`, so the update's RMS matches AdamW's and Muon can
            use an AdamW learning rate (Moonlight, arXiv 2502.16982). `"spectral_unclamped"`
            is PyTorch's third option.
    """

    lr: float
    momentum: float
    companion_optimizer_config: AdamWConfig

    weight_decay: float = 0.1
    nesterov: bool = True
    adjust_lr_fn: Literal["original", "match_rms_adamw", "spectral_unclamped"] = "original"
    ns_coefficients = (3.4445, -4.775, 2.0315)
    eps = 1e-7
    ns_steps = 5


class MuonWithCompanion(Optimizer):
    def __init__(
        self,
        params: nn.Module | ParamsT,
        muon_config: MuonConfig,
        *,
        make_companion: Callable[[ParamsT], Optimizer],
        no_decay_embeddings: bool = True,
    ) -> None:
        """Construct Muon and its companion from disjoint parameter sets.

        Given the model, embedding weights, including an output projection tied to
        them, go to the companion in a group of their own named `"<companion>_embeddings"`,
        without weight decay unless `no_decay_embeddings` is off. Muon updates the other
        trainable 2D parameters; the companion updates the rest. Given parameters
        instead, Muon updates every trainable 2D parameter.

        Args:
            params (`torch.nn.Module` or `Iterable[Tensor]`): The model, or bare parameters.
            muon_config (`MuonConfig`): Configuration for the Muon parameter group.
            make_companion: Factory for the companion's parameters or parameter groups.
            no_decay_embeddings (`bool`, *optional*, defaults to `True`): Exclude embedding
                weights from weight decay. Used only when `params` is a model.
        """

        if isinstance(params, nn.Module):
            embeddings, trainable = split_embedding_parameters(params)
        else:
            # Without the model, Muon accepts bare parameters only.
            tensors = cast(Iterable[Tensor], params)
            embeddings, trainable = [], [p for p in tensors if p.requires_grad]
        matrices: list[Tensor] = [p for p in trainable if p.ndim == 2]
        others: list[Tensor] = [p for p in trainable if p.ndim != 2]

        if not matrices or not (others or embeddings):
            raise ValueError(
                "MuonWithCompanion requires at least one 2D parameter for Muon and one "
                "parameter for the companion."
            )

        self.muon: Muon = Muon(
            matrices,
            lr=muon_config.lr,
            weight_decay=muon_config.weight_decay,
            momentum=muon_config.momentum,
            nesterov=muon_config.nesterov,
            ns_coefficients=muon_config.ns_coefficients,
            eps=muon_config.eps,
            ns_steps=muon_config.ns_steps,
            adjust_lr_fn=muon_config.adjust_lr_fn,
        )
        if embeddings:
            groups: list[dict[str, Any]] = [{"params": others}] if others else []
            embedding_group: dict[str, Any] = {"params": embeddings, "name": EMBEDDINGS_GROUP}
            if no_decay_embeddings:
                embedding_group["weight_decay"] = 0.0
            self.companion = make_companion(groups + [embedding_group])
        else:
            self.companion = make_companion(others)

        # Name each group after the optimizer that steps it, as one class name can't.
        for group in self.muon.param_groups:
            group["name"] = "muon"
        companion_name = type(self.companion).__name__.lower()
        for group in self.companion.param_groups:
            role = group.get("name")
            group["name"] = companion_name if role is None else f"{companion_name}_{role}"
        self._muon_group_count = len(self.muon.param_groups)
        groups = self.muon.param_groups + self.companion.param_groups

        super().__init__(groups, defaults={})

        if any(set(self.muon.state) & set(self.companion.state)):
            raise ValueError("Muon and companion optimizers must not share state keys.")

        self.state.update(self.muon.state)
        self.state.update(self.companion.state)

        self.muon.state = self.state
        self.companion.state = self.state

    @overload
    def step(self, closure: None = None) -> None: ...

    @overload
    def step(self, closure: Callable[[], float]) -> float: ...

    @overload
    def step(self, closure: Callable[[], float] | None = None) -> float | None: ...

    # Each inner step disables gradients itself. Calling them in the caller's grad mode
    # keeps their compiled frames to one grad mode, so a compiled update compiles once.
    def step(self, closure: Callable[[], float] | None = None) -> float | None:
        loss = None
        if closure is not None:
            with torch.enable_grad():
                loss = closure()

        self.muon.step()
        self.companion.step()

        return loss

    def load_state_dict(self, state_dict: dict[str, Any]) -> None:
        super().load_state_dict(state_dict)

        # Loading replaces the group dicts; hand each optimizer its own again.
        self.muon.param_groups = self.param_groups[: self._muon_group_count]
        self.companion.param_groups = self.param_groups[self._muon_group_count :]

        self.muon.state = self.state
        self.companion.state = self.state


def muon(
    config: MuonConfig,
    params: nn.Module | ParamsT,
    *,
    get_companion_factory: Callable[[type[AdamWConfig]], _CompanionFactory[AdamWConfig]],
) -> Optimizer:
    """Build Muon with the optimizer selected by the companion config."""

    companion_config = config.companion_optimizer_config
    factory = get_companion_factory(type(companion_config))
    return MuonWithCompanion(
        params,
        config,
        make_companion=partial(factory, companion_config),
        no_decay_embeddings=companion_config.no_decay_embeddings,
    )
