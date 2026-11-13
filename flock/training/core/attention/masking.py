import torch


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
    """Return validated `[batch, 2]` start/stop spans as a CPU int64 tensor.

    The input boolean `[batch, sequence]` padding mask may be on CPU or CUDA,
    including after `Trainer` moves the collator's batch to its device. This
    function copies the prepared spans to CPU; keep them there when passing
    them to `flash_attention_3` to avoid CUDA synchronization in each layer.

    Stops are exclusive. Prepare once per model forward and reuse across layers.
    Return `None` when there is no padding; causality stays in `is_causal`.
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
    return torch.stack((starts, stops), dim=-1).cpu()
