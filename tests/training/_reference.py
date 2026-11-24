"""Independent reference setup; these helpers do not call Flock's math helpers."""

import copy

import torch
from transformers import Olmo2Config as HFConfig
from transformers import Olmo2ForCausalLM
from transformers.models.olmo2.modeling_olmo2 import Olmo2MLP

from flock.training.core.attention import GroupedAttentionWithRoPE
from flock.training.core.distributed import DistributedRuntime
from flock.training.core.models.olmo2 import Olmo2, Olmo2Config
from flock.training.core.nn import SwiGLU
from flock.training.core.trainer import Trainer


def hf_config(config: Olmo2Config) -> HFConfig:
    # Map architecture fields explicitly: init_seed/hf_repo belong only to Flock.
    return HFConfig(
        vocab_size=config.vocab_size,
        hidden_size=config.hidden_size,
        intermediate_size=config.intermediate_size,
        num_hidden_layers=config.num_hidden_layers,
        num_attention_heads=config.num_attention_heads,
        num_key_value_heads=config.num_key_value_heads,
        max_position_embeddings=config.max_position_embeddings,
        rope_parameters={"rope_type": "default", "rope_theta": config.rope_theta},
        rms_norm_eps=config.rms_norm_eps,
        initializer_range=config.initializer_range,
        hidden_act="silu",
        attention_bias=False,
        attention_dropout=0.0,
        tie_word_embeddings=False,
        pad_token_id=None,
        eos_token_id=None,
        use_cache=False,
    )


def token_batch(padding: str, device: torch.device) -> tuple[torch.Tensor, torch.Tensor]:
    if padding == "left":
        ids = [[0, 0, 2, 3, 4, 5], [6, 7, 8, 9, 10, 11]]
        mask = [[0, 0, 1, 1, 1, 1], [1, 1, 1, 1, 1, 1]]
    elif padding == "right":
        ids = [[2, 3, 4, 5, 0, 0], [6, 7, 8, 9, 10, 11]]
        mask = [[1, 1, 1, 1, 0, 0], [1, 1, 1, 1, 1, 1]]
    else:
        assert padding == "none"
        ids = [[2, 3, 4, 5, 6, 7], [6, 7, 8, 9, 10, 11]]
        mask = [[1] * 6, [1] * 6]
    return torch.tensor(ids, device=device), torch.tensor(mask, device=device, dtype=torch.bool)


# FP32 tolerances. Absolute tolerances also cover gradients close to zero; never
# choose tolerances by retrying until a test passes.
FP32_TOLERANCES = {"logits": (1e-6, 1e-5), "gradients": (2e-5, 2e-4), "update": (2e-5, 2e-4)}


def assert_parity(
    actual: torch.Tensor | None,
    expected: torch.Tensor | None,
    dtype: torch.dtype,
    kind: str,
    name: str,
    *,
    fp32: torch.Tensor | None = None,
) -> None:
    """Compare `actual` with `expected`, a trusted run in the same `dtype`.

    FP32 uses fixed tolerances and ignores `fp32`. FP16 and BF16 use FlashAttention-3/4's
    rule: `fp32` is an FP32 run on the same rounded weights and inputs, and `actual` may
    be at most twice as far from it as `expected`, plus twice the rounding error of
    arithmetic on it in `dtype`.
    """
    assert actual is not None, name
    assert expected is not None, name
    if dtype == torch.float32:
        atol, rtol = FP32_TOLERANCES[kind]
        torch.testing.assert_close(
            actual,
            expected,
            atol=atol,
            rtol=rtol,
            msg=lambda error: f"{name} ({dtype}, {kind}): {error}",
        )
        return
    assert fp32 is not None, name
    fp32 = fp32.detach().float()
    error = (actual.detach().float() - fp32).abs().max().item()
    expected_error = (expected.detach().float() - fp32).abs().max().item()
    rounded = fp32.to(dtype)
    rounding_error = ((rounded + 0.3 - 0.3) - rounded).abs().max().item()
    assert error <= 2 * expected_error + 2 * rounding_error, (
        f"{name} ({dtype}, {kind}): FP32 error {error:.3g} exceeds twice the expected "
        f"run's {expected_error:.3g} plus twice the rounding error {rounding_error:.3g}"
    )


