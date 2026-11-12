from torch._tensor import Tensor
from collections.abc import Generator
from contextlib import contextmanager

import torch
from torch.nn import attention

from .sdpa import sdpa_attention


@contextmanager
def _fa3_context(device: torch.device, dtype: torch.dtype) -> Generator[None]:
    # FA3 have strict dtype and device requirement so we must check it before running.
    if device.type != "cuda":
        raise ValueError("flash_attention_3 requires CUDA tensors on a Hopper GPU")
    if dtype not in (torch.float16, torch.bfloat16):
        raise ValueError("flash_attention_3 requires float16 or bfloat16 Q/K/V")
    if torch.cuda.get_device_capability(device)[0] != 9:
        raise ValueError("flash_attention_3 requires a Hopper GPU (compute capability 9.0)")

    # Check which flash attention implementation is active, if any.
    active = attention.current_flash_attention_impl()
    if active is None:
        try:
            attention.activate_flash_attention_impl("FA3")
        except ImportError as error:
            raise ImportError(
                "PyTorch's FA3 integration requires a compatible FlashAttention-3 installation. "
            ) from error
    elif active != "FA3":
        raise RuntimeError(f"Cannot activate FA3 while {active!r} is active in this process")

    with attention.sdpa_kernel(attention.SDPBackend.FLASH_ATTENTION):
        yield


def flash_attention_3(
    q: torch.Tensor,
    k: torch.Tensor,
    v: torch.Tensor,
    *,
    attention_mask: torch.Tensor | None = None,
    is_causal: bool = False,
) -> torch.Tensor:
    """Run full-sequence self-attention through PyTorch's FA3 SDPA implementation."""

    if (
        q.ndim != 4
        or k.ndim != 4
        or v.shape != k.shape
        # batch size and head dimension are the same.
        or q.shape[0] != k.shape[0]
        # shape[2:] is sequence dimension.
        or q.shape[2:] != k.shape[2:]
        # sequence length is non-zero.
        or min(q.shape) == 0
        # shape[1] is head dimension.
        or k.shape[1] == 0
        # so basically we make sure the head dimension is a multiple of the query head dimension
        # since the head dimension is a multiple of the key head dimension
        or q.shape[1] % k.shape[1]
    ):
        raise ValueError(
            "FA3 expects full-sequence Q/K/V with matching dimensions and MHA/GQA heads"
        )

    if q.device != k.device or q.device != v.device or q.dtype != k.dtype or q.dtype != v.dtype:
        raise ValueError("FA3 Q/K/V must share a device and dtype")

    # Each span represents a range of non-padding token positions in the sequence,
    # specified as [start, end] (start inclusive, end exclusive).
    spans: list[list[int]] | None = None
    if attention_mask is not None:
        if (
            attention_mask.dtype != torch.bool
            # the mask must have the same shape as the query sequence (shape[0] is batch size, shape[1] is sequence length)
            or attention_mask.shape != (q.shape[0], q.shape[2])
            or attention_mask.device != q.device
        ):
            raise ValueError("FA3 expects a boolean [batch, sequence] padding mask on Q's device")

        positions = torch.arange(q.shape[2], device=q.device)
        starts = torch.where(attention_mask, positions, q.shape[2]).amin(dim=-1)
        stops = starts + attention_mask.sum(dim=-1)
        spans = torch.stack((starts, stops), dim=-1).tolist()

    with _fa3_context(q.device, q.dtype):
        if spans is None:
            return sdpa_attention(q, k, v, is_causal=is_causal)

        rows = []
        for row, (start, stop) in enumerate(spans):
            query: Tensor = q[row : row + 1]
            key: Tensor = k[row : row + 1, :, start:stop]
            value = v[row : row + 1, :, start:stop]
            # If the row is empty, return zero output.
            if start == stop:
                zero = query[:, :, :0].sum() + key.sum() + value.sum()
                output = torch.zeros_like(query) + zero
            # If the row is not empty, compute attention.
            elif not is_causal:
                output = sdpa_attention(query, key, value)
            # If the row is not empty and causal, compute causal attention.
            else:
                chunks: list[Tensor] = [torch.zeros_like(query[:, :, :start])]
                chunks.append(sdpa_attention(query[:, :, start:stop], key, value, is_causal=True))
                if stop < q.shape[2]:
                    chunks.append(sdpa_attention(query[:, :, stop:], key, value))
                output = torch.cat(chunks, dim=2)
            rows.append(output)
        return torch.cat(rows, dim=0)
