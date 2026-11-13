"""Expose causal model implementations, architecture presets and the model registry."""

from .base import BaseModel
from .hf import HFCompatibleConfig, HFCompatibleModel
from .olmo2 import OLMO2_1B_CONFIG, OLMO2_7B_CONFIG, Olmo2, Olmo2Config

__all__ = [
    "OLMO2_1B_CONFIG",
    "OLMO2_7B_CONFIG",
    "BaseModel",
    "HFCompatibleConfig",
    "HFCompatibleModel",
    "Olmo2",
    "Olmo2Config",
    "registry",
]

registry: dict[str, type[BaseModel]] = {"olmo2": Olmo2, "hf": HFCompatibleModel}