def olmo2_training_parity(
    config: Olmo2Config, padding: str, device: torch.device, dtype: torch.dtype
) -> None:
    model = Olmo2(config).to(device=device, dtype=dtype)
    reference_config = hf_config(config)
    reference_config._attn_implementation = "eager"
    reference = Olmo2ForCausalLM(reference_config)
    # Transformers' functools.wraps decoration exposes an extra self to ty.
    reference.to(device=device, dtype=dtype)  # ty: ignore[missing-argument]
    reference.load_state_dict(model.state_dict(), strict=True)
    # FP32 run on the same rounded weights, for FP16 and BF16 comparisons.
    fp32_reference = Olmo2ForCausalLM(reference_config)
    fp32_reference.to(device=device)  # ty: ignore[missing-argument]
    fp32_reference.load_state_dict(model.state_dict(), strict=True)
    model.train()
    reference.train()
    fp32_reference.train()

    ids, mask = token_batch(padding, device)
    labels = ids[:, 1:].clone()
    labels.masked_fill_(~(mask[:, :-1] & mask[:, 1:]), -100)
    positions = (mask[:, :-1].long().cumsum(-1) - 1).clamp_min(0)
    actual = model(
        {
            "input_ids": ids[:, :-1],
            "attention_mask": mask[:, :-1],
            "labels": labels,
            "position_ids": positions,
        }
    )
    # HF shifts labels internally. Suppress the pad -> first-real-token target as
    # well as padding targets, exactly matching the collator's adjacent-token rule.
    hf_labels = torch.full_like(ids, -100)
    hf_labels[:, 1:] = labels
    position_ids = (mask.long().cumsum(-1) - 1).clamp_min(0)
    expected, fp32_expected = (
        hf_model(ids, attention_mask=mask, position_ids=position_ids, labels=hf_labels)
        for hf_model in (reference, fp32_reference)
    )
    valid_inputs = mask[:, :-1]
    assert_parity(
        actual[valid_inputs],
        expected.logits[:, :-1][valid_inputs],
        dtype,
        "logits",
        "logits",
        fp32=fp32_expected.logits[:, :-1][valid_inputs],
    )
    trainer = object.__new__(Trainer)
    trainer.runtime = DistributedRuntime(0, 0, 1, device)
    trainer.torch_compile = False
    loss, _, token_count = trainer._calc_batch_loss(actual, labels)
    assert token_count.item() == (labels != -100).sum().item()
    loss.backward()
    expected.loss.backward()
    fp32_expected.loss.backward()
    for name, parameter in model.named_parameters():
        other = reference.get_parameter(name)
        assert parameter.grad is not None, name
        assert other.grad is not None, name
        assert parameter.grad.isfinite().all(), name
        assert_parity(
            parameter.grad,
            other.grad,
            dtype,
            "gradients",
            name,
            fp32=fp32_reference.get_parameter(name).grad,
        )

    # Like FlashAttention's and PyTorch's tests, FP16 and BF16 compare only outputs
    # and gradients.
    if dtype != torch.float32:
        return
    assert_parity(loss, expected.loss, dtype, "logits", "masked next-token loss")
    for candidate in (model, reference):
        torch.optim.AdamW(
            candidate.parameters(), lr=1e-3, betas=(0.9, 0.95), eps=1e-4, weight_decay=0.01
        ).step()
    for name, parameter in model.named_parameters():
        assert_parity(parameter, reference.get_parameter(name), dtype, "update", name)


def check_swiglu_parity(config: Olmo2Config, device: torch.device, dtype: torch.dtype) -> None:
    model = SwiGLU(hidden_size=config.hidden_size, intermediate_size=config.intermediate_size)
    reference = Olmo2MLP(hf_config(config))
    reference.load_state_dict(model.state_dict(), strict=True)
    model.to(device=device, dtype=dtype)
    reference.to(device=device, dtype=dtype)
    fp32_reference = copy.deepcopy(reference).float()
    x = torch.linspace(-1, 1, 2 * 5 * config.hidden_size, device=device, dtype=dtype)
    x = x.reshape(2, 5, config.hidden_size).requires_grad_()
    reference_x = x.detach().clone().requires_grad_()
    fp32_x = x.detach().float().requires_grad_()
    actual, expected, fp32_expected = model(x), reference(reference_x), fp32_reference(fp32_x)
    assert_parity(actual, expected, dtype, "logits", "SwiGLU output", fp32=fp32_expected)
    for output in (actual, expected, fp32_expected):
        output.float().square().mean().backward()
    assert_parity(x.grad, reference_x.grad, dtype, "gradients", "SwiGLU input", fp32=fp32_x.grad)
    for name, parameter in model.named_parameters():
        assert_parity(
            parameter.grad,
            reference.get_parameter(name).grad,
            dtype,
            "gradients",
            name,
            fp32=fp32_reference.get_parameter(name).grad,
        )


def check_gqa_parity(kv_heads: int, device: torch.device, dtype: torch.dtype, causal: bool) -> None:
    attention = GroupedAttentionWithRoPE(
        hidden_size=32, num_attention_heads=4, num_key_value_heads=kv_heads, bias=True
    ).to(device=device, dtype=dtype)
    reference = copy.deepcopy(attention)
    fp32_reference = copy.deepcopy(attention).float()
    x = torch.linspace(-1, 1, 2 * 5 * 32, device=device, dtype=dtype)
    x = x.reshape(2, 5, 32).requires_grad_()
    reference_x = x.detach().clone().requires_grad_()
    fp32_x = x.detach().float().requires_grad_()
    actual = attention(x, is_causal=causal)

    def oracle(module: GroupedAttentionWithRoPE, inputs: torch.Tensor) -> torch.Tensor:
        # Do not use SDPA in the oracle: explicitly repeat K/V and evaluate QK^T.
        q = module.q_proj(inputs).reshape(2, 5, 4, 8).transpose(1, 2)
        k = module.k_proj(inputs).reshape(2, 5, kv_heads, 8).transpose(1, 2)
        v = module.v_proj(inputs).reshape(2, 5, kv_heads, 8).transpose(1, 2)
        k = k.repeat_interleave(4 // kv_heads, dim=1)
        v = v.repeat_interleave(4 // kv_heads, dim=1)
        scores = q @ k.transpose(-2, -1) / (8**0.5)
        if causal:
            scores = scores.masked_fill(
                torch.ones(5, 5, device=device, dtype=torch.bool).triu(1), float("-inf")
            )
        weights = scores.float().softmax(-1).to(inputs.dtype)
        return module.o_proj((weights @ v).transpose(1, 2).reshape(2, 5, 32))

    expected, fp32_expected = oracle(reference, reference_x), oracle(fp32_reference, fp32_x)
    assert_parity(actual, expected, dtype, "logits", "GQA output", fp32=fp32_expected)
    for output in (actual, expected, fp32_expected):
        output.float().square().mean().backward()
    assert_parity(x.grad, reference_x.grad, dtype, "gradients", "GQA input", fp32=fp32_x.grad)
    for name, parameter in attention.named_parameters():
        assert_parity(
            parameter.grad,
            reference.get_parameter(name).grad,
            dtype,
            "gradients",
            name,
            fp32=fp32_reference.get_parameter(name).grad,
        )
