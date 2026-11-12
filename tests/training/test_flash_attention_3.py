"""CPU checks for mask adaptation and lifecycle; these do not execute FA3 kernels."""

import importlib
from dataclasses import replace
from unittest.mock import Mock

import pytest
import torch
from torch.nn import attention

from flock.training.core.attention import flash_attention_3, sdpa_attention
from flock.training.core.models.olmo2 import Olmo2
from tests.training._reference import olmo2_training_parity
from tests.training.test_olmo2 import make_batch

fa3_module = importlib.import_module("flock.training.core.attention.flash_attention_3")


@pytest.fixture
def math_kernel(monkeypatch):
    # Only substitute device/backend selection. Exercise the real padding adapter.
    monkeypatch.setattr(
        fa3_module,
        "_fa3_context",
        lambda device, dtype: attention.sdpa_kernel(attention.SDPBackend.MATH),
    )


@pytest.mark.parametrize("kv_heads", [4, 2, 1])
@pytest.mark.parametrize("is_causal", [False, True])
@pytest.mark.parametrize("padding", ["mixed", "none", "empty"])
def test_padding_adapter_outputs_and_gradients(math_kernel, kv_heads, is_causal, padding):
    # Include left, right, both-sided padding, one token, and fully masked rows.
    mask = torch.tensor(
        [[0, 0, 1, 1, 1], [1, 1, 1, 0, 0], [0, 1, 1, 0, 0], [0, 0, 1, 0, 0], [0, 0, 0, 0, 0]],
        dtype=torch.bool,
    )
    if padding == "none":
        mask.fill_(True)
    elif padding == "empty":
        mask.fill_(False)
    inputs = [
        torch.randn(5, 5, heads, 8).transpose(1, 2).requires_grad_()
        for heads in (4, kv_heads, kv_heads)
    ]
    reference_inputs = [tensor.detach().clone().requires_grad_() for tensor in inputs]
    dense_mask = mask[:, None, None, :].expand(-1, 1, 5, -1)
    if is_causal:
        dense_mask = dense_mask & torch.ones(5, 5, dtype=torch.bool).tril()

    actual = flash_attention_3(*inputs, attention_mask=mask, is_causal=is_causal)
    with attention.sdpa_kernel(attention.SDPBackend.MATH):
        expected = sdpa_attention(*reference_inputs, attention_mask=dense_mask)
    torch.testing.assert_close(actual, expected)
    upstream = torch.randn_like(actual)
    actual.backward(upstream)
    expected.backward(upstream)
    for tensor, reference in zip(inputs, reference_inputs, strict=True):
        assert tensor.grad is not None
        torch.testing.assert_close(tensor.grad, reference.grad, atol=2e-6, rtol=2e-5)


@pytest.mark.parametrize("kv_heads", [4, 2, 1])
@pytest.mark.parametrize("padding", ["none", "left", "right"])
def test_model_huggingface_training_parity(math_kernel, config, kv_heads, padding):
    olmo2_training_parity(
        replace(config, attn_implementation="flash_attention_3", num_key_value_heads=kv_heads),
        padding,
        torch.device("cpu"),
        torch.float32,
    )


def test_model_preserves_padding_logits_and_gradients(math_kernel, config):
    model = Olmo2(replace(config, attn_implementation="flash_attention_3", num_key_value_heads=2))
    reference = Olmo2(replace(config, num_key_value_heads=2))
    batch = make_batch(
        torch.tensor([[0, 0, 0, 0], [0, 2, 3, 0], [4, 5, 0, 0]]),
        torch.tensor([[0, 0, 0, 0], [0, 1, 1, 0], [1, 1, 0, 0]]),
    )
    actual, expected = model(batch), reference(batch)
    torch.testing.assert_close(actual, expected)
    upstream = torch.randn_like(actual)
    actual.backward(upstream)
    expected.backward(upstream)
    for name, parameter in model.named_parameters():
        torch.testing.assert_close(
            parameter.grad, reference.get_parameter(name).grad, atol=2e-5, rtol=2e-4
        )


@pytest.mark.parametrize(
    "mask, message",
    [
        (torch.ones(1, 1, 4, 4, dtype=torch.bool), "boolean"),
        (torch.ones(1, 4), "boolean"),
        (torch.tensor([[True, False, True, False]]), "contiguous"),
    ],
)
def test_unsupported_masks_raise_before_activation(mask, message):
    q = torch.randn(1, 2, 4, 8)
    with pytest.raises(ValueError, match=message):
        flash_attention_3(q, q, q, attention_mask=mask, is_causal=True)


def test_rejects_non_square_attention():
    q = torch.randn(1, 2, 4, 8)
    with pytest.raises(ValueError, match="full-sequence"):
        flash_attention_3(q[:, :, :1], q, q, is_causal=True)


def test_cpu_request_does_not_fall_back():
    q = torch.randn(1, 2, 4, 8)
    with pytest.raises(ValueError, match="CUDA"):
        flash_attention_3(q, q, q)


def test_fp32_request_does_not_fall_back():
    with (
        pytest.raises(ValueError, match="float16 or bfloat16"),
        fa3_module._fa3_context(torch.device("cuda"), torch.float32),
    ):
        pytest.fail("unsupported dtype was accepted")


def test_non_hopper_request_does_not_fall_back(monkeypatch):
    monkeypatch.setattr(torch.cuda, "get_device_capability", lambda device: (8, 0))
    with (
        pytest.raises(ValueError, match="Hopper"),
        fa3_module._fa3_context(torch.device("cuda"), torch.bfloat16),
    ):
        pytest.fail("unsupported GPU was accepted")


@pytest.fixture
def mock_activation(monkeypatch):
    state = {"active": None}
    activate = Mock(side_effect=lambda name: state.update(active=name))
    monkeypatch.setattr(torch.cuda, "get_device_capability", lambda device: (9, 0))
    monkeypatch.setattr(attention, "current_flash_attention_impl", lambda: state["active"])
    monkeypatch.setattr(attention, "activate_flash_attention_impl", activate)
    return state, activate


def test_activation_survives_forward_and_only_runs_once(mock_activation):
    state, activate = mock_activation
    with attention.sdpa_kernel(attention.SDPBackend.MATH):
        for _ in range(2):
            with fa3_module._fa3_context(torch.device("cuda"), torch.bfloat16):
                assert torch.backends.cuda.flash_sdp_enabled()
                assert not torch.backends.cuda.math_sdp_enabled()
                assert not torch.backends.cuda.mem_efficient_sdp_enabled()
                assert not torch.backends.cuda.cudnn_sdp_enabled()
            # Backward occurs after this point and must still use FA3 registration.
            assert state["active"] == "FA3"
            assert torch.backends.cuda.math_sdp_enabled()
            assert not torch.backends.cuda.flash_sdp_enabled()
    activate.assert_called_once_with("FA3")


def test_does_not_replace_another_active_implementation(mock_activation):
    state, activate = mock_activation
    state["active"] = "FA4"
    with (
        pytest.raises(RuntimeError, match="FA4"),
        fa3_module._fa3_context(torch.device("cuda"), torch.bfloat16),
    ):
        pytest.fail("conflicting implementation was replaced")
    activate.assert_not_called()


def test_missing_kernel_dependency_has_actionable_error(mock_activation):
    _, activate = mock_activation
    activate.side_effect = ModuleNotFoundError("flash_attn_interface")
    with (
        pytest.raises(ImportError, match="installing torch alone"),
        fa3_module._fa3_context(torch.device("cuda"), torch.bfloat16),
    ):
        pytest.fail("missing dependency was ignored")
