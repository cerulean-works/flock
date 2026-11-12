"""Explicit FA3 checks: require a Hopper GPU and compatible external FA3 kernels."""

from dataclasses import replace

import pytest
import torch
from torch.nn import attention

from tests.training._reference import olmo2_training_parity

pytestmark = [pytest.mark.gpu, pytest.mark.fa3]


@pytest.mark.parametrize("dtype", [torch.float16, torch.bfloat16])
@pytest.mark.parametrize("kv_heads", [4, 2, 1], ids=["mha", "gqa", "mqa"])
@pytest.mark.parametrize("padding", ["none", "left", "right"])
def test_fa3_huggingface_training_parity(config, cuda_device, dtype, kv_heads, padding):
    previous = attention.current_flash_attention_impl()
    assert previous in (None, "FA3"), "run FA3 checks without another custom flash implementation"
    try:
        olmo2_training_parity(
            replace(
                config,
                hidden_size=256,
                intermediate_size=384,
                num_key_value_heads=kv_heads,
                attn_implementation="flash_attention_3",
            ),
            padding,
            cuda_device,
            dtype,
        )
        assert attention.current_flash_attention_impl() == "FA3"
    finally:
        # All backward work has finished; leave unrelated GPU tests' dispatch intact.
        if previous is None and attention.current_flash_attention_impl() == "FA3":
            attention.restore_flash_attention_impl()
