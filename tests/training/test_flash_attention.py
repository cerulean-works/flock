"""CPU checks for mask adaptation and lifecycle; these do not execute FA3 or FA4 kernels."""

import importlib
from collections.abc import Callable, Iterator
from dataclasses import replace
from itertools import pairwise
from typing import Any, NamedTuple
from unittest.mock import Mock

import pytest
import torch
from torch._subclasses.fake_tensor import FakeTensorMode
from torch.nn import attention
from torch.nn import functional as F

from flock.training.core.attention import (
    AttentionInterface,
    VarlenMetadata,
    flash_attention_3,
    flash_attention_4,
    prepare_packed_varlen_metadata,
    prepare_varlen_metadata,
    sdpa_attention,
)
from flock.training.core.models.olmo2 import Olmo2, Olmo2Config
from tests.training._reference import olmo2_training_parity
from tests.training.test_olmo2 import (
    assert_packed_matches_documents_alone,
    assert_reads_nothing_back,
    make_batch,
)

flash_module = importlib.import_module("flock.training.core.attention.flash_attention")


class Flash(NamedTuple):
    backend: str
    attend: Callable[..., torch.Tensor]
    implementation: Any
    # PyTorch's implementation name and the compute capability majors it accepts.
    name: str
    majors: frozenset[int]


FLASH_BACKENDS = [
    Flash("flash_attention_3", flash_attention_3, flash_module._FA3, "FA3", frozenset({9})),
    Flash("flash_attention_4", flash_attention_4, flash_module._FA4, "FA4", frozenset({9, 10})),
]


@pytest.fixture(params=FLASH_BACKENDS, ids=[flash.backend for flash in FLASH_BACKENDS])
def flash(request: pytest.FixtureRequest) -> Flash:
    return request.param


@pytest.fixture
def math_kernel(monkeypatch: pytest.MonkeyPatch) -> Mock:
    # Substitute GPU kernels; exercise the real packing and restoration.
    monkeypatch.setattr(
        flash_module,
        "_flash_context",
        lambda implementation, device: attention.sdpa_kernel(attention.SDPBackend.MATH),
    )
    compute = Mock(wraps=math_varlen)
    monkeypatch.setattr(flash_module, "varlen_attn", compute)
    return compute


def math_varlen(
    q: torch.Tensor,
    k: torch.Tensor,
    v: torch.Tensor,
    cu_q: torch.Tensor,
    cu_k: torch.Tensor,
    max_q: int,
    max_k: int,
    *,
    window_size: tuple[int, int],
    enable_gqa: bool,
    num_splits: int | None,
) -> torch.Tensor:
    # Independent dense block attention detects incorrect sequence boundaries.
    q_batch = torch.repeat_interleave(torch.arange(cu_q.numel() - 1), cu_q.diff())
    k_batch = torch.repeat_interleave(torch.arange(cu_k.numel() - 1), cu_k.diff())
    visible = q_batch[:, None] == k_batch[None, :]
    if window_size == (-1, 0):
        visible &= torch.arange(q.shape[0])[:, None] >= torch.arange(k.shape[0])[None, :]
    else:
        assert window_size[0] >= max_q - 1 and window_size[1] >= max_k - 1
    return (
        F.scaled_dot_product_attention(
            q.transpose(0, 1)[None],
            k.transpose(0, 1)[None],
            v.transpose(0, 1)[None],
            attn_mask=visible,
            enable_gqa=enable_gqa,
        )
        .squeeze(0)
        .transpose(0, 1)
    )


def test_backend_registers_its_function_with_varlen_preparer(flash: Flash) -> None:
    assert AttentionInterface.get_interface(flash.backend) is flash.attend
    assert AttentionInterface.get_mask_preparer(flash.backend) is prepare_varlen_metadata
    preparer = AttentionInterface.get_metadata_preparer(flash.backend)
    assert preparer.backend.packed_preparer is prepare_packed_varlen_metadata


