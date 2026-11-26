from flock.training.core.models.base import BaseModel
from flock.training.core.models.hf import HFCompatibleConfig, HFCompatibleModel
from flock.training.core.models.llama32 import LLAMA32_1B_CONFIG, Llama32, Llama32Config
from flock.training.core.models.olmo2 import OLMO2_1B_CONFIG, OLMO2_7B_CONFIG, Olmo2, Olmo2Config

__all__ = [
    "LLAMA32_1B_CONFIG",
    "OLMO2_1B_CONFIG",
    "OLMO2_7B_CONFIG",
    "BaseModel",
    "HFCompatibleConfig",
    "HFCompatibleModel",
    "Llama32",
    "Llama32Config",
    "Olmo2",
    "Olmo2Config",
    "registry",
]

registry: dict[str, type[BaseModel]] = {
    "olmo2": Olmo2,
    "llama32": Llama32,
    "hf": HFCompatibleModel,
}
