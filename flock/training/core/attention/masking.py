from typing import NamedTuple

import torch


class PackedSequences(NamedTuple):
    """Describe documents packed end to end into the flattened `[batch * sequence]` tokens.

    Attention never crosses a document boundary, and each document starts at
    position zero. Documents must not span rows; the packing collator emits one row.

    Attributes:
        cu_seqlens (`torch.Tensor`): int32 boundaries shaped `[documents + 1]`, starting
            at zero and ending at `batch * sequence`, on the model's device.
        max_seqlen (`int`): Upper bound on any document's length, used to size kernels.
    """

    cu_seqlens: torch.Tensor
    max_seqlen: int


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


def prepare_sdpa_mask(
    padding_mask: torch.Tensor | None,
    *,
    is_causal: bool,
    packed: PackedSequences | None = None,
) -> torch.Tensor | None:
    """Combine padding, document boundaries and causality for SDPA.

    Without padding or packing the mask is omitted. Packed documents build a dense
    `[batch, 1, sequence, sequence]` mask, so prefer a flash backend for long packed rows.
    """
    if packed is not None:
        if padding_mask is None:
            raise ValueError("Packed SDPA attention needs the [batch, sequence] token mask")
        batch, sequence = padding_mask.shape
        positions = torch.arange(batch * sequence, device=packed.cu_seqlens.device)
        documents = torch.searchsorted(packed.cu_seqlens[1:], positions, right=True)
        documents = documents.view(batch, sequence).to(padding_mask.device)
        mask = documents[:, None, :, None] == documents[:, None, None, :]
        mask = mask & padding_mask[:, None, None, :]
        if is_causal:
            mask = (
                mask
                & torch.ones(
                    sequence, sequence, dtype=torch.bool, device=padding_mask.device
                ).tril()
            )
        return mask

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
    padding_mask: torch.Tensor | None,
    *,
    is_causal: bool,
    packed: PackedSequences | None = None,
) -> torch.Tensor | PackedSequences | None:
    """Return packed boundaries, or validated `[batch, 2]` start/stop spans on the CPU.

    Packed documents pass through unchanged; the packing collator already
    places padding in its own documents. Otherwise stops are exclusive. Prepare
    once per model forward and reuse across layers. Return `None` when there is
    no padding; causality stays in `is_causal`.
    """
    if packed is not None:
        cu_seqlens = packed.cu_seqlens
        if cu_seqlens.ndim != 1 or cu_seqlens.dtype != torch.int32 or cu_seqlens.numel() < 2:
            raise ValueError("Packed boundaries must be a 1D int32 tensor with at least 2 values")
        if packed.max_seqlen <= 0:
            raise ValueError("Packed max_seqlen must be positive")
        return packed

    padding_mask = _padding_mask_or_none(padding_mask)
    if padding_mask is None:
        return None

    sequence = padding_mask.shape[1]
    positions = torch.arange(sequence, device=padding_mask.device)
    starts = torch.where(padding_mask, positions, sequence).amin(dim=-1)
    stops = starts + padding_mask.sum(dim=-1)

    span_mask = (positions >= starts[:, None]) & (positions < stops[:, None])
    if not torch.equal(padding_mask, span_mask):
        raise ValueError(
            "Flash attention padding masks must contain one contiguous token span per row"
        )
    return torch.stack((starts, stops), dim=-1).cpu()
