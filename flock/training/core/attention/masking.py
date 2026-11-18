import torch

from . import packing
from .metadata import VarlenMetadata


def _padding_mask_or_none(padding_mask: torch.Tensor | None) -> torch.Tensor | None:
    """Validate a padding mask, returning `None` when no padding is present.

    `padding_mask` must be `None` or a `torch.bool` tensor with shape
    `(batch_size, sequence_length)`: one row per sequence and one column per
    token. `True` marks a real token; `False` marks padding. An all-`True` mask
    is treated as `None`.
    """
    if padding_mask is None:
        return padding_mask
    if padding_mask.ndim != 2 or padding_mask.dtype != torch.bool:
        raise ValueError("Expected a boolean [batch, sequence] padding mask")
    if padding_mask.all().item():
        return None
    return padding_mask


def prepare_sdpa_mask(padding_mask: torch.Tensor | None, *, is_causal: bool) -> torch.Tensor | None:
    """Combine padding and causality into one SDPA mask, omitting it when unpadded."""
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
) -> VarlenMetadata | None:
    """Return FA3's varlen metadata on the padding mask's device.

    Prepare once per model forward and pass it to every layer. Return `None`
    when there is no padding; causality always stays in `is_causal`.
    """
    padding_mask = _padding_mask_or_none(padding_mask)
    if padding_mask is None:
        return None

    sequence = padding_mask.shape[1]
    positions = torch.arange(sequence, device=padding_mask.device)
    starts = torch.where(padding_mask, positions, sequence).amin(dim=-1)
    stops = starts + padding_mask.sum(dim=-1)

    span_mask = (positions >= starts[:, None]) & (positions < stops[:, None])
    if not torch.equal(padding_mask, span_mask):
        raise ValueError("FA3 padding masks must contain one contiguous token span per row")

    # Build the metadata on CPU from the exclusive-stop spans, then copy it once.
    starts, stops = torch.stack((starts, stops), dim=-1).cpu().unbind(dim=1)
    positions = torch.arange(sequence)
    valid = (positions >= starts[:, None]) & (positions < stops[:, None])
    indices = valid.flatten().nonzero().flatten()
    cu_seq_lens, max_length = packing.cu_seqlens(stops - starts)
    device = padding_mask.device
    cu_seq_lens = cu_seq_lens.to(device, non_blocking=True)
    # Self-attention: queries and keys share one set of lengths.
    return VarlenMetadata(
        cu_seq_lens_q=cu_seq_lens,
        cu_seq_lens_k=cu_seq_lens,
        max_length_q=max_length,
        max_length_k=max_length,
        indices=indices.to(device, non_blocking=True),
    )
