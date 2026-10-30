from torch import nn

from .olmo2 import Olmo2_1B

registry: dict[str, type[nn.Module]] = {"olmo2_1b": Olmo2_1B}
