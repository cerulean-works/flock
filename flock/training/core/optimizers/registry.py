from collections.abc import Iterable
from functools import partial
from typing import Any, ClassVar, Protocol

from torch import Tensor
from torch.optim import Optimizer

from .adamw import AdamWConfig, adamw
from .muon import MuonConfig, muon

type OptimizerConfig = AdamWConfig | MuonConfig


class OptimizerFactory[ConfigT: OptimizerConfig](Protocol):
    """Construct an optimizer from its config and parameters on their final device."""

    def __call__(self, config: ConfigT, params: Iterable[Tensor]) -> Optimizer: ...


class OptimizerInterface:
    """Register optimizer factories by configuration type.

    Factories return instances of `torch.optim.Optimizer` subclasses. Registrations
    apply to future optimizer construction. Trainer configs accept the types in
    `OptimizerConfig`.
    """

    _global_mapping: ClassVar[dict[type[OptimizerConfig], OptimizerFactory[Any]]] = {}

    @classmethod
    def register[ConfigT: OptimizerConfig](
        cls, config_type: type[ConfigT], factory: OptimizerFactory[ConfigT]
    ) -> None:
        """Register or replace the optimizer factory for a configuration type."""
        cls._global_mapping[config_type] = factory

    @classmethod
    def get_interface[ConfigT: OptimizerConfig](
        cls, config_type: type[ConfigT]
    ) -> OptimizerFactory[ConfigT]:
        """Resolve a configuration type to its optimizer factory."""
        try:
            return cls._global_mapping[config_type]
        except KeyError:
            available = ", ".join(sorted(config.__name__ for config in cls._global_mapping))
            raise TypeError(
                f"Unsupported OptimizerConfig type {config_type.__name__}; available: {available}"
            ) from None


OptimizerInterface.register(AdamWConfig, adamw)

OptimizerInterface.register(
    MuonConfig, partial(muon, get_companion_factory=OptimizerInterface.get_interface)
)
