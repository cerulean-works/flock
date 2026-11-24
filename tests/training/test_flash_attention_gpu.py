"""Explicit FA3 and FA4 checks: require a supported GPU and compatible external kernels.

FA3 needs a Hopper GPU and the `flash-attn-3` package. FA4 needs a Hopper or Blackwell
GPU and the `flash-attn-4` package. The `gpu` dependency group installs both. Select one
with `-m 'gpu and fa3'` or `-m 'gpu and fa4'`.
"""

import copy
from collections.abc import Callable
from dataclasses import replace
from typing import NamedTuple
from unittest.mock import Mock

import pytest
import torch
from torch.nn import attention

from flock.training.core.attention import (
    flash_attention_3,
    flash_attention_4,
    prepare_packed_varlen_metadata,
    prepare_sdpa_mask,
    prepare_varlen_metadata,
    sdpa_attention,
)
from flock.training.core.distributed import DistributedRuntime
from flock.training.core.models.olmo2 import Olmo2, Olmo2Config
from flock.training.core.trainer import Trainer
from tests.training._reference import assert_parity, olmo2_training_parity, token_batch
from tests.training.test_olmo2 import PACKED_ROWS, PACKED_WIDTH, make_batch, packed_batch

pytestmark = [pytest.mark.gpu]


class Flash(NamedTuple):
    backend: str
    attend: Callable[..., torch.Tensor]
    # PyTorch's implementation name.
    name: str


@pytest.fixture(
    params=[
        pytest.param(
            Flash("flash_attention_3", flash_attention_3, "FA3"),
            marks=pytest.mark.fa3,
            id="flash_attention_3",
        ),
        pytest.param(
            Flash("flash_attention_4", flash_attention_4, "FA4"),
            marks=pytest.mark.fa4,
            id="flash_attention_4",
        ),
    ]
)
def flash(request: pytest.FixtureRequest) -> Flash:
    return request.param


