from dataclasses import dataclass
from typing import ClassVar, Protocol

import torch

from .flash_attention_3 import flash_attention_3
from .masking import prepare_flash_attention_mask, prepare_sdpa_mask
from .sdpa import sdpa_attention


class AttentionFunction(Protocol):
    """Compute attention from prepared Q/K/V in [batch, heads, sequence, head_dim] order.

    Return the same layout, with Q's heads and sequence length. Each function
    defines its supported mask formats and handles GQA. Models use the registered
    mask preparer; direct callers supply an already prepared mask. `is_causal`
    describes the attention pattern; SDPA masks must already include causality.
    The caller owns projections, normalization and RoPE.
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


class _MaskPreparationFunction(Protocol):
    def __call__(
        self, padding_mask: torch.Tensor | None, *, is_causal: bool
    ) -> torch.Tensor | None: ...


@dataclass(frozen=True)
class _AttentionBackend:
    function: AttentionFunction
    mask_preparer: _MaskPreparationFunction


class AttentionInterface:
    """Register attention functions and their mask preparers together by name."""

    _global_mapping: ClassVar[dict[str, _AttentionBackend]] = {
        "sdpa": _AttentionBackend(sdpa_attention, prepare_sdpa_mask),
        "flash_attention_3": _AttentionBackend(flash_attention_3, prepare_flash_attention_mask),
    }

    @classmethod
    def register(
        cls,
        name: str,
        function: AttentionFunction,
        *,
        mask_preparer: _MaskPreparationFunction,
    ) -> None:
        """Register or replace an attention function with an explicit mask preparer."""
        cls._global_mapping[name] = _AttentionBackend(function, mask_preparer)

    @classmethod
    def get_interface(cls, attn_implementation: str) -> AttentionFunction:
        """Resolve an implementation name to its attention function."""
        return cls._get_backend(attn_implementation).function

    @classmethod
    def get_mask_preparer(cls, attn_implementation: str) -> _MaskPreparationFunction:
        """Resolve an implementation name to its model-level mask preparer."""
        return cls._get_backend(attn_implementation).mask_preparer

    @classmethod
    def _get_backend(cls, attn_implementation: str) -> _AttentionBackend:
        try:
            return cls._global_mapping[attn_implementation]
        except KeyError:
            available = ", ".join(sorted(cls._global_mapping))
            raise ValueError(
                f"Unknown attention implementation {attn_implementation!r}; available: {available}"
            ) from None
