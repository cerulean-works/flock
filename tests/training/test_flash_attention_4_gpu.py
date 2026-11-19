"""Explicit FA4 checks: require a Hopper or Blackwell GPU and the FA4 CuTe kernels."""

from collections.abc import Iterator
from dataclasses import replace

import pytest
import torch
from torch.nn import attention
from torch.nn import functional as F

from flock.training.core.attention import (
    PackedSequences,
    flash_attention_4,
    prepare_flash_attention_mask,
    prepare_sdpa_mask,
    sdpa_attention,
)
from flock.training.core.models.olmo2 import Olmo2Config
from tests.training._reference import assert_parity, olmo2_training_parity

pytestmark = [pytest.mark.gpu, pytest.mark.fa4]


@pytest.fixture(autouse=True)
def fa4_session() -> Iterator[None]:
    previous = attention.current_flash_attention_impl()
    assert previous in (None, "FA4"), "run FA4 checks without another custom flash implementation"
    try:
        yield
    finally:
        # All backward work has finished; leave unrelated GPU tests' dispatch intact.
        if previous is None and attention.current_flash_attention_impl() == "FA4":
            attention.restore_flash_attention_impl()


@pytest.mark.parametrize("dtype", [torch.float16, torch.bfloat16])
@pytest.mark.parametrize("kv_heads", [4, 2, 1], ids=["mha", "gqa", "mqa"])
@pytest.mark.parametrize("is_causal", [False, True])
def test_varlen_mixed_padding_outputs_and_gradients(
    cuda_device: torch.device, dtype: torch.dtype, kv_heads: int, is_causal: bool
) -> None:
    mask = torch.zeros(5, 160, dtype=torch.bool, device=cuda_device)
    mask[0, :140] = True
    mask[1, 11:] = True
    mask[2, 7:150] = True
    mask[3, 50] = True
    inputs = [
        torch.randn(5, 160, heads, 64, device=cuda_device, dtype=dtype)
        .transpose(1, 2)
        .requires_grad_()
        for heads in (4, kv_heads, kv_heads)
    ]
    references = [tensor.detach().clone().requires_grad_() for tensor in inputs]
    spans = prepare_flash_attention_mask(mask, is_causal=is_causal)
    with attention.sdpa_kernel(attention.SDPBackend.MATH):
        expected = sdpa_attention(
            *references, attention_mask=prepare_sdpa_mask(mask, is_causal=is_causal)
        ).masked_fill(~mask[:, None, :, None], 0)
    actual = flash_attention_4(*inputs, attention_mask=spans, is_causal=is_causal)
    assert_parity(actual, expected, dtype, "logits", "padded attention")
    upstream = torch.randn_like(actual)
    actual.backward(upstream)
    expected.backward(upstream)
    for tensor, reference in zip(inputs, references, strict=True):
        assert_parity(tensor.grad, reference.grad, dtype, "gradients", "padded attention")
    assert attention.current_flash_attention_impl() == "FA4"


@pytest.mark.parametrize("dtype", [torch.float16, torch.bfloat16])
@pytest.mark.parametrize("kv_heads", [4, 1], ids=["mha", "mqa"])
def test_packed_documents_match_documentwise_attention(
    cuda_device: torch.device, dtype: torch.dtype, kv_heads: int
) -> None:
    lengths = [130, 1, 257, 64, 60]
    total = sum(lengths)
    inputs = [
        torch.randn(1, total, heads, 64, device=cuda_device, dtype=dtype)
        .transpose(1, 2)
        .requires_grad_()
        for heads in (4, kv_heads, kv_heads)
    ]
    references = [tensor.detach().clone().requires_grad_() for tensor in inputs]
    boundaries = torch.tensor([0, *lengths], device=cuda_device).cumsum(0).to(torch.int32)
    packed = PackedSequences(boundaries, max_seqlen=max(lengths))

    actual = flash_attention_4(*inputs, attention_mask=packed, is_causal=True)
    pieces, start = [], 0
    with attention.sdpa_kernel(attention.SDPBackend.MATH):
        for length in lengths:
            window = slice(start, start + length)
            q, k, v = (tensor[:, :, window] for tensor in references)
            pieces.append(
                F.scaled_dot_product_attention(q, k, v, is_causal=True, enable_gqa=kv_heads != 4)
            )
            start += length
    expected = torch.cat(pieces, dim=2)
    assert_parity(actual, expected, dtype, "logits", "packed attention")
    upstream = torch.randn_like(actual)
    actual.backward(upstream)
    expected.backward(upstream)
    for tensor, reference in zip(inputs, references, strict=True):
        assert_parity(tensor.grad, reference.grad, dtype, "gradients", "packed attention")


@pytest.mark.parametrize("dtype", [torch.float16, torch.bfloat16])
@pytest.mark.parametrize("kv_heads", [4, 2, 1], ids=["mha", "gqa", "mqa"])
@pytest.mark.parametrize("padding", ["none", "left", "right"])
def test_fa4_huggingface_training_parity(
    config: Olmo2Config, cuda_device: torch.device, dtype: torch.dtype, kv_heads: int, padding: str
) -> None:
    olmo2_training_parity(
        replace(
            config,
            hidden_size=256,
            intermediate_size=384,
            num_key_value_heads=kv_heads,
            attn_implementation="flash_attention_4",
        ),
        padding,
        cuda_device,
        dtype,
    )
    assert attention.current_flash_attention_impl() == "FA4"
