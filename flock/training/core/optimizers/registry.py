from typing import Any, ClassVar, Protocol

from torch import nn

from .adamw import AdamWConfig, adamw
from .base import BaseOptimizer
from .muon import MuonConfig, muon
from .muon_adamw import MuonAdamWConfig, muon_adamw

type OptimizerConfig = AdamWConfig | MuonConfig | MuonAdamWConfig


class OptimizerFactory[ConfigT: OptimizerConfig](Protocol):
    """Construct an optimizer from its config and a model already on its final device.

    Factories receive the model so they can group parameters by module or name.
    """

    def __call__(self, config: ConfigT, model: nn.Module) -> BaseOptimizer: ...


class OptimizerInterface:
    """Register optimizer factories by configuration type.

    Factories return the shared `BaseOptimizer` contract. Registering a factory
    replaces the implementation for that config type; existing optimizers keep
    their implementation. Trainer configs accept the types in `OptimizerConfig`.
    """

    _global_mapping: ClassVar[dict[type[OptimizerConfig], OptimizerFactory[Any]]] = {
        AdamWConfig: adamw,
        MuonConfig: muon,
        MuonAdamWConfig: muon_adamw,
    }

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