@pytest.mark.parametrize("kv_heads", [4, 2, 1])
@pytest.mark.parametrize("is_causal", [False, True])
@pytest.mark.parametrize("padding", ["mixed", "none", "empty"])
def test_padding_adapter_outputs_and_gradients(
    math_kernel: Mock, flash: Flash, kv_heads: int, is_causal: bool, padding: str
) -> None:
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

    attention_metadata = prepare_varlen_metadata(mask, is_causal=is_causal)
    actual = flash.attend(*inputs, attention_metadata=attention_metadata, is_causal=is_causal)
    with attention.sdpa_kernel(attention.SDPBackend.MATH):
        expected = sdpa_attention(*reference_inputs, attention_metadata=dense_mask)
    expected = expected.masked_fill(~mask[:, None, :, None], 0)
    torch.testing.assert_close(actual, expected)
    assert math_kernel.call_count == (1 if padding == "mixed" else 0)
    upstream = torch.randn_like(actual)
    actual.backward(upstream)
    expected.backward(upstream)
    for tensor, reference in zip(inputs, reference_inputs, strict=True):
        assert tensor.grad is not None
        torch.testing.assert_close(tensor.grad, reference.grad, atol=2e-6, rtol=2e-5)


@pytest.mark.parametrize("kv_heads", [4, 2, 1])
@pytest.mark.parametrize("padding", ["none", "left", "right"])
def test_model_huggingface_training_parity(
    math_kernel: Mock, flash: Flash, config: Olmo2Config, kv_heads: int, padding: str
) -> None:
    olmo2_training_parity(
        replace(config, attn_implementation=flash.backend, num_key_value_heads=kv_heads),
        padding,
        torch.device("cpu"),
        torch.float32,
    )


@pytest.mark.parametrize("kv_heads", [4, 2, 1])
def test_model_preserves_real_token_logits_and_gradients(
    math_kernel: Mock, flash: Flash, config: Olmo2Config, kv_heads: int
) -> None:
    model = Olmo2(replace(config, attn_implementation=flash.backend, num_key_value_heads=kv_heads))
    reference = Olmo2(replace(config, num_key_value_heads=kv_heads))
    batch = make_batch(
        torch.tensor([[0, 0, 0, 0], [0, 2, 3, 0], [4, 5, 0, 0], [0, 6, 0, 0]]),
        torch.tensor([[0, 0, 0, 0], [0, 1, 1, 0], [1, 1, 0, 0], [0, 1, 0, 0]]),
    )
    actual, expected = model(batch), reference(batch)
    valid = batch["attention_mask"].bool()
    torch.testing.assert_close(actual[valid], expected[valid])
    upstream = torch.randn_like(actual).masked_fill(~valid[:, :, None], 0)
    actual.backward(upstream)
    expected.backward(upstream)
    for name, parameter in model.named_parameters():
        torch.testing.assert_close(
            parameter.grad, reference.get_parameter(name).grad, atol=2e-5, rtol=2e-4
        )


@pytest.mark.parametrize("backend", ["sdpa", "flash_attention_3", "flash_attention_4"])
@pytest.mark.parametrize("padding", [False, True])
def test_model_future_tokens_cannot_change_earlier_logits(
    math_kernel: Mock, config: Olmo2Config, backend: str, padding: bool
) -> None:
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


