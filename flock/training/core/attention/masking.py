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


def prepare_varlen_metadata(
    padding_mask: torch.Tensor | None, *, is_causal: bool
) -> VarlenMetadata | None:
    """Return `VarlenMetadata` for varlen backends such as FA3 and FA4, on the mask's device.

    Prepare once per model forward and pass it to every layer. Return `None`
    when there is no padding; causality always stays in `is_causal`.
    """
    padding_mask = _padding_mask_or_none(padding_mask)
    if padding_mask is None:
        return None

    batch, sequence = padding_mask.shape
    positions = torch.arange(sequence, device=padding_mask.device)
    starts = torch.where(padding_mask, positions, sequence).amin(dim=-1)
    stops = starts + padding_mask.sum(dim=-1)

    span_mask = (positions >= starts[:, None]) & (positions < stops[:, None])
    if not torch.equal(padding_mask, span_mask):
        raise ValueError("Varlen padding masks must contain one contiguous token span per row")

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
        batch_size=batch,
        seq_len=sequence,
    )


def prepare_packed_sdpa_mask(
    cu_seq_lens: torch.Tensor, *, batch_size: int, seq_len: int, is_causal: bool = True
) -> torch.Tensor:
    """Build a boolean [batch, 1, seq_len, seq_len] SDPA mask on the boundaries' device.

    The collator supplies cumulative boundaries covering every position in the flattened
    batch, with each row's padding in a separate segment. Each query sees keys in its own
    segment, up to and including its own position when causal.
    """
    positions = torch.arange(
        batch_size * seq_len, dtype=cu_seq_lens.dtype, device=cu_seq_lens.device
    )
    # A token at an exclusive end belongs to the next segment.
    segment_ids = torch.bucketize(positions, cu_seq_lens[1:], right=True, out_int32=True)
    segment_ids = segment_ids.view(batch_size, seq_len)
    mask = segment_ids[:, :, None] == segment_ids[:, None, :]
    if is_causal:
        mask.tril_()
    return mask[:, None]


def prepare_packed_varlen_metadata(
    cu_seq_lens: torch.Tensor, *, batch_size: int, seq_len: int, is_causal: bool = True
) -> VarlenMetadata:
    """Return `VarlenMetadata` for a packed batch without reading from its device.

    `cu_seq_lens` holds int32 document boundaries in the flattened
    `[batch_size * seq_len]` axis, from 0 to `batch_size * seq_len`. The documents
    cover every position, including each row's padding segment, so the backend
    reshapes instead of gathering. The collator builds the boundaries on the CPU.
    Documents never span rows, so `seq_len` bounds every document's length. The
    bound is static, so the kernel's integer arguments never wait for the device.
    FlashAttention applies `is_causal` at attention time, so both directions use
    the same metadata.
    """
    return VarlenMetadata(
        cu_seq_lens_q=cu_seq_lens,
        cu_seq_lens_k=cu_seq_lens,
        max_length_q=seq_len,
        max_length_k=seq_len,
        indices=None,
        batch_size=batch_size,
        seq_len=seq_len,
    )
