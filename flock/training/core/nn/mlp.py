"""Provide the gated feed-forward projection used by the decoder."""

import torch
from torch import nn
from torch.nn import functional as F


class SwiGLU(nn.Module):
    """Compute `down(silu(gate(x)) * up(x))` while preserving the hidden width.

    Args:
        hidden_size (`int`): Positive input and output width.
        intermediate_size (`int`): Positive gate and up-projection width.
        bias (`bool`, *optional*, defaults to `False`): Add biases to all projections.

    Raises:
        `ValueError`: If either width is nonpositive.
    """

    def __init__(self, *, hidden_size: int, intermediate_size: int, bias: bool = False) -> None:
        """Construct independent gate, up and down projections."""
        super().__init__()
        if hidden_size <= 0 or intermediate_size <= 0:
            raise ValueError("hidden_size and intermediate_size must be positive")
        self.gate_proj = nn.Linear(hidden_size, intermediate_size, bias=bias)
        self.up_proj = nn.Linear(hidden_size, intermediate_size, bias=bias)
        self.down_proj = nn.Linear(intermediate_size, hidden_size, bias=bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:  # ty: ignore[invalid-mutable-override]
        """Return gated hidden states with the same leading dimensions and width as `x`."""
        return self.down_proj(F.silu(self.gate_proj(x)) * self.up_proj(x))
