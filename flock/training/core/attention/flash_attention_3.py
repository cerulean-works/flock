from collections.abc import Generator
from contextlib import contextmanager
from typing import cast

import torch
from torch._tensor import Tensor
from torch.nn import attention
from torch.nn.attention.varlen import varlen_attn

from .sdpa import sdpa_attention


@contextmanager
def _fa3_context(device: torch.device) -> Generator[None]:
    # Validate hardware here; SDPA handles dtype dispatch under autocast.
    # Also note that FA3 only supports CUDA tensors on a Hopper GPU.
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
    # Prepared int64 [batch, 2] padding spans must stay on CPU, even when Q/K/V are on CUDA.
    attention_mask: torch.Tensor | None = None,
    is_causal: bool = False,
) -> torch.Tensor:
    """Run full-sequence self-attention through PyTorch's FA3 implementation.

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
            returned by `prepare_flash_attention_mask`. Each call derives
            packing indices and cumulative lengths from these spans, then
            processes all nonempty sequences together with PyTorch's
            `varlen_attn` and the registered FA3 kernels.
        is_causal (`bool`, *optional*, defaults to `False`):
            Whether to prevent attention to future tokens.

    Returns:
        `torch.Tensor`: Attention output with the same shape as `q`. Padding
        query positions are zero; real-token outputs match masked SDPA.
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
        # Note that this should be CPU when we use prepared masking
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

        batch, heads, sequence, features = q.shape
        starts, stops = attention_mask.unbind(dim=1)
        if not ((0 <= starts) & (starts <= stops) & (stops <= sequence)).all():
            raise ValueError("FA3 padding spans must lie within Q's sequence length")

        lengths = stops - starts
        positions: Tensor = torch.arange(sequence, device=attention_mask.device)
        valid: Tensor = (positions >= starts[:, None]) & (positions < stops[:, None])
        indices = valid.flatten().nonzero().flatten().to(q.device, non_blocking=True)

        # from [batch, sequence, heads, features] to [batch * heads, sequence, features]
        # and it also remove the padding from the sequence dimension.
        packed_tensors: list[Tensor] = []
        for tensor in (q, k, v):
            packed_tensor = tensor.transpose(1, 2).flatten(0, 1).index_select(0, indices)
            packed_tensors.append(packed_tensor)
        query, key, value = packed_tensors

        # varlen custom op does not perform SDPA's autocast dispatch.
        if torch.is_autocast_enabled(device_type="cuda"):
            dtype = torch.get_autocast_dtype("cuda")
            packed_tensors = []
            for tensor in (query, key, value):
                if tensor.dtype in (torch.float16, torch.bfloat16, torch.float32):
                    tensor = tensor.to(dtype)
                packed_tensors.append(tensor)
            query, key, value = packed_tensors

        # if there is no valid indices, return the sum of the query, key, and value tensors.
        if indices.numel() == 0:
            output = query + key.sum() + value.sum()
        else:
            # remove the zero length sequences from the sequence dimension
            nonempty_lengths = lengths[lengths > 0].to(torch.int32)
            
            cu_seqlens: Tensor = torch.cat(
                (nonempty_lengths.new_zeros(1), nonempty_lengths.cumsum(0, dtype=torch.int32))
            ).to(q.device, non_blocking=True)
            maximum = int(nonempty_lengths.max())

            # window is the maximum sequence length for each batch
            window = (-1, 0) if is_causal else (maximum, maximum)

            output = varlen_attn(
                query,
                key,
                value,
                # cu_seq lens is the boundary of each sequence in the batch
                cu_seqlens,
                cu_seqlens,
                maximum,
                maximum,
                window_size=window,
                enable_gqa=q.shape[1] != k.shape[1],
                num_splits=1,
            )
            output = cast(Tensor, output)

        padded = output.new_zeros((batch * sequence, heads, features))
        # back to the original shape which is [batch * heads(with padding), sequence, features]
        padded.index_copy_(0, indices, output)
        # [batch * heads(with padding), sequence, features] -> [batch, sequence, heads, features]
        return padded.view(batch, sequence, heads, features).transpose(1, 2)