def test_unpadded_model_uses_one_batched_flash_call_per_layer(
    math_kernel: Mock, flash: Flash, config: Olmo2Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    compute = Mock(wraps=sdpa_attention)
    monkeypatch.setattr(flash_module, "sdpa_attention", compute)
    model = Olmo2(replace(config, attn_implementation=flash.backend))
    ids = torch.tensor([[2, 3, 4, 5], [6, 7, 8, 9]])
    model(make_batch(ids))
    assert compute.call_count == config.num_hidden_layers
    for call in compute.call_args_list:
        assert call.args[0].shape[0] == ids.shape[0]
        assert call.kwargs.get("attention_metadata") is None
        assert call.kwargs["is_causal"] is True


@pytest.mark.parametrize("num_layers", [2, 4])
def test_model_prepares_packing_before_layers_once_per_forward(
    math_kernel: Mock,
    flash: Flash,
    config: Olmo2Config,
    monkeypatch: pytest.MonkeyPatch,
    num_layers: int,
) -> None:
    config = replace(config, num_hidden_layers=num_layers)
    model = Olmo2(replace(config, attn_implementation=flash.backend))
    reference = Olmo2(config)
    ids = torch.tensor([[0, 2, 3, 0], [4, 5, 6, 7]])
    mask = torch.zeros_like(ids, dtype=torch.bool)
    batch = make_batch(ids, mask)
    host_reads = []
    received = []
    original_equal = torch.equal
    original_cpu = torch.Tensor.cpu
    original_item = torch.Tensor.item

    def equal(*args: Any, **kwargs: Any) -> bool:
        host_reads.append("equal")
        return original_equal(*args, **kwargs)

    def cpu(tensor: torch.Tensor, *args: Any, **kwargs: Any) -> torch.Tensor:
        host_reads.append("cpu")
        return original_cpu(tensor, *args, **kwargs)

    def item(tensor: torch.Tensor) -> bool | int | float | complex:
        host_reads.append("item")
        return original_item(tensor)

    def record_padding(module: torch.nn.Module, args: tuple[Any, ...]) -> None:
        # Original mask validation and its CPU copy precede the decoder loop.
        assert host_reads == ["item", "equal", "cpu"]
        received.append(args[-1])

    for layer in model.model.layers:
        layer.register_forward_pre_hook(record_padding)

    previous = None
    for rows, expected_indices, expected_cu_seqlens in (
        ([[0, 1, 1, 0], [0, 0, 0, 0]], [1, 2], [0, 2]),
        ([[1, 1, 0, 0], [0, 0, 1, 1]], [0, 1, 6, 7], [0, 2, 4]),
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
        assert isinstance(prepared, VarlenMetadata)
        # Every layer reuses the same metadata instead of rebuilding it.
        assert all(metadata is prepared for metadata in received)
        torch.testing.assert_close(prepared.indices, torch.tensor(expected_indices))
        torch.testing.assert_close(
            prepared.cu_seq_lens_q, torch.tensor(expected_cu_seqlens, dtype=torch.int32)
        )
        assert prepared.cu_seq_lens_k is prepared.cu_seq_lens_q
        assert prepared.max_length_q == prepared.max_length_k == 2
        assert prepared is not previous
        previous = prepared
        torch.testing.assert_close(actual[mask], reference(batch)[mask])


def test_padded_model_uses_one_varlen_call_per_layer(
    math_kernel: Mock, flash: Flash, config: Olmo2Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    dense = Mock(side_effect=AssertionError("padded input reached SDPA"))
    monkeypatch.setattr(flash_module, "sdpa_attention", dense)
    model = Olmo2(replace(config, attn_implementation=flash.backend))
    ids = torch.tensor([[0, 2, 3, 0], [4, 5, 6, 7], [0, 0, 0, 0]])
    model(make_batch(ids, ids != 0))
    assert math_kernel.call_count == config.num_hidden_layers
    for call in math_kernel.call_args_list:
        q, k, v, cu_q, cu_k, max_q, max_k = call.args
        assert q.shape == k.shape == v.shape == (6, config.num_attention_heads, 8)
        assert cu_q is cu_k
        torch.testing.assert_close(cu_q, torch.tensor([0, 2, 6], dtype=torch.int32))
        assert max_q == max_k == 4
        assert call.kwargs["num_splits"] is None
    dense.assert_not_called()


def test_model_rejects_noncontiguous_padding_before_decoder_layers(
    flash: Flash, config: Olmo2Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = Olmo2(replace(config, attn_implementation=flash.backend))
    layer_forward = Mock(side_effect=AssertionError("invalid padding reached a decoder layer"))
    monkeypatch.setattr(model.model.layers[0], "forward", layer_forward)
    batch = make_batch(torch.tensor([[2, 0, 3, 0]]), torch.tensor([[1, 0, 1, 0]]))
    with pytest.raises(ValueError, match="contiguous"):
        model(batch)
    layer_forward.assert_not_called()


def _prepared(rows: list[list[bool]]) -> VarlenMetadata:
    prepared = prepare_varlen_metadata(torch.tensor(rows), is_causal=True)
    assert prepared is not None
    return prepared


@pytest.mark.parametrize(
    "attention_metadata, q_shape",
    [
        # Raw spans and dense masks are not varlen metadata.
        (torch.tensor([[1, 3]]), (1, 2, 4, 8)),
        (torch.tensor([[False, True, True, False]]), (1, 2, 4, 8)),
        # Metadata built for another batch size or a longer sequence.
        (_prepared([[False, True, True, False]] * 2), (1, 2, 8, 8)),
        (_prepared([[True] * 8 + [False] * 8]), (1, 2, 8, 8)),
    ],
)
def test_requires_metadata_matching_q(
    math_kernel: Mock, flash: Flash, attention_metadata: Any, q_shape: tuple[int, ...]
) -> None:
    q = torch.randn(q_shape)
    with pytest.raises(ValueError, match=f"{flash.name} expects VarlenMetadata matching"):
        flash.attend(q, q, q, attention_metadata=attention_metadata)
    math_kernel.assert_not_called()


@pytest.mark.parametrize("field", ["indices", "cu_seq_lens_q", "cu_seq_lens_k"])
def test_requires_metadata_on_qs_device(flash: Flash, field: str) -> None:
    prepared = _prepared([[False, True, True, False]])
    moved = replace(prepared, **{field: getattr(prepared, field).to("meta")})
    q = torch.randn(1, 2, 4, 8)
    with pytest.raises(ValueError, match="VarlenMetadata matching"):
        flash.attend(q, q, q, attention_metadata=moved)


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
def test_preparer_rejects_unsupported_padding(mask: torch.Tensor, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        prepare_varlen_metadata(mask, is_causal=True)


def test_rejects_non_square_attention(flash: Flash) -> None:
    q = torch.randn(1, 2, 4, 8)
    with pytest.raises(ValueError, match="full-sequence"):
        flash.attend(q[:, :, :1], q, q, is_causal=True)


def test_cpu_request_does_not_fall_back(flash: Flash) -> None:
    q = torch.randn(1, 2, 4, 8)
    with pytest.raises(ValueError, match="CUDA"):
        flash.attend(q, q, q)


@pytest.fixture
def mock_activation(monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, str | None], Mock]:
    state = {"active": None}
    activate = Mock(side_effect=lambda name: state.update(active=name))
    monkeypatch.setattr(torch.cuda, "get_device_capability", lambda device: (9, 0))
    monkeypatch.setattr(attention, "current_flash_attention_impl", lambda: state["active"])
    monkeypatch.setattr(attention, "activate_flash_attention_impl", activate)
    return state, activate


@pytest.mark.parametrize(
    "capability",
    [(8, 0), (9, 0), (10, 0), (10, 3), (11, 0), (12, 0)],
    ids=["a100", "h100-h200", "b200-gb200", "b300-gb300", "sm110", "rtx-pro-6000-blackwell"],
)
def test_gpu_support_matches_pytorch_integration(
    flash: Flash,
    mock_activation: tuple[dict[str, str | None], Mock],
    monkeypatch: pytest.MonkeyPatch,
    capability: tuple[int, int],
) -> None:
    _, activate = mock_activation
    monkeypatch.setattr(torch.cuda, "get_device_capability", lambda device: capability)
    context = flash_module._flash_context(flash.implementation, torch.device("cuda"))
    major, minor = capability
    if major in flash.majors:
        with context:
            pass
        activate.assert_called_once_with(flash.name)
    else:
        # Unsupported GPUs raise instead of falling back to another kernel.
        with (
            pytest.raises(ValueError, match=rf"got compute capability {major}\.{minor}"),
            context,
        ):
            pytest.fail("unsupported GPU was accepted")
        activate.assert_not_called()


@pytest.fixture
def fake_cuda(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Exercise CUDA autocast's dtype rules without a CUDA runtime or kernels.
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "is_bf16_supported", lambda: True)
    with FakeTensorMode():
        yield


def test_public_function_activates_its_implementation(
    fake_cuda: None,
    flash: Flash,
    mock_activation: tuple[dict[str, str | None], Mock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, _ = mock_activation
    compute = Mock(side_effect=lambda q, k, v, **kwargs: q.clone())
    monkeypatch.setattr(flash_module, "sdpa_attention", compute)
    q = torch.empty(1, 2, 4, 8, device="cuda:0", dtype=torch.bfloat16)
    flash.attend(q, q, q, is_causal=True)
    compute.assert_called_once()
    assert state["active"] == flash.name


@pytest.mark.parametrize("dtype", [torch.float16, torch.bfloat16])
@pytest.mark.parametrize("value_dtype", [torch.float32, torch.float16, torch.bfloat16])
def test_sdpa_handles_cuda_autocast_dtypes(
    fake_cuda: None,
    math_kernel: Mock,
    flash: Flash,
    monkeypatch: pytest.MonkeyPatch,
    dtype: torch.dtype,
    value_dtype: torch.dtype,
) -> None:
    # Exercise real SDPA autocast dispatch without requiring CUDA kernels.
    compute = Mock(wraps=sdpa_attention)
    monkeypatch.setattr(flash_module, "sdpa_attention", compute)
    q = torch.empty(2, 4, 5, 8, device="cuda:0", dtype=torch.float32)
    k = torch.empty(2, 2, 5, 8, device="cuda:0", dtype=torch.float32)
    v = torch.empty_like(k, dtype=value_dtype)

    with torch.autocast("cuda", dtype=dtype):
        output = flash.attend(q, k, v, is_causal=True)

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
def test_mixed_dtypes_require_enabled_cuda_autocast(
    fake_cuda: None,
    mock_activation: tuple[dict[str, str | None], Mock],
    flash: Flash,
    autocast_device: str,
) -> None:
    q = torch.empty(1, 2, 4, 8, device="cuda:0")
    v = torch.empty_like(q, dtype=torch.bfloat16)
    with (
        torch.autocast(autocast_device, dtype=torch.bfloat16),
        torch.autocast("cuda", enabled=False),
        pytest.raises(RuntimeError, match="same dtype"),
    ):
        flash.attend(q, q, v)


@pytest.mark.parametrize("dtype", [torch.float16, torch.bfloat16])
@pytest.mark.parametrize("value_dtype", [torch.float32, torch.float16, torch.bfloat16])
@pytest.mark.parametrize("empty", [False, True])
def test_varlen_applies_cuda_autocast(
    math_kernel: Mock,
    flash: Flash,
    monkeypatch: pytest.MonkeyPatch,
    dtype: torch.dtype,
    value_dtype: torch.dtype,
    empty: bool,
) -> None:
    # Real CPU metadata exercises packing; emulate the CUDA autocast state and kernel.
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "is_bf16_supported", lambda: True)
    compute = Mock(side_effect=lambda q, *args, **kwargs: q.clone())
    monkeypatch.setattr(flash_module, "varlen_attn", compute)
    q = torch.randn(2, 4, 3, 8, requires_grad=True)
    k = torch.randn(2, 2, 3, 8, requires_grad=True)
    v = torch.randn(2, 2, 3, 8, dtype=value_dtype, requires_grad=True)
    rows = [[False] * 3] * 2 if empty else [[False, True, True], [True, False, False]]
    prepared = _prepared(rows)
    with torch.autocast("cuda", dtype=dtype):
        output = flash.attend(q, k, v, attention_metadata=prepared, is_causal=True)
    assert output.shape == q.shape
    assert output.dtype == dtype
    if empty:
        compute.assert_not_called()
        output.sum().backward()
        for tensor in (q, k, v):
            assert tensor.grad is not None
            torch.testing.assert_close(tensor.grad, torch.zeros_like(tensor))
    else:
        compute.assert_called_once()
        assert all(tensor.dtype == dtype for tensor in compute.call_args.args[:3])
        assert compute.call_args.kwargs["enable_gqa"] is True
        assert compute.call_args.kwargs["window_size"] == (-1, 0)


def test_activation_survives_forward_and_only_runs_once(
    flash: Flash, mock_activation: tuple[dict[str, str | None], Mock]
) -> None:
    state, activate = mock_activation
    with attention.sdpa_kernel(attention.SDPBackend.MATH):
        for _ in range(2):
            with flash_module._flash_context(flash.implementation, torch.device("cuda")):
                assert torch.backends.cuda.flash_sdp_enabled()
                assert not torch.backends.cuda.math_sdp_enabled()
                assert not torch.backends.cuda.mem_efficient_sdp_enabled()
                assert not torch.backends.cuda.cudnn_sdp_enabled()
            # Backward occurs after this point and must still use the registered kernels.
            assert state["active"] == flash.name
            assert torch.backends.cuda.math_sdp_enabled()
            assert not torch.backends.cuda.flash_sdp_enabled()
    activate.assert_called_once_with(flash.name)


def test_does_not_replace_another_active_implementation(
    flash: Flash, mock_activation: tuple[dict[str, str | None], Mock]
) -> None:
    state, activate = mock_activation
    other = "FA4" if flash.name == "FA3" else "FA3"
    state["active"] = other
    with (
        pytest.raises(RuntimeError, match=f"Cannot activate {flash.name} while '{other}'"),
        flash_module._flash_context(flash.implementation, torch.device("cuda")),
    ):
        pytest.fail("conflicting implementation was replaced")
    activate.assert_not_called()


def test_missing_kernel_dependency_has_actionable_error(
    flash: Flash, mock_activation: tuple[dict[str, str | None], Mock]
) -> None:
    _, activate = mock_activation
    activate.side_effect = ModuleNotFoundError("No module named 'flash_attn'")
    # The message names what to install, not only what is missing.
    kernels = {"FA3": "FlashAttention-3 installation", "FA4": "flash-attn-4 package"}[flash.name]
    with (
        pytest.raises(ImportError, match="installing torch alone") as error,
        flash_module._flash_context(flash.implementation, torch.device("cuda")),
    ):
        pytest.fail("missing dependency was ignored")
    assert kernels in str(error.value)


@pytest.mark.parametrize("kv_heads", [4, 2, 1])
@pytest.mark.parametrize("is_causal", [False, True])
def test_packed_documents_never_attend_across_boundaries(
    math_kernel: Mock, flash: Flash, kv_heads: int, is_causal: bool
) -> None:
    # Rows of 7: documents of 3, 2 and 2, then documents of 4 and 1 and padding of 2.
    batch, width = 2, 7
    cu = torch.tensor([0, 3, 5, 7, 11, 12, 14], dtype=torch.int32)
    inputs = [
        torch.randn(batch, width, heads, 8).transpose(1, 2).requires_grad_()
        for heads in (4, kv_heads, kv_heads)
    ]
    metadata = prepare_packed_varlen_metadata(
        cu, batch_size=batch, seq_len=width, is_causal=is_causal
    )
    assert metadata.indices is None
    assert metadata.max_length_q == metadata.max_length_k == width
    actual = flash.attend(*inputs, attention_metadata=metadata, is_causal=is_causal)

    # Oracle: run each document alone through SDPA's math kernel.
    flat = [tensor.detach().transpose(1, 2).flatten(0, 1) for tensor in inputs]
    expected = torch.empty_like(flat[0])
    for start, stop in pairwise(cu.tolist()):
        q, k, v = (tensor[start:stop].transpose(0, 1)[None] for tensor in flat)
        with attention.sdpa_kernel(attention.SDPBackend.MATH):
            output = F.scaled_dot_product_attention(
                q, k, v, is_causal=is_causal, enable_gqa=kv_heads != 4
            )
        expected[start:stop] = output[0].transpose(0, 1)
    expected = expected.unflatten(0, (batch, width)).transpose(1, 2)
    torch.testing.assert_close(actual, expected)
    assert math_kernel.call_count == 1
    call = math_kernel.call_args
    assert call.args[3] is cu and call.args[5] == width
    actual.sum().backward()
    assert all(tensor.grad is not None and tensor.grad.isfinite().all() for tensor in inputs)


@pytest.mark.parametrize("kv_heads", [4, 2, 1])
def test_packed_model_matches_documents_run_separately(
    math_kernel: Mock, flash: Flash, config: Olmo2Config, kv_heads: int
) -> None:
    model = Olmo2(replace(config, attn_implementation=flash.backend, num_key_value_heads=kv_heads))
    assert_packed_matches_documents_alone(model)
    # Only the packed forward runs the varlen kernel, once per layer; each document
    # alone is unpadded and takes the dense path.
    assert math_kernel.call_count == config.num_hidden_layers


def test_packed_model_reads_nothing_back_from_the_device(
    math_kernel: Mock, flash: Flash, config: Olmo2Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert_reads_nothing_back(
        Olmo2(replace(config, attn_implementation=flash.backend)), monkeypatch
    )
