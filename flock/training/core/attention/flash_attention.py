from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal, cast

import torch
from torch._tensor import Tensor
from torch.nn import attention
from torch.nn.attention.varlen import varlen_attn

from . import packing
from .metadata import VarlenMetadata
from .sdpa import sdpa_attention


@dataclass(frozen=True, slots=True, kw_only=True)
class _FlashImplementation:
    """A flash attention implementation registered with `torch.nn.attention`.

    `capabilities` lists the compute capability majors that PyTorch's integration
    accepts. The remaining fields fill in error messages: `gpus` describes the
    supported GPUs, and `install` names the kernels to install.
    """

    name: Literal["FA3", "FA4"]
    capabilities: tuple[int, ...]
    gpus: str
    install: str


_FA3 = _FlashImplementation(
    name="FA3",
    capabilities=(9,),
    gpus="a Hopper GPU (compute capability 9.0)",
    install="a FlashAttention-3 installation compatible with your PyTorch and CUDA versions",
)
_FA4 = _FlashImplementation(
    name="FA4",
    # PyTorch's FA4 integration rejects other GPUs, even where FA4 has kernels.
    capabilities=(9, 10),
    gpus="a Hopper or Blackwell data center GPU (compute capability 9.x or 10.x)",
    install="the flash-attn-4 package (flash-attn-4[cu13] with a CUDA 13 build of PyTorch)",
)


@contextmanager
def _flash_context(implementation: _FlashImplementation, device: torch.device) -> Generator[None]:
    # Validate hardware here; SDPA handles dtype dispatch under autocast.
    name: Literal["FA3", "FA4"] = implementation.name
    if device.type != "cuda":
        raise ValueError(f"{name} requires CUDA tensors on {implementation.gpus}")
    major, minor = torch.cuda.get_device_capability(device)
    if major not in implementation.capabilities:
        raise ValueError(
            f"{name} requires {implementation.gpus}, got compute capability {major}.{minor}"
        )

    # Check which flash attention implementation is active, if any.
    active: str | None = attention.current_flash_attention_impl()
    if active is None:
        try:
            attention.activate_flash_attention_impl(name)
        except ImportError as error:
            raise ImportError(
                f"PyTorch's {name} integration requires {implementation.install}; "
                f"installing torch alone does not provide the {name} kernels."
            ) from error
    elif active != name:
        raise RuntimeError(f"Cannot activate {name} while {active!r} is active in this process")

    with attention.sdpa_kernel(attention.SDPBackend.FLASH_ATTENTION):
        yield


def _flash_attention(
    implementation: _FlashImplementation,
    q: torch.Tensor,
    k: torch.Tensor,
    v: torch.Tensor,
    *,
    attention_metadata: VarlenMetadata | None,
    is_causal: bool,
) -> torch.Tensor:
    """Validate Q/K/V and metadata, then run them through `implementation`'s kernels."""
    name = implementation.name
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
            f"{name} expects full-sequence Q/K/V with matching dimensions and MHA/GQA heads"
        )

    if q.device != k.device or q.device != v.device:
        raise ValueError(f"{name} Q/K/V must share a device")

    if attention_metadata is not None and (
        not isinstance(attention_metadata, VarlenMetadata)
        or attention_metadata.batch_size != q.shape[0]
        or attention_metadata.seq_len != q.shape[2]
        or (
            attention_metadata.indices is not None and attention_metadata.indices.device != q.device
        )
        or attention_metadata.cu_seq_lens_q.device != q.device
        or attention_metadata.cu_seq_lens_k.device != q.device
    ):
        raise ValueError(
            f"{name} expects VarlenMetadata matching Q's batch size, sequence length and device; "
            "call prepare_varlen_metadata or prepare_packed_varlen_metadata first"
        )

    with _flash_context(implementation, q.device):
        # Not a fallback: _flash_context pins SDPA to its flash backend, so this still
        # runs FA3 or FA4. Without padding, the dense path skips the varlen pack/unpack.
        if attention_metadata is None:
            return sdpa_attention(q, k, v, is_causal=is_causal)

        indices = attention_metadata.indices
        if indices is None:
            # Packed documents cover every position, so flatten instead of gathering.
            query, key, value = (packing.flatten(tensor) for tensor in (q, k, v))
        else:
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
        if indices is not None and indices.numel() == 0:
            output = query + key.sum() + value.sum()
        else:
            max_length_k = attention_metadata.max_length_k

            # The window is (left, right); -1 means unlimited on that side.
            # A full-span finite window avoids PyTorch's cuDNN varlen dispatch.
            # FA4 runs any finite window as sliding-window attention, but this
            # window still covers every key in the row.
            window = (-1, 0) if is_causal else (max_length_k, max_length_k)

            output = varlen_attn(
                query,
                key,
                value,
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

        if indices is None:
            return packing.unflatten(output, q.shape[0], q.shape[2])
        return packing.unpack(output, indices, q.shape[0], q.shape[2])


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
            From `prepare_varlen_metadata` or `prepare_packed_varlen_metadata`, on Q's
            device. `None` means one unpadded sequence per row.
        is_causal (`bool`, *optional*, defaults to `False`):
            Whether to prevent attention to future tokens.

    Returns:
        `torch.Tensor`: Attention output with the same shape as `q`. Padding
        query positions are zero; real-token outputs match masked SDPA.
    """
    return _flash_attention(
        _FA3, q, k, v, attention_metadata=attention_metadata, is_causal=is_causal
    )


def flash_attention_4(
    q: torch.Tensor,
    k: torch.Tensor,
    v: torch.Tensor,
    *,
    attention_metadata: VarlenMetadata | None = None,
    is_causal: bool = False,
) -> torch.Tensor:
    """Run full-sequence self-attention through PyTorch's FA4 implementation.

    Takes the same inputs and metadata as `flash_attention_3`. FA4 runs on Hopper
    and Blackwell GPUs (compute capability 9.x or 10.x) and needs the external
    `flash-attn-4` package, which provides the `flash_attn.cute` kernels.

    Args:
        q (`torch.Tensor`):
            Queries with shape `(batch_size, num_query_heads, seq_len, head_dim)`.
        k (`torch.Tensor`):
            Keys with shape `(batch_size, num_kv_heads, seq_len, head_dim)`.
        v (`torch.Tensor`):
            Values with the same shape as `k`.
        attention_metadata (`VarlenMetadata`, *optional*):
            From `prepare_varlen_metadata` or `prepare_packed_varlen_metadata`, on Q's
            device. `None` means one unpadded sequence per row.
        is_causal (`bool`, *optional*, defaults to `False`):
            Whether to prevent attention to future tokens.

    Returns:
        `torch.Tensor`: Attention output with the same shape as `q`. Padding
        query positions are zero; real-token outputs match masked SDPA.
    """
    return _flash_attention(
        _FA4, q, k, v, attention_metadata=attention_metadata, is_causal=is_causal
    )
