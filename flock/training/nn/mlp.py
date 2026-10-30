import torch
from torch import nn
from torch.nn import functional as F


class SwiGLU(nn.Module):
    """Gated SiLU MLP: down(silu(gate(x)) * up(x))."""

    def __init__(self, *, hidden_size: int, intermediate_size: int, bias: bool = False):
        super().__init__()
        if hidden_size <= 0 or intermediate_size <= 0:
            raise ValueError("hidden_size and intermediate_size must be positive")
        self.gate_proj = nn.Linear(hidden_size, intermediate_size, bias=bias)
        self.up_proj = nn.Linear(hidden_size, intermediate_size, bias=bias)
        self.down_proj = nn.Linear(intermediate_size, hidden_size, bias=bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:  # ty: ignore[invalid-mutable-override]
        return self.down_proj(F.silu(self.gate_proj(x)) * self.up_proj(x))
