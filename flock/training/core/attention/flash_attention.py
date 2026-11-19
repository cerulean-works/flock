from collections.abc import Generator
from contextlib import contextmanager
from typing import Annotated, Literal, cast

import torch
from torch._tensor import Tensor
from torch.nn import attention
from torch.nn.attention.varlen import varlen_attn

from .masking import PackedSequences
from .sdpa import sdpa_attention

type FlashImplementation = Literal["FA3", "FA4"]

# Compute capability majors each kernel family supports, the GPU name and its capabilities.
_SUPPORTED_GPUS: dict[FlashImplementation, tuple[tuple[int, ...], str, str]] = {
    "FA3": ((9,), "a Hopper GPU", "compute capability 9.0"),
    "FA4": ((9, 10), "a Hopper or Blackwell GPU", "compute capability 9.0 or 10.0"),
}
_KERNEL_PACKAGES: dict[FlashImplementation, str] = {
    "FA3": "a compatible FlashAttention-3 installation",
    "FA4": "the FlashAttention-4 CuTe kernels (`flash_attn.cute`)",
}


@contextmanager
def _flash_context(device: torch.device, impl: FlashImplementation) -> Generator[None]:
    # Validate hardware here; SDPA handles dtype dispatch under autocast.
    majors, gpu, capability = _SUPPORTED_GPUS[impl]
    name = f"flash_attention_{impl[-1]}"
    if device.type != "cuda":
        raise ValueError(f"{name} requires CUDA tensors on {gpu}")
    if torch.cuda.get_device_capability(device)[0] not in majors:
        raise ValueError(f"{name} requires {gpu} ({capability})")

    # Check which flash attention implementation is active, if any.
    active = attention.current_flash_attention_impl()
    if active is None:
        try:
            attention.activate_flash_attention_impl(impl)
        except ImportError as error:
            raise ImportError(
                f"PyTorch's {impl} integration requires {_KERNEL_PACKAGES[impl]}; "
                f"installing torch alone does not provide the {impl} kernels."
            ) from error
    elif active != impl:
        raise RuntimeError(f"Cannot activate {impl} while {active!r} is active in this process")

    with attention.sdpa_kernel(attention.SDPBackend.FLASH_ATTENTION):
        yield


def _autocast_packed(*tensors: Tensor) -> list[Tensor]:
    # varlen custom op does not perform SDPA's autocast dispatch.
    if not torch.is_autocast_enabled(device_type="cuda"):
        return list(tensors)
    dtype = torch.get_autocast_dtype("cuda")
    return [
        tensor.to(dtype)
        if tensor.dtype in (torch.float16, torch.bfloat16, torch.float32)
        else tensor
        for tensor in tensors
    ]


def _varlen(
    query: Tensor,
    key: Tensor,
    value: Tensor,
    cu_seqlens: Tensor,
    maximum: int,
    *,
    is_causal: bool,
    enable_gqa: bool,
) -> Tensor:
    # The window is (left, right); -1 means unlimited on that side.
    # A full-span finite window avoids PyTorch's cuDNN varlen dispatch.
    window = (-1, 0) if is_causal else (maximum, maximum)
    output = varlen_attn(
        query,
        key,
        value,
        # Sequence boundaries for Q and K/V in the packed batch.
        cu_seqlens,
        cu_seqlens,
        maximum,
        maximum,
        window_size=window,
        enable_gqa=enable_gqa,
        # Let the "torch intelligence" decide how to split the attention computation.
        num_splits=None,
    )
    return cast(Tensor, output)


