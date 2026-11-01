"""Independent reference setup; these helpers do not call Flock's math helpers."""

import copy

import torch
from transformers import Olmo2Config as HFConfig
from transformers import Olmo2ForCausalLM
from transformers.models.olmo2.modeling_olmo2 import Olmo2MLP

from flock.training.core.models.olmo2 import Olmo2, Olmo2Config
from flock.training.core.nn import GroupedAttentionWithRoPE, SwiGLU
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


def token_batch(padding: str, device: torch.device):
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


# Absolute tolerances also cover gradients close to zero. Low precision has its own
# fixed error budget; never choose tolerances by retrying until a test passes.
TOLERANCES = {
    torch.float32: {"logits": (1e-6, 1e-5), "gradients": (2e-5, 2e-4), "update": (2e-5, 2e-4)},
    torch.float16: {"logits": (5e-4, 5e-3), "gradients": (2e-3, 2e-2), "update": (2e-4, 1e-2)},
    torch.bfloat16: {"logits": (3e-3, 3e-2), "gradients": (1e-2, 5e-2), "update": (5e-4, 2e-2)},
}


def assert_parity(actual, expected, dtype, kind, name):
    assert actual is not None, name
    assert expected is not None, name
    atol, rtol = TOLERANCES[dtype][kind]
    torch.testing.assert_close(
        actual,
        expected,
        atol=atol,
        rtol=rtol,
        msg=lambda error: f"{name} ({dtype}, {kind}): {error}",
    )


def olmo2_training_parity(config, padding, device, dtype):
    model = Olmo2(config).to(device=device, dtype=dtype)
    reference_config = hf_config(config)
    reference_config._attn_implementation = "eager"
    reference = Olmo2ForCausalLM(reference_config)
    # Transformers' functools.wraps decoration exposes an extra self to ty.
    reference.to(device=device, dtype=dtype)  # ty: ignore[missing-argument]
    reference.load_state_dict(model.state_dict(), strict=True)
    model.train()
    reference.train()

    ids, mask = token_batch(padding, device)
    labels = ids[:, 1:].clone()
    labels.masked_fill_(~(mask[:, :-1] & mask[:, 1:]), -100)
    actual = model({"input_ids": ids[:, :-1], "attention_mask": mask[:, :-1], "labels": labels})
    # HF shifts labels internally. Suppress the pad -> first-real-token target as
    # well as padding targets, exactly matching the collator's adjacent-token rule.
    hf_labels = torch.full_like(ids, -100)
    hf_labels[:, 1:] = labels
    expected = reference(
        ids,
        attention_mask=mask,
        position_ids=(mask.long().cumsum(-1) - 1).clamp_min(0),
        labels=hf_labels,
    )
    valid_inputs = mask[:, :-1]
    assert_parity(
        actual[valid_inputs], expected.logits[:, :-1][valid_inputs], dtype, "logits", "logits"
    )
    loss, token_count = Trainer._calc_batch_loss(object.__new__(Trainer), actual, labels)
    assert token_count.item() == (labels != -100).sum().item()
    # HF computes its loss in FP32. Compare scalar values while retaining each
    # implementation's real loss precision for backward, including mixed precision.
    assert_parity(loss.float(), expected.loss, dtype, "logits", "masked next-token loss")
    loss.backward()
    expected.loss.backward()
    for name, parameter in model.named_parameters():
        other = reference.get_parameter(name)
        assert parameter.grad is not None, name
        assert other.grad is not None, name
        assert parameter.grad.isfinite().all(), name
        assert_parity(parameter.grad, other.grad, dtype, "gradients", name)

    for candidate in (model, reference):
        torch.optim.AdamW(
            candidate.parameters(), lr=1e-3, betas=(0.9, 0.95), eps=1e-4, weight_decay=0.01
        ).step()
    for name, parameter in model.named_parameters():
        assert_parity(parameter, reference.get_parameter(name), dtype, "update", name)


def check_swiglu_parity(config, device, dtype):
    model = SwiGLU(hidden_size=config.hidden_size, intermediate_size=config.intermediate_size)
    reference = Olmo2MLP(hf_config(config))
    reference.load_state_dict(model.state_dict(), strict=True)
    model.to(device=device, dtype=dtype)
    reference.to(device=device, dtype=dtype)
    x = torch.linspace(-1, 1, 2 * 5 * config.hidden_size, device=device, dtype=dtype)
    x = x.reshape(2, 5, config.hidden_size).requires_grad_()
    reference_x = x.detach().clone().requires_grad_()
    actual, expected = model(x), reference(reference_x)
    assert_parity(actual, expected, dtype, "logits", "SwiGLU output")
    actual.float().square().mean().backward()
    expected.float().square().mean().backward()
    assert_parity(x.grad, reference_x.grad, dtype, "gradients", "SwiGLU input")
    for name, parameter in model.named_parameters():
        assert_parity(parameter.grad, reference.get_parameter(name).grad, dtype, "gradients", name)


def check_gqa_parity(kv_heads, device, dtype, causal):
    attention = GroupedAttentionWithRoPE(
        hidden_size=32, num_attention_heads=4, num_key_value_heads=kv_heads, bias=True
    ).to(device=device, dtype=dtype)
    reference = copy.deepcopy(attention)
    x = torch.linspace(-1, 1, 2 * 5 * 32, device=device, dtype=dtype)
    x = x.reshape(2, 5, 32).requires_grad_()
    reference_x = x.detach().clone().requires_grad_()
    actual = attention(x, is_causal=causal)
    # Do not use SDPA in the oracle: explicitly repeat K/V and evaluate QK^T.
    q = reference.q_proj(reference_x).reshape(2, 5, 4, 8).transpose(1, 2)
    k = reference.k_proj(reference_x).reshape(2, 5, kv_heads, 8).transpose(1, 2)
    v = reference.v_proj(reference_x).reshape(2, 5, kv_heads, 8).transpose(1, 2)
    k = k.repeat_interleave(4 // kv_heads, dim=1)
    v = v.repeat_interleave(4 // kv_heads, dim=1)
    scores = q @ k.transpose(-2, -1) / (8**0.5)
    if causal:
        scores = scores.masked_fill(
            torch.ones(5, 5, device=device, dtype=torch.bool).triu(1), float("-inf")
        )
    weights = scores.float().softmax(-1).to(dtype)
    expected = reference.o_proj((weights @ v).transpose(1, 2).reshape(2, 5, 32))
    assert_parity(actual, expected, dtype, "logits", "GQA output")
    actual.float().square().mean().backward()
    expected.float().square().mean().backward()
    assert_parity(x.grad, reference_x.grad, dtype, "gradients", "GQA input")
    for name, parameter in attention.named_parameters():
        assert_parity(parameter.grad, reference.get_parameter(name).grad, dtype, "gradients", name)
