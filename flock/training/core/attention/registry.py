from typing import ClassVar, Protocol

import torch

from .flash_attention_3 import flash_attention_3
from .sdpa import sdpa_attention


class AttentionFunction(Protocol):
    """Compute attention from prepared Q/K/V in [batch, heads, sequence, head_dim] order.

    Return the same layout, with Q's heads and sequence length. Kernels own mask
    handling and GQA, while the caller owns projections, normalization and RoPE.
    """

    def __call__(
        self,
        q: torch.Tensor,
        k: torch.Tensor,
        v: torch.Tensor,
        *,
        attention_mask: torch.Tensor | None = None,
        is_causal: bool = False,
    ) -> torch.Tensor: ...


class AttentionInterface:
    """Register attention functions by name."""

    _global_mapping: ClassVar[dict[str, AttentionFunction]] = {
        "sdpa": sdpa_attention,
        "flash_attention_3": flash_attention_3,
    }

    @classmethod
    def register(cls, name: str, function: AttentionFunction) -> None:
        """Register or replace an attention function."""
        cls._global_mapping[name] = function

    @classmethod
    def get_interface(cls, attn_implementation: str) -> AttentionFunction:
        """Resolve an implementation name to its attention function."""
        try:
            return cls._global_mapping[attn_implementation]
        except KeyError:
            available = ", ".join(sorted(cls._global_mapping))
            raise ValueError(
                f"Unknown attention implementation {attn_implementation!r}; available: {available}"
            ) from None
