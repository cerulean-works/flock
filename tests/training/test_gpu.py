"""CUDA counterparts are deliberately outside the normal CPU test selection."""

from dataclasses import replace

import pytest
import torch
from torch.nn.attention import SDPBackend, sdpa_kernel

from tests.training._reference import (
    check_gqa_parity,
    check_swiglu_parity,
    olmo2_training_parity,
)

pytestmark = pytest.mark.gpu


@pytest.mark.parametrize("dtype", [torch.float32, torch.float16, torch.bfloat16])
@pytest.mark.parametrize("kv_heads", [4, 2, 1], ids=["mha", "gqa", "mqa"])
@pytest.mark.parametrize("padding", ["none", "left", "right"])
def test_cuda_olmo2_huggingface_training_parity(config, cuda_device, dtype, kv_heads, padding):
    # Math SDPA keeps this numerical oracle independent of fused-kernel selection.
    # Fused attention has a separate explicit counterpart below.
    with sdpa_kernel(SDPBackend.MATH):
        olmo2_training_parity(
            replace(config, num_key_value_heads=kv_heads), padding, cuda_device, dtype
        )


@pytest.mark.parametrize("dtype", [torch.float32, torch.float16, torch.bfloat16])
def test_cuda_swiglu_huggingface_parity(config, cuda_device, dtype):
    check_swiglu_parity(config, cuda_device, dtype)


@pytest.mark.parametrize("dtype", [torch.float16, torch.bfloat16])
@pytest.mark.parametrize("kv_heads", [4, 2, 1])
def test_cuda_flash_attention_counterpart(cuda_device, dtype, kv_heads):
    # Require a real fused execution, rather than allowing an unnoticed fallback.
    with sdpa_kernel(SDPBackend.FLASH_ATTENTION):
        check_gqa_parity(kv_heads, cuda_device, dtype, causal=True)
