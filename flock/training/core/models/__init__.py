from .base import BaseModel
from .hf import HFCompatibleConfig, HFCompatibleModel
from .llama import (
    LLAMA3_2_1B_CONFIG,
    LLAMA3_2_3B_CONFIG,
    Llama,
    Llama3RopeScaling,
    LlamaConfig,
)
from .olmo2 import OLMO2_1B_CONFIG, OLMO2_7B_CONFIG, Olmo2, Olmo2Config

__all__ = [
    "LLAMA3_2_1B_CONFIG",
    "LLAMA3_2_3B_CONFIG",
    "OLMO2_1B_CONFIG",
    "OLMO2_7B_CONFIG",
    "BaseModel",
    "HFCompatibleConfig",
    "HFCompatibleModel",
    "Llama",
    "Llama3RopeScaling",
    "LlamaConfig",
    "Olmo2",
    "Olmo2Config",
    "registry",
]

registry: dict[str, type[BaseModel]] = {"olmo2": Olmo2, "llama": Llama, "hf": HFCompatibleModel}
