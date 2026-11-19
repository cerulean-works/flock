from collections.abc import Generator
from contextlib import contextmanager
from typing import cast

import torch
from torch._tensor import Tensor
from torch.nn import attention
from torch.nn.attention.varlen import varlen_attn

from . import packing
from .metadata import VarlenMetadata
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
    attention_metadata: VarlenMetadata | None = None,
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
        attention_metadata (`VarlenMetadata`, *optional*):
            From `prepare_varlen_metadata`, on Q's device. `None` means no padding.
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
        # Q and K/V must have the same batch size.
        or q.shape[0] != k.shape[0]
        # Match both sequence length and features per head.
        or q.shape[2:] != k.shape[2:]
        # All Q dimensions must be nonzero.
        or min(q.shape) == 0
        # K/V must have at least one head.
        or k.shape[1] == 0
        # The number of Q heads must be a multiple of the number of K/V heads.
        or q.shape[1] % k.shape[1]
    ):
        raise ValueError(
            "FA3 expects full-sequence Q/K/V with matching dimensions and MHA/GQA heads"
        )

    if q.device != k.device or q.device != v.device:
        raise ValueError("FA3 Q/K/V must share a device")

    if attention_metadata is not None and (
        not isinstance(attention_metadata, VarlenMetadata)
        or attention_metadata.batch_size != q.shape[0]
        or attention_metadata.seq_len != q.shape[2]
        or attention_metadata.indices.device != q.device
        or attention_metadata.cu_seq_lens_q.device != q.device
        or attention_metadata.cu_seq_lens_k.device != q.device
    ):
        raise ValueError(
            "FA3 expects VarlenMetadata matching Q's batch size, sequence length and device; "
            "call prepare_varlen_metadata first"
        )

    with _fa3_context(q.device):
        # Not a fallback: _fa3_context pins SDPA to its FA3 flash backend, so this
        # still runs FA3. Without padding, the dense path skips the varlen pack/unpack.
        if attention_metadata is None:
            return sdpa_attention(q, k, v, is_causal=is_causal)

        indices = attention_metadata.indices
        query, key, value = (packing.pack(tensor, indices) for tensor in (q, k, v))

        # varlen custom op does not perform SDPA's autocast dispatch.
        if torch.is_autocast_enabled(device_type="cuda"):
            dtype = torch.get_autocast_dtype("cuda")
            packed_tensors: list[Tensor] = []
            for tensor in (query, key, value):
                if tensor.dtype in (torch.float16, torch.bfloat16, torch.float32):
                    tensor: Tensor = tensor.to(dtype)
                packed_tensors.append(tensor)
            query, key, value = packed_tensors

        # Keep the empty output connected to Q/K/V so backward produces zero gradients.
        if indices.numel() == 0:
            output = query + key.sum() + value.sum()
        else:
            max_length_k = attention_metadata.max_length_k

            # The window is (left, right); -1 means unlimited on that side.
            # A full-span finite window avoids PyTorch's cuDNN varlen dispatch.
            window = (-1, 0) if is_causal else (max_length_k, max_length_k)

            output = varlen_attn(
                query,
                key,
                value,
                # Sequence boundaries for Q and K/V in the packed batch.
                attention_metadata.cu_seq_lens_q,
                attention_metadata.cu_seq_lens_k,
                attention_metadata.max_length_q,
                max_length_k,
                window_size=window,
                enable_gqa=q.shape[1] != k.shape[1],
                # Let the "torch intelligence" decide how to split the attention computation.
                num_splits=None,
            )
            output = cast(Tensor, output)

        return packing.unpack(output, indices, q.shape[0], q.shape[2])
