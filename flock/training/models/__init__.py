from torch import nn

from .olmo2 import Olmo2

registry: dict[str, type[nn.Module]] = {"olmo2": Olmo2}
