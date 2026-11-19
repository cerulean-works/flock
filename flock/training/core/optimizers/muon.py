from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from functools import partial
from typing import Any, overload

import torch
from torch import Tensor
from torch.optim import Muon, Optimizer

from .adamw import AdamWConfig

type _CompanionFactory = Callable[[AdamWConfig, Iterable[Tensor]], Optimizer]


@dataclass(frozen=True, kw_only=True)
class MuonConfig:
    """Configure Muon for matrices and a companion for the other parameters.

    Args:
        lr (`float`): Base learning rate.
        momentum (`float`): Momentum coefficient.
        weight_decay (`float`): Muon weight decay coefficient.
        companion_optimizer_config (`AdamWConfig`): Config for the registered companion factory.
        nesterov (`bool`): Enable Nesterov momentum in Muon.
    """

    lr: float
    momentum: float
    weight_decay: float
    companion_optimizer_config: AdamWConfig

    nesterov: bool = True
    ns_coefficients = (3.4445, -4.775, 2.0315)
    eps = 1e-7
    ns_steps = 5


class MuonWithCompanion(Optimizer):
    def __init__(
        self,
        params: Iterable[Tensor],
        muon_config: MuonConfig,
        *,
        make_companion: Callable[[Iterable[Tensor]], Optimizer],
    ) -> None:
        """Construct Muon and its companion from disjoint parameter sets.

        Args:
            params (`Iterable[Tensor]`): Model parameters to optimize.
            muon_config (`MuonConfig`): Configuration for the Muon parameter group.
            make_companion: Factory for the non-2D parameters.
        """

        matrices = []
        others = []

        for param in params:
            if not param.requires_grad:
                continue
            if param.ndim == 2:
                matrices.append(param)
            else:
                others.append(param)

        if not matrices or not others:
            raise ValueError(
                "MuonWithCompanion requires at least one 2D parameter and one non-2D parameter."
            )

        self.muon: Muon = Muon(
            matrices,
            **{k: v for k, v in asdict(muon_config).items() if k != "companion_optimizer_config"},
        )
        self.companion = make_companion(others)

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

    @torch.no_grad()
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

        self.muon.param_groups = [self.param_groups[0]]
        self.companion.param_groups = self.param_groups[1:]

        self.muon.state = self.state
        self.companion.state = self.state


def muon(
    config: MuonConfig,
    params: Iterable[Tensor],
    *,
    get_companion_factory: Callable[[type[AdamWConfig]], _CompanionFactory],
) -> Optimizer:
    """Build Muon with the optimizer selected by the companion config."""

    companion_config = config.companion_optimizer_config
    factory = get_companion_factory(type(companion_config))
    return MuonWithCompanion(params, config, make_companion=partial(factory, companion_config))