@pytest.mark.parametrize("dtype", [torch.float16, torch.bfloat16])
@pytest.mark.parametrize("kv_heads", [4, 2, 1], ids=["mha", "gqa", "mqa"])
@pytest.mark.parametrize("is_causal", [False, True])
def test_varlen_mixed_padding_outputs_and_gradients(
    flash: Flash,
    cuda_device: torch.device,
    dtype: torch.dtype,
    kv_heads: int,
    is_causal: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    fp32_references = [tensor.detach().float().requires_grad_() for tensor in inputs]
    prepared = prepare_varlen_metadata(mask, is_causal=is_causal)
    assert prepared is not None
    assert prepared.indices is not None
    assert prepared.indices.device.type == "cuda"
    assert prepared.cu_seq_lens_q.device.type == "cuda"
    dense_mask = prepare_sdpa_mask(mask, is_causal=is_causal)
    with attention.sdpa_kernel(attention.SDPBackend.MATH):
        expected, fp32_expected = (
            sdpa_attention(*tensors, attention_metadata=dense_mask).masked_fill(
                ~mask[:, None, :, None], 0
            )
            for tensors in (references, fp32_references)
        )
    previous = attention.current_flash_attention_impl()
    assert previous in (None, flash.name)
    cudnn = Mock(side_effect=AssertionError(f"{flash.name} varlen must not dispatch to cuDNN"))
    monkeypatch.setattr(torch.ops.aten, "_cudnn_attention_forward", cudnn)
    monkeypatch.setattr(torch.ops.aten, "_cudnn_attention_backward", cudnn)
    try:
        actual = flash.attend(*inputs, attention_metadata=prepared, is_causal=is_causal)
        assert_parity(actual, expected, dtype, "logits", "packed attention", fp32=fp32_expected)
        upstream = torch.randn_like(actual)
        actual.backward(upstream)
        expected.backward(upstream)
        fp32_expected.backward(upstream.float())
        for tensor, reference, fp32_reference in zip(
            inputs, references, fp32_references, strict=True
        ):
            assert_parity(
                tensor.grad,
                reference.grad,
                dtype,
                "gradients",
                "packed attention",
                fp32=fp32_reference.grad,
            )
        cudnn.assert_not_called()
    finally:
        if previous is None and attention.current_flash_attention_impl() == flash.name:
            attention.restore_flash_attention_impl()


@pytest.mark.parametrize("dtype", [torch.float16, torch.bfloat16])
@pytest.mark.parametrize("kv_heads", [4, 2, 1], ids=["mha", "gqa", "mqa"])
@pytest.mark.parametrize("padding", ["none", "left", "right"])
def test_huggingface_training_parity(
    flash: Flash,
    config: Olmo2Config,
    cuda_device: torch.device,
    dtype: torch.dtype,
    kv_heads: int,
    padding: str,
) -> None:
    previous = attention.current_flash_attention_impl()
    assert previous in (None, flash.name), (
        f"run {flash.name} checks without another custom flash implementation"
    )
    try:
        olmo2_training_parity(
            replace(
                config,
                hidden_size=256,
                intermediate_size=384,
                num_key_value_heads=kv_heads,
                attn_implementation=flash.backend,
            ),
            padding,
            cuda_device,
            dtype,
        )
        assert attention.current_flash_attention_impl() == flash.name
    finally:
        # All backward work has finished; leave unrelated GPU tests' dispatch intact.
        if previous is None and attention.current_flash_attention_impl() == flash.name:
            attention.restore_flash_attention_impl()


@pytest.mark.parametrize("dtype", [torch.float16, torch.bfloat16])
@pytest.mark.parametrize("kv_heads", [4, 2, 1], ids=["mha", "gqa", "mqa"])
@pytest.mark.parametrize("padding", ["none", "left", "right"])
def test_fp32_weights_with_cuda_autocast(
    flash: Flash,
    config: Olmo2Config,
    cuda_device: torch.device,
    dtype: torch.dtype,
    kv_heads: int,
    padding: str,
) -> None:
    config = replace(config, hidden_size=256, intermediate_size=384, num_key_value_heads=kv_heads)
    # Match the trainer: master weights stay FP32; only forward runs under autocast.
    model = Olmo2(replace(config, attn_implementation=flash.backend)).to(cuda_device)
    reference = Olmo2(replace(config, attn_implementation="sdpa")).to(cuda_device)
    reference.load_state_dict(model.state_dict(), strict=True)
    # The same FP32 weights run without autocast give the FP32 result.
    fp32_reference = copy.deepcopy(reference)
    ids, mask = token_batch(padding, cuda_device)
    labels = ids[:, 1:].clone()
    labels.masked_fill_(~(mask[:, :-1] & mask[:, 1:]), -100)
    batch = {
        "input_ids": ids[:, :-1],
        "attention_mask": mask[:, :-1],
        "labels": labels,
        "position_ids": (mask[:, :-1].long().cumsum(-1) - 1).clamp_min(0),
    }
    trainer = object.__new__(Trainer)
    trainer.runtime = DistributedRuntime(0, 0, 1, cuda_device)
    trainer.torch_compile = False

    previous = attention.current_flash_attention_impl()
    assert previous in (None, flash.name), (
        f"run {flash.name} checks without another custom flash implementation"
    )
    try:
        with torch.autocast("cuda", dtype=dtype):
            with attention.sdpa_kernel(attention.SDPBackend.MATH):
                expected = reference(batch)
            actual = model(batch)
            loss, _, _ = trainer._calc_batch_loss(actual, labels)
            expected_loss, _, _ = trainer._calc_batch_loss(expected, labels)
        with attention.sdpa_kernel(attention.SDPBackend.MATH):
            fp32_expected = fp32_reference(batch)
        fp32_loss, _, _ = trainer._calc_batch_loss(fp32_expected, labels)
        assert actual.dtype == dtype
        valid = mask[:, :-1]
        assert_parity(
            actual[valid],
            expected[valid],
            dtype,
            "logits",
            "autocast logits",
            fp32=fp32_expected[valid],
        )
        loss.backward()
        expected_loss.backward()
        fp32_loss.backward()
        for name, parameter in model.named_parameters():
            assert parameter.dtype == torch.float32, name
            assert parameter.grad is not None, name
            assert parameter.grad.dtype == torch.float32, name
            assert parameter.grad.isfinite().all(), name
            assert_parity(
                parameter.grad,
                reference.get_parameter(name).grad,
                dtype,
                "gradients",
                name,
                fp32=fp32_reference.get_parameter(name).grad,
            )
        assert attention.current_flash_attention_impl() == flash.name
    finally:
        if previous is None and attention.current_flash_attention_impl() == flash.name:
            attention.restore_flash_attention_impl()


@pytest.mark.parametrize("dtype", [torch.float16, torch.bfloat16])
@pytest.mark.parametrize("kv_heads", [4, 2, 1], ids=["mha", "gqa", "mqa"])
@pytest.mark.parametrize("is_causal", [False, True])
def test_packed_documents_match_each_document_alone(
    flash: Flash,
    cuda_device: torch.device,
    dtype: torch.dtype,
    kv_heads: int,
    is_causal: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Rows of 160: documents of 7, 64, 1 and 88, then documents of 100 and padding of 60.
    lengths, batch, width = [7, 64, 1, 88, 100, 60], 2, 160
    cu = torch.tensor([0, *torch.tensor(lengths).cumsum(0).tolist()], dtype=torch.int32)
    inputs = [
        torch.randn(batch, width, heads, 64, device=cuda_device, dtype=dtype)
        .transpose(1, 2)
        .requires_grad_()
        for heads in (4, kv_heads, kv_heads)
    ]
    metadata = prepare_packed_varlen_metadata(cu.to(cuda_device), batch_size=batch, seq_len=width)
    cudnn = Mock(side_effect=AssertionError(f"{flash.name} varlen must not dispatch to cuDNN"))
    monkeypatch.setattr(torch.ops.aten, "_cudnn_attention_forward", cudnn)
    monkeypatch.setattr(torch.ops.aten, "_cudnn_attention_backward", cudnn)

    def documentwise(tensors: list[torch.Tensor]) -> torch.Tensor:
        flat = [tensor.transpose(1, 2).flatten(0, 1) for tensor in tensors]
        outputs = []
        for start, stop in zip(cu.tolist(), cu.tolist()[1:], strict=False):
            q, k, v = (tensor[start:stop].transpose(0, 1)[None] for tensor in flat)
            with attention.sdpa_kernel(attention.SDPBackend.MATH):
                output = torch.nn.functional.scaled_dot_product_attention(
                    q, k, v, is_causal=is_causal, enable_gqa=kv_heads != 4
                )
            outputs.append(output[0].transpose(0, 1))
        return torch.cat(outputs).unflatten(0, (batch, width)).transpose(1, 2)

    references = [tensor.detach().clone().requires_grad_() for tensor in inputs]
    fp32_references = [tensor.detach().float().requires_grad_() for tensor in inputs]
    previous = attention.current_flash_attention_impl()
    assert previous in (None, flash.name)
    try:
        actual = flash.attend(*inputs, attention_metadata=metadata, is_causal=is_causal)
        expected, fp32_expected = documentwise(references), documentwise(fp32_references)
        assert_parity(actual, expected, dtype, "logits", "packed documents", fp32=fp32_expected)
        upstream = torch.randn_like(actual)
        actual.backward(upstream)
        expected.backward(upstream)
        fp32_expected.backward(upstream.float())
        for tensor, reference, fp32_reference in zip(
            inputs, references, fp32_references, strict=True
        ):
            assert_parity(
                tensor.grad,
                reference.grad,
                dtype,
                "gradients",
                "packed documents",
                fp32=fp32_reference.grad,
            )
        cudnn.assert_not_called()
    finally:
        if previous is None and attention.current_flash_attention_impl() == flash.name:
            attention.restore_flash_attention_impl()


@pytest.mark.parametrize("dtype", [torch.float16, torch.bfloat16])
def test_packed_model_matches_each_document_alone(
    flash: Flash, config: Olmo2Config, cuda_device: torch.device, dtype: torch.dtype
) -> None:
    config = replace(config, hidden_size=256, intermediate_size=384)
    model = Olmo2(replace(config, attn_implementation=flash.backend)).to(cuda_device, dtype)
    reference = Olmo2(config).to(cuda_device, dtype)
    reference.load_state_dict(model.state_dict(), strict=True)
    fp32_reference = Olmo2(config).to(cuda_device)
    fp32_reference.load_state_dict(model.state_dict(), strict=True)
    packed = packed_batch(PACKED_ROWS, PACKED_WIDTH)
    batch = {key: value.to(cuda_device) for key, value in packed.items()}
    real = batch["attention_mask"]
    previous = attention.current_flash_attention_impl()
    assert previous in (None, flash.name)
    try:
        actual = model(batch)
        upstream = torch.randn_like(actual).masked_fill(~real[:, :, None], 0)
        actual.backward(upstream)
        # Reference: each document alone in an unpadded row, through SDPA's math kernel.
        expected, fp32_expected = [], []
        for row, documents in enumerate(PACKED_ROWS):
            start = 0
            for document in documents:
                stop = start + len(document)
                alone = make_batch(torch.tensor([document], device=cuda_device))
                with attention.sdpa_kernel(attention.SDPBackend.MATH):
                    outputs = reference(alone), fp32_reference(alone)
                for output in outputs:
                    output.backward(upstream[row, start:stop][None].to(output.dtype))
                expected.append(outputs[0][0])
                fp32_expected.append(outputs[1][0])
                start = stop
        assert_parity(
            actual[real],
            torch.cat(expected),
            dtype,
            "logits",
            "packed model",
            fp32=torch.cat(fp32_expected),
        )
        for name, parameter in model.named_parameters():
            assert_parity(
                parameter.grad,
                reference.get_parameter(name).grad,
                dtype,
                "gradients",
                name,
                fp32=fp32_reference.get_parameter(name).grad,
            )
        assert attention.current_flash_attention_impl() == flash.name
    finally:
        if previous is None and attention.current_flash_attention_impl() == flash.name:
            attention.restore_flash_attention_impl()
