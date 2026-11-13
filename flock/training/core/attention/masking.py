import torch


def _padding_mask_or_none(padding_mask: torch.Tensor | None) -> torch.Tensor | None:
    if padding_mask is not None:
        if padding_mask.ndim != 2 or padding_mask.dtype != torch.bool:
            raise ValueError("Expected a boolean [batch, sequence] padding mask")
        if padding_mask.all().item():
            return None
    return padding_mask


def prepare_sdpa_mask(padding_mask: torch.Tensor | None, *, is_causal: bool) -> torch.Tensor | None:
    """Combine padding and causality for SDPA, omitting the mask when unpadded."""
    padding_mask = _padding_mask_or_none(padding_mask)
    if padding_mask is None:
        return None

    mask = padding_mask[:, None, None, :]
    if is_causal:
        sequence = padding_mask.shape[1]
        causal = torch.ones(sequence, sequence, dtype=torch.bool, device=padding_mask.device).tril()
        mask = mask & causal
    return mask


def prepare_flash_attention_mask(
    padding_mask: torch.Tensor | None, *, is_causal: bool
) -> torch.Tensor | None:
    """Keep padding and causality separate for the FlashAttention adapter."""
    return _padding_mask_or_none(padding_mask)
