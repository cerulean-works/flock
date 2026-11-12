"""CUDA counterparts are deliberately outside the normal CPU test selection."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
import torch
from torch.nn.attention import SDPBackend, sdpa_kernel

from tests.training._reference import (
    check_gqa_parity,
    check_swiglu_parity,
    olmo2_training_parity,
)

if TYPE_CHECKING:
    from flock.training.core.models.olmo2 import Olmo2Config


pytestmark = pytest.mark.gpu


@pytest.mark.parametrize("dtype", [torch.float32, torch.float16, torch.bfloat16])
@pytest.mark.parametrize("kv_heads", [4, 2, 1], ids=["mha", "gqa", "mqa"])
@pytest.mark.parametrize("padding", ["none", "left", "right"])
def test_cuda_olmo2_huggingface_training_parity(
    config: Olmo2Config, cuda_device: torch.device, dtype: torch.dtype, kv_heads: int, padding: str
) -> None:
    # Math SDPA keeps this numerical oracle independent of fused-kernel selection.
    # Fused attention has a separate explicit counterpart below.
    with sdpa_kernel(SDPBackend.MATH):
        olmo2_training_parity(
            replace(config, num_key_value_heads=kv_heads), padding, cuda_device, dtype
        )


@pytest.mark.parametrize("dtype", [torch.float32, torch.float16, torch.bfloat16])
@pytest.mark.parametrize("kv_heads", [4, 2, 1], ids=["mha", "gqa", "mqa"])
@pytest.mark.parametrize("padding", ["left", "right"])
def test_cuda_padded_olmo2_default_sdpa_training_parity(
    config: Olmo2Config, cuda_device: torch.device, dtype: torch.dtype, kv_heads: int, padding: str
) -> None:
    # Exercise normal SDPA dispatch with the model's boolean padding/causal mask,
    # including fully masked query rows from left padding.
    olmo2_training_parity(
        replace(config, num_key_value_heads=kv_heads), padding, cuda_device, dtype
    )


@pytest.mark.parametrize("dtype", [torch.float32, torch.float16, torch.bfloat16])
def test_cuda_swiglu_huggingface_parity(
    config: Olmo2Config, cuda_device: torch.device, dtype: torch.dtype
) -> None:
    check_swiglu_parity(config, cuda_device, dtype)


@pytest.mark.parametrize("dtype", [torch.float16, torch.bfloat16])
@pytest.mark.parametrize("kv_heads", [4, 2, 1])
def test_cuda_flash_attention_counterpart(
    cuda_device: torch.device, dtype: torch.dtype, kv_heads: int
) -> None:
    # Require a real fused execution, rather than allowing an unnoticed fallback.
    with sdpa_kernel(SDPBackend.FLASH_ATTENTION):
        check_gqa_parity(kv_heads, cuda_device, dtype, causal=True)
