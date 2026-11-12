from dataclasses import dataclass
from typing import Protocol

import torch


@dataclass(frozen=True)
class PreparedAttentionMask:
    """Keep a backend's mask and causal flag together."""

    attention_mask: torch.Tensor | None
    is_causal: bool


class MaskPreparationFunction(Protocol):
    """Prepare a boolean padding mask for full-sequence self-attention."""

    def __call__(
        self, padding_mask: torch.Tensor | None, *, is_causal: bool
    ) -> PreparedAttentionMask: ...


def _padding_mask_or_none(padding_mask: torch.Tensor | None) -> torch.Tensor | None:
    if padding_mask is not None:
        if padding_mask.ndim != 2 or padding_mask.dtype != torch.bool:
            raise ValueError("Expected a boolean [batch, sequence] padding mask")
        if padding_mask.all().item():
            return None
    return padding_mask


def prepare_sdpa_mask(
    padding_mask: torch.Tensor | None, *, is_causal: bool
) -> PreparedAttentionMask:
    """Combine padding and causality for SDPA, omitting the mask when unpadded."""
    padding_mask = _padding_mask_or_none(padding_mask)
    if padding_mask is None:
        return PreparedAttentionMask(None, is_causal)

    mask = padding_mask[:, None, None, :]
    if is_causal:
        sequence = padding_mask.shape[1]
        causal = torch.ones(sequence, sequence, dtype=torch.bool, device=padding_mask.device).tril()
        mask = mask & causal
    return PreparedAttentionMask(mask, False)


def prepare_flash_attention_mask(
    padding_mask: torch.Tensor | None, *, is_causal: bool
) -> PreparedAttentionMask:
    """Keep padding and causality separate for the FlashAttention adapter."""
    return PreparedAttentionMask(_padding_mask_or_none(padding_mask), is_causal)
