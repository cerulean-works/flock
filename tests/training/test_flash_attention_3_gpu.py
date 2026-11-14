"""Explicit FA3 checks: require a Hopper GPU and compatible external FA3 kernels."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
import torch
from torch.nn import attention

from flock.training.core.attention import (
    flash_attention_3,
    prepare_flash_attention_mask,
    prepare_sdpa_mask,
    sdpa_attention,
)
from flock.training.core.models.olmo2 import Olmo2
from flock.training.core.trainer import Trainer
from tests.training._reference import assert_parity, olmo2_training_parity, token_batch

pytestmark = [pytest.mark.gpu, pytest.mark.fa3]


@pytest.mark.parametrize("dtype", [torch.float16, torch.bfloat16])
@pytest.mark.parametrize("kv_heads", [4, 2, 1], ids=["mha", "gqa", "mqa"])
@pytest.mark.parametrize("is_causal", [False, True])
def test_varlen_mixed_padding_outputs_and_gradients(
    cuda_device, dtype, kv_heads, is_causal, monkeypatch
):
    # Lengths >128 also exercise inputs eligible for PyTorch 2.14's cuDNN path.
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
    assert spans is not None and spans.device.type == "cpu"
    with attention.sdpa_kernel(attention.SDPBackend.MATH):
        expected = sdpa_attention(
            *references, attention_mask=prepare_sdpa_mask(mask, is_causal=is_causal)
        ).masked_fill(~mask[:, None, :, None], 0)
    previous = attention.current_flash_attention_impl()
    assert previous in (None, "FA3")
    cudnn = Mock(side_effect=AssertionError("FA3 varlen must not dispatch to cuDNN"))
    monkeypatch.setattr(torch.ops.aten, "_cudnn_attention_forward", cudnn)
    monkeypatch.setattr(torch.ops.aten, "_cudnn_attention_backward", cudnn)
    try:
        actual = flash_attention_3(*inputs, attention_mask=spans, is_causal=is_causal)
        assert_parity(actual, expected, dtype, "logits", "packed attention")
        upstream = torch.randn_like(actual)
        actual.backward(upstream)
        expected.backward(upstream)
        for tensor, reference in zip(inputs, references, strict=True):
            assert_parity(tensor.grad, reference.grad, dtype, "gradients", "packed attention")
        cudnn.assert_not_called()
    finally:
        if previous is None and attention.current_flash_attention_impl() == "FA3":
            attention.restore_flash_attention_impl()


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


@pytest.mark.parametrize("dtype", [torch.float16, torch.bfloat16])
@pytest.mark.parametrize("kv_heads", [4, 2, 1], ids=["mha", "gqa", "mqa"])
@pytest.mark.parametrize("padding", ["none", "left", "right"])
def test_fa3_fp32_weights_with_cuda_autocast(config, cuda_device, dtype, kv_heads, padding):
    config = replace(config, hidden_size=256, intermediate_size=384, num_key_value_heads=kv_heads)
    # Match the trainer: master weights stay FP32; only forward runs under autocast.
    model = Olmo2(replace(config, attn_implementation="flash_attention_3")).to(cuda_device)
    reference = Olmo2(replace(config, attn_implementation="sdpa")).to(cuda_device)
    reference.load_state_dict(model.state_dict(), strict=True)
    ids, mask = token_batch(padding, cuda_device)
    labels = ids[:, 1:].clone()
    labels.masked_fill_(~(mask[:, :-1] & mask[:, 1:]), -100)
    batch = {"input_ids": ids[:, :-1], "attention_mask": mask[:, :-1], "labels": labels}

    previous = attention.current_flash_attention_impl()
    assert previous in (None, "FA3"), "run FA3 checks without another custom flash implementation"
    try:
        with torch.autocast("cuda", dtype=dtype):
            with attention.sdpa_kernel(attention.SDPBackend.MATH):
                expected = reference(batch)
            actual = model(batch)
            loss, _ = Trainer._calc_batch_loss(object.__new__(Trainer), actual, labels)
            expected_loss, _ = Trainer._calc_batch_loss(object.__new__(Trainer), expected, labels)
        assert actual.dtype == dtype
        valid = mask[:, :-1]
        assert_parity(actual[valid], expected[valid], dtype, "logits", "autocast logits")
        assert_parity(loss, expected_loss, dtype, "logits", "autocast loss")
        loss.backward()
        expected_loss.backward()
        for name, parameter in model.named_parameters():
            assert parameter.dtype == torch.float32, name
            assert parameter.grad is not None, name
            assert parameter.grad.dtype == torch.float32, name
            assert parameter.grad.isfinite().all(), name
            assert_parity(
                parameter.grad, reference.get_parameter(name).grad, dtype, "gradients", name
            )
        assert attention.current_flash_attention_impl() == "FA3"
    finally:
        if previous is None and attention.current_flash_attention_impl() == "FA3":
            attention.restore_flash_attention_impl()
