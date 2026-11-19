from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

import torch

from .flash_attention import flash_attention_3, flash_attention_4
from .masking import prepare_sdpa_mask, prepare_varlen_metadata
from .sdpa import sdpa_attention


class AttentionFunction[MetadataT](Protocol):
    """Compute attention from prepared Q/K/V in [batch, heads, sequence, head_dim] order.

    Return the same layout and handle GQA. `MetadataT` is the metadata kind the
    function reads, as returned by its paired mask preparer.
    """

    def __call__(
        self,
        q: torch.Tensor,
        k: torch.Tensor,
        v: torch.Tensor,
        *,
        attention_metadata: MetadataT | None = None,
        is_causal: bool = False,
    ) -> torch.Tensor: ...


class _MaskPreparationFunction[MetadataT](Protocol):
    def __call__(
        self, padding_mask: torch.Tensor | None, *, is_causal: bool
    ) -> MetadataT | None: ...


@dataclass(frozen=True)
class _AttentionBackend[MetadataT]:
    function: AttentionFunction[MetadataT]
    mask_preparer: _MaskPreparationFunction[MetadataT]


class AttentionInterface:
    """Register attention functions and their mask preparers together by name."""

    _global_mapping: ClassVar[dict[str, _AttentionBackend[Any]]] = {
        "sdpa": _AttentionBackend(sdpa_attention, prepare_sdpa_mask),
        "flash_attention_3": _AttentionBackend(flash_attention_3, prepare_varlen_metadata),
        "flash_attention_4": _AttentionBackend(flash_attention_4, prepare_varlen_metadata),
    }

    @classmethod
    def register[MetadataT](
        cls,
        name: str,
        function: AttentionFunction[MetadataT],
        *,
        mask_preparer: _MaskPreparationFunction[MetadataT],
    ) -> None:
        """Register or replace an attention function with an explicit mask preparer."""
        cls._global_mapping[name] = _AttentionBackend(function, mask_preparer)

    @classmethod
    def get_interface(cls, attn_implementation: str) -> AttentionFunction[Any]:
        """Resolve an implementation name to its attention function."""
        return cls._get_backend(attn_implementation).function

    @classmethod
    def get_mask_preparer(cls, attn_implementation: str) -> _MaskPreparationFunction[Any]:
        """Resolve an implementation name to its model-level mask preparer."""
        return cls._get_backend(attn_implementation).mask_preparer

    @classmethod
    def _get_backend(cls, attn_implementation: str) -> _AttentionBackend[Any]:
        try:
            return cls._global_mapping[attn_implementation]
        except KeyError:
            available = ", ".join(sorted(cls._global_mapping))
            raise ValueError(
                f"Unknown attention implementation {attn_implementation!r}; available: {available}"
            ) from None