def _flash_attention(
    q: torch.Tensor,
    k: torch.Tensor,
    v: torch.Tensor,
    *,
    attention_mask: torch.Tensor | PackedSequences | None,
    is_causal: bool,
    impl: FlashImplementation,
) -> torch.Tensor:
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
            f"{impl} expects full-sequence Q/K/V with matching dimensions and MHA/GQA heads"
        )

    if q.device != k.device or q.device != v.device:
        raise ValueError(f"{impl} Q/K/V must share a device")

    packed = attention_mask if isinstance(attention_mask, PackedSequences) else None
    if packed is None and attention_mask is not None:
        attention_mask = cast(Tensor, attention_mask)
        if (
            attention_mask.shape != (q.shape[0], 2)
            # Note that this should be CPU when we use prepared masking
            or attention_mask.device.type != "cpu"
            or attention_mask.dtype != torch.int64
        ):
            raise ValueError(
                f"{impl} expects prepared CPU int64 [batch, 2] padding spans; "
                "call prepare_flash_attention_mask first"
            )

    with _flash_context(q.device, impl):
        if attention_mask is None:
            return sdpa_attention(q, k, v, is_causal=is_causal)

        batch, heads, sequence, features = q.shape
        enable_gqa = q.shape[1] != k.shape[1]

        if packed is not None:
            # Every position belongs to one document, so the row flattens without indexing.
            query, key, value = _autocast_packed(
                *(tensor.transpose(1, 2).flatten(0, 1) for tensor in (q, k, v))
            )
            output = _varlen(
                query,
                key,
                value,
                packed.cu_seqlens,
                min(packed.max_seqlen, batch * sequence),
                is_causal=is_causal,
                enable_gqa=enable_gqa,
            )
            return output.view(batch, sequence, heads, features).transpose(1, 2)

        starts, stops = cast(Tensor, attention_mask).unbind(dim=1)
        if not ((0 <= starts) & (starts <= stops) & (stops <= sequence)).all():
            raise ValueError(f"{impl} padding spans must lie within Q's sequence length")

        lengths = stops - starts
        positions: Tensor = torch.arange(sequence, device=starts.device)
        valid: Tensor = (positions >= starts[:, None]) & (positions < stops[:, None])
        indices = valid.flatten().nonzero().flatten().to(q.device, non_blocking=True)

        # Transpose to [batch, sequence, heads, features], then pack valid tokens.
        # The packed shape is [total_valid_tokens, heads, features].
        query, key, value = _autocast_packed(
            *(tensor.transpose(1, 2).flatten(0, 1).index_select(0, indices) for tensor in (q, k, v))
        )

        # Keep the empty output connected to Q/K/V so backward produces zero gradients.
        if indices.numel() == 0:
            output = query + key.sum() + value.sum()
        else:
            # Build packed boundaries using only nonempty sequence lengths.
            nonempty_lengths = lengths[lengths > 0].to(torch.int32)

            cu_seqlens: Tensor = torch.cat(
                (nonempty_lengths.new_zeros(1), nonempty_lengths.cumsum(0, dtype=torch.int32))
            ).to(q.device, non_blocking=True)
            output = _varlen(
                query,
                key,
                value,
                cu_seqlens,
                int(nonempty_lengths.max()),
                is_causal=is_causal,
                enable_gqa=enable_gqa,
            )

        padded = output.new_zeros((batch * sequence, heads, features))
        # Scatter results back to [batch * sequence, heads, features]; padding stays zero.
        padded.index_copy_(0, indices, output)
        # Reshape to [batch, sequence, heads, features], then restore the original head axis.
        return padded.view(batch, sequence, heads, features).transpose(1, 2)


def flash_attention_3(
    q: torch.Tensor,
    k: torch.Tensor,
    v: torch.Tensor,
    *,
    attention_mask: Annotated[torch.Tensor, "CPU int64 [batch, 2] padding spans"]
    | PackedSequences
    | None = None,
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
        attention_mask (`torch.Tensor` or `PackedSequences`, *optional*):
            Output of `prepare_flash_attention_mask`. Prepared CPU int64 padding
            spans with shape `(batch_size, 2)` become packing indices and
            cumulative lengths, then all nonempty sequences run together with
            PyTorch's `varlen_attn` and the registered FA3 kernels.
            `PackedSequences` boundaries run the flattened row through
            `varlen_attn` directly, so attention stays within each document.
        is_causal (`bool`, *optional*, defaults to `False`):
            Whether to prevent attention to future tokens.

    Returns:
        `torch.Tensor`: Attention output with the same shape as `q`. Padding
        query positions are zero; real-token outputs match masked SDPA.
    """
    return _flash_attention(q, k, v, attention_mask=attention_mask, is_causal=is_causal, impl="FA3")


def flash_attention_4(
    q: torch.Tensor,
    k: torch.Tensor,
    v: torch.Tensor,
    *,
    attention_mask: Annotated[torch.Tensor, "CPU int64 [batch, 2] padding spans"]
    | PackedSequences
    | None = None,
    is_causal: bool = False,
) -> torch.Tensor:
    """Run full-sequence self-attention through PyTorch's FA4 implementation.

    FA4 runs on Hopper and Blackwell GPUs (compute capability 9.0 or 10.0) using
    the FlashAttention-4 CuTe kernels. Inputs, masks and outputs follow
    [`training.core.attention.flash_attention_3`]. FA3 and FA4 cannot both be
    active in one process.

    Args:
        q (`torch.Tensor`):
            Queries with shape `(batch_size, num_query_heads, seq_len, head_dim)`.
        k (`torch.Tensor`):
            Keys with shape `(batch_size, num_kv_heads, seq_len, head_dim)`.
        v (`torch.Tensor`):
            Values with the same shape as `k`.
        attention_mask (`torch.Tensor` or `PackedSequences`, *optional*):
            Output of `prepare_flash_attention_mask`: CPU int64 padding spans
            with shape `(batch_size, 2)`, or packed document boundaries.
        is_causal (`bool`, *optional*, defaults to `False`):
            Whether to prevent attention to future tokens.

    Returns:
        `torch.Tensor`: Attention output with the same shape as `q`. Padding
        query positions are zero; real-token outputs match masked SDPA.
    """
    return _flash_attention(q, k, v, attention_mask=attention_mask, is_causal=is_causal, impl="FA4")
