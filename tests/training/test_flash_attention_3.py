"""CPU checks for mask adaptation and lifecycle; these do not execute FA3 kernels."""

import importlib
from dataclasses import replace
from unittest.mock import Mock

import pytest
import torch
from torch._subclasses.fake_tensor import FakeTensorMode
from torch.nn import attention
from torch.nn import functional as F

from flock.training.core.attention import (
    flash_attention_3,
    prepare_flash_attention_mask,
    sdpa_attention,
)
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
        lambda device: attention.sdpa_kernel(attention.SDPBackend.MATH),
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

    attention_mask = prepare_flash_attention_mask(mask, is_causal=is_causal)
    actual = flash_attention_3(*inputs, attention_mask=attention_mask, is_causal=is_causal)
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


@pytest.mark.parametrize("kv_heads", [4, 2, 1])
def test_model_preserves_padding_logits_and_gradients(math_kernel, config, kv_heads):
    model = Olmo2(
        replace(config, attn_implementation="flash_attention_3", num_key_value_heads=kv_heads)
    )
    reference = Olmo2(replace(config, num_key_value_heads=kv_heads))
    batch = make_batch(
        torch.tensor([[0, 0, 0, 0], [0, 2, 3, 0], [4, 5, 0, 0], [0, 6, 0, 0]]),
        torch.tensor([[0, 0, 0, 0], [0, 1, 1, 0], [1, 1, 0, 0], [0, 1, 0, 0]]),
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


@pytest.mark.parametrize("backend", ["sdpa", "flash_attention_3"])
@pytest.mark.parametrize("padding", [False, True])
def test_model_future_tokens_cannot_change_earlier_logits(math_kernel, config, backend, padding):
    model = Olmo2(replace(config, attn_implementation=backend)).eval()
    ids = torch.tensor([[2, 3, 4, 5]])
    mask = torch.ones_like(ids)
    if padding:
        ids = F.pad(ids, (1, 1))
        mask = F.pad(mask, (1, 1))
    changed = ids.clone()
    start = int(padding)
    changed[:, start + 2 : start + 4] = torch.tensor([20, 21])
    with torch.no_grad():
        actual = model(make_batch(ids, mask))
        other = model(make_batch(changed, mask))
    torch.testing.assert_close(actual[:, start : start + 2], other[:, start : start + 2])


def test_unpadded_model_uses_one_batched_fa3_call_per_layer(math_kernel, config, monkeypatch):
    compute = Mock(wraps=sdpa_attention)
    monkeypatch.setattr(fa3_module, "sdpa_attention", compute)
    model = Olmo2(replace(config, attn_implementation="flash_attention_3"))
    ids = torch.tensor([[2, 3, 4, 5], [6, 7, 8, 9]])
    model(make_batch(ids))
    assert compute.call_count == config.num_hidden_layers
    for call in compute.call_args_list:
        assert call.args[0].shape[0] == ids.shape[0]
        assert call.kwargs.get("attention_mask") is None
        assert call.kwargs["is_causal"] is True


@pytest.mark.parametrize("num_layers", [2, 4])
def test_model_prepares_padding_before_layers_once_per_forward(
    math_kernel, config, monkeypatch, num_layers
):
    config = replace(config, num_hidden_layers=num_layers)
    model = Olmo2(replace(config, attn_implementation="flash_attention_3"))
    reference = Olmo2(config)
    ids = torch.tensor([[0, 2, 3, 0], [4, 5, 6, 7]])
    mask = torch.zeros_like(ids, dtype=torch.bool)
    batch = make_batch(ids, mask)
    host_reads = []
    received = []
    original_equal = torch.equal
    original_cpu = torch.Tensor.cpu
    original_item = torch.Tensor.item

    def equal(*args, **kwargs):
        host_reads.append("equal")
        return original_equal(*args, **kwargs)

    def cpu(tensor, *args, **kwargs):
        host_reads.append("cpu")
        return original_cpu(tensor, *args, **kwargs)

    def item(tensor):
        host_reads.append("item")
        return original_item(tensor)

    def record_padding(module, args):
        # Validation and transfer must finish before the first decoder layer.
        assert host_reads == ["item", "equal", "cpu"]
        received.append(args[-1])

    for layer in model.model.layers:
        layer.register_forward_pre_hook(record_padding)

    previous = None
    for rows, expected_spans in (
        ([[0, 1, 1, 0], [0, 0, 0, 0]], ((1, 3), (4, 4))),
        ([[1, 1, 0, 0], [0, 0, 1, 1]], ((0, 2), (2, 4))),
    ):
        # Reusing and mutating the same input mask must not reuse stale metadata.
        mask.copy_(torch.tensor(rows, dtype=torch.bool))
        host_reads.clear()
        received.clear()
        with monkeypatch.context() as patch:
            patch.setattr(torch, "equal", equal)
            patch.setattr(torch.Tensor, "cpu", cpu)
            patch.setattr(torch.Tensor, "item", item)
            actual = model(batch)

        assert host_reads == ["item", "equal", "cpu"]
        assert len(received) == num_layers
        prepared = received[0]
        assert isinstance(prepared, torch.Tensor)
        assert all(padding is prepared for padding in received)
        torch.testing.assert_close(prepared, torch.tensor(expected_spans))
        assert prepared is not previous
        previous = prepared
        torch.testing.assert_close(actual, reference(batch))


def test_model_rejects_noncontiguous_padding_before_decoder_layers(config, monkeypatch):
    model = Olmo2(replace(config, attn_implementation="flash_attention_3"))
    layer_forward = Mock(side_effect=AssertionError("invalid padding reached a decoder layer"))
    monkeypatch.setattr(model.model.layers[0], "forward", layer_forward)
    batch = make_batch(torch.tensor([[2, 0, 3, 0]]), torch.tensor([[1, 0, 1, 0]]))
    with pytest.raises(ValueError, match="contiguous"):
        model(batch)
    layer_forward.assert_not_called()


@pytest.mark.parametrize(
    "spans",
    [
        torch.tensor([[1, 3], [0, 0]]),
        torch.tensor([[1, 3, 4]]),
        torch.tensor([[1.0, 3.0]]),
        torch.tensor([[False, True, True, False]]),
        torch.empty(1, 2, dtype=torch.int64, device="meta"),
    ],
)
def test_fa3_requires_prepared_cpu_spans(spans):
    q = torch.randn(1, 2, 4, 8)
    with pytest.raises(ValueError, match="prepared CPU int64"):
        flash_attention_3(q, q, q, attention_mask=spans)


@pytest.mark.parametrize("span", [[-1, 2], [3, 2], [1, 5]])
def test_prepared_spans_must_fit_query(math_kernel, span):
    q = torch.randn(1, 2, 4, 8)
    with pytest.raises(ValueError, match="sequence length"):
        flash_attention_3(q, q, q, attention_mask=torch.tensor([span]))


@pytest.mark.parametrize(
    "mask, message",
    [
        (torch.ones(1, 1, 4, 4, dtype=torch.bool), "boolean"),
        (torch.ones(1, 4), "boolean"),
        (torch.tensor([[True, False, True, False]]), "contiguous"),
        (torch.tensor([[False, True, False, True]]), "contiguous"),
        (
            torch.tensor([[True, True, False, False], [False, True, False, True]]),
            "contiguous",
        ),
    ],
)
def test_preparer_rejects_unsupported_padding(mask, message):
    with pytest.raises(ValueError, match=message):
        prepare_flash_attention_mask(mask, is_causal=True)


def test_rejects_non_square_attention():
    q = torch.randn(1, 2, 4, 8)
    with pytest.raises(ValueError, match="full-sequence"):
        flash_attention_3(q[:, :, :1], q, q, is_causal=True)


def test_cpu_request_does_not_fall_back():
    q = torch.randn(1, 2, 4, 8)
    with pytest.raises(ValueError, match="CUDA"):
        flash_attention_3(q, q, q)


def test_non_hopper_request_does_not_fall_back(monkeypatch):
    monkeypatch.setattr(torch.cuda, "get_device_capability", lambda device: (8, 0))
    with (
        pytest.raises(ValueError, match="Hopper"),
        fa3_module._fa3_context(torch.device("cuda")),
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


@pytest.fixture
def fake_cuda(monkeypatch):
    # Exercise CUDA autocast's dtype rules without a CUDA runtime or kernels.
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "is_bf16_supported", lambda: True)
    with FakeTensorMode():
        yield


@pytest.mark.parametrize("dtype", [torch.float16, torch.bfloat16])
@pytest.mark.parametrize("value_dtype", [torch.float32, torch.float16, torch.bfloat16])
def test_sdpa_handles_cuda_autocast_dtypes(fake_cuda, math_kernel, monkeypatch, dtype, value_dtype):
    # Exercise real SDPA autocast dispatch without requiring CUDA kernels.
    compute = Mock(wraps=sdpa_attention)
    monkeypatch.setattr(fa3_module, "sdpa_attention", compute)
    q = torch.empty(2, 4, 5, 8, device="cuda:0", dtype=torch.float32)
    k = torch.empty(2, 2, 5, 8, device="cuda:0", dtype=torch.float32)
    v = torch.empty_like(k, dtype=value_dtype)

    with torch.autocast("cuda", dtype=dtype):
        output = flash_attention_3(q, k, v, is_causal=True)

    assert output.shape == q.shape
    assert output.dtype == dtype
    compute.assert_called_once()
    assert all(
        actual is expected
        for actual, expected in zip(compute.call_args.args, (q, k, v), strict=True)
    )
    assert compute.call_args.kwargs["is_causal"] is True
    assert q.dtype == k.dtype == torch.float32
    assert v.dtype == value_dtype


@pytest.mark.parametrize("autocast_device", ["cpu", "cuda"])
def test_mixed_dtypes_require_enabled_cuda_autocast(fake_cuda, mock_activation, autocast_device):
    q = torch.empty(1, 2, 4, 8, device="cuda:0")
    v = torch.empty_like(q, dtype=torch.bfloat16)
    with (
        torch.autocast(autocast_device, dtype=torch.bfloat16),
        torch.autocast("cuda", enabled=False),
        pytest.raises(RuntimeError, match="same dtype"),
    ):
        flash_attention_3(q, q, v)


def test_activation_survives_forward_and_only_runs_once(mock_activation):
    state, activate = mock_activation
    with attention.sdpa_kernel(attention.SDPBackend.MATH):
        for _ in range(2):
            with fa3_module._fa3_context(torch.device("cuda")):
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
        fa3_module._fa3_context(torch.device("cuda")),
    ):
        pytest.fail("conflicting implementation was replaced")
    activate.assert_not_called()


def test_missing_kernel_dependency_has_actionable_error(mock_activation):
    _, activate = mock_activation
    activate.side_effect = ModuleNotFoundError("flash_attn_interface")
    with (
        pytest.raises(ImportError, match="installing torch alone"),
        fa3_module._fa3_context(torch.device("cuda")),
    ):
        pytest.fail("missing dependency was ignored")
