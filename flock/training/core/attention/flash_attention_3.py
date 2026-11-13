from collections.abc import Generator
from contextlib import contextmanager

import torch
from torch._tensor import Tensor
from torch.nn import attention

from .sdpa import sdpa_attention


@contextmanager
def _fa3_context(device: torch.device) -> Generator[None]:
    # Validate hardware here; SDPA handles dtype dispatch under autocast.
    if device.type != "cuda":
        raise ValueError("flash_attention_3 requires CUDA tensors on a Hopper GPU")
    if torch.cuda.get_device_capability(device)[0] != 9:
        raise ValueError("flash_attention_3 requires a Hopper GPU (compute capability 9.0)")

    # Check which flash attention implementation is active, if any.
    active = attention.current_flash_attention_impl()
    if active is None:
        try:
            attention.activate_flash_attention_impl("FA3")
        except ImportError as error:
            raise ImportError(
                "PyTorch's FA3 integration requires a compatible FlashAttention-3 installation; "
                "installing torch alone does not provide the FA3 kernels."
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
    """Run full-sequence self-attention through PyTorch's FA3 SDPA implementation.

    Q/K/V use four dimensions: batch size, number of attention heads, sequence
    length, and features per head (`head_dim`), in that order. Batch size,
    sequence length, and `head_dim` must match across Q/K/V. The number of query
    heads must be a multiple of the number of key/value heads, supporting both
    multi-head attention (MHA) and grouped-query attention (GQA).

    Args:
        q (`torch.Tensor`):
            Queries with shape `(batch_size, num_query_heads, seq_len, head_dim)`.
        k (`torch.Tensor`):
            Keys with shape `(batch_size, num_kv_heads, seq_len, head_dim)`.
        v (`torch.Tensor`):
            Values with the same shape as `k`.
        attention_mask (`torch.Tensor`, *optional*):
            Prepared CPU int64 padding spans with shape `(batch_size, 2)`, as
            returned by `prepare_flash_attention_mask`. These spans must stay
            on the CPU even though Q/K/V are on CUDA, so reading slice indices
            does not synchronize with CUDA in each layer. The preparer accepts
            the original padding mask on either CPU or CUDA.
        is_causal (`bool`, *optional*, defaults to `False`):
            Whether to prevent attention to future tokens.

    Returns:
        `torch.Tensor`: Attention output with the same shape as `q`.
    """

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

    if q.device != k.device or q.device != v.device:
        raise ValueError("FA3 Q/K/V must share a device")

    if attention_mask is not None and (
        attention_mask.shape != (q.shape[0], 2)
        or attention_mask.device.type != "cpu"
        or attention_mask.dtype != torch.int64
    ):
        raise ValueError(
            "FA3 expects prepared CPU int64 [batch, 2] padding spans; "
            "call prepare_flash_attention_mask first"
        )

    with _fa3_context(q.device):
        if attention_mask is None:
            return sdpa_attention(q, k, v, is_causal=is_causal)

        rows = []
        for row, span in enumerate(attention_mask):
            # The spans are already on the CPU, so indexing never waits for CUDA.
            start, stop = int(span[0]), int(span[1])
            if not 0 <= start <= stop <= q.shape[2]:
                raise ValueError("FA3 padding spans must lie within Q's sequence length")
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
