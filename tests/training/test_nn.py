from dataclasses import replace

import pytest
import torch
from torch import nn
from transformers.models.olmo2.modeling_olmo2 import (
    Olmo2Attention,
    Olmo2RotaryEmbedding,
    apply_rotary_pos_emb,
)

from flock.training.core.nn import GroupedAttentionWithRoPE
from tests.training._reference import check_gqa_parity, check_swiglu_parity, hf_config


@pytest.mark.parametrize("mask_type", ["none", "causal", "padding", "additive"])
def test_attention_matches_pytorch_multihead_attention(mask_type):
    """The shared module also supports ordinary attention without OLMo QK-norm/RoPE."""
    torch.manual_seed(0)
    attention = GroupedAttentionWithRoPE(hidden_size=16, num_attention_heads=4, bias=True)
    reference = nn.MultiheadAttention(16, 4, bias=True, batch_first=True)
    with torch.no_grad():
        reference.in_proj_weight.copy_(
            torch.cat(
                [
                    attention.q_proj.weight,
                    attention.k_proj.weight,
                    attention.v_proj.weight,
                ]
            )
        )
        reference.in_proj_bias.copy_(
            torch.cat(
                [
                    attention.q_proj.bias,
                    attention.k_proj.bias,
                    attention.v_proj.bias,
                ]
            )
        )
        reference.out_proj.load_state_dict(attention.o_proj.state_dict())

    x = torch.randn(2, 5, 16, requires_grad=True)
    reference_x = x.detach().clone().requires_grad_()
    mask = reference_mask = padding_mask = None
    if mask_type == "causal":
        reference_mask = torch.ones(5, 5, dtype=torch.bool).triu(1)
    elif mask_type == "padding":
        padding_mask = torch.tensor([[False, False, False, True, True], [False] * 5])
        mask = ~padding_mask[:, None, None, :]
    elif mask_type == "additive":
        mask = reference_mask = torch.zeros(5, 5).masked_fill(
            torch.ones(5, 5, dtype=torch.bool).triu(1), float("-inf")
        )
    actual = attention(x, attention_mask=mask, is_causal=mask_type == "causal")
    expected, _ = reference(
        reference_x,
        reference_x,
        reference_x,
        attn_mask=reference_mask,
        key_padding_mask=padding_mask,
    )
    torch.testing.assert_close(actual, expected, atol=1e-6, rtol=1e-5)
    actual.square().sum().backward()
    expected.square().sum().backward()
    torch.testing.assert_close(x.grad, reference_x.grad, atol=1e-6, rtol=1e-5)
    assert reference.in_proj_weight.grad is not None
    assert reference.in_proj_bias.grad is not None
    for index, projection in enumerate((attention.q_proj, attention.k_proj, attention.v_proj)):
        weight_grad = reference.in_proj_weight.grad.chunk(3)[index]
        bias_grad = reference.in_proj_bias.grad.chunk(3)[index]
        torch.testing.assert_close(projection.weight.grad, weight_grad, atol=2e-5, rtol=2e-4)
        torch.testing.assert_close(projection.bias.grad, bias_grad, atol=2e-5, rtol=2e-4)
    torch.testing.assert_close(
        attention.o_proj.weight.grad, reference.out_proj.weight.grad, atol=2e-5, rtol=2e-4
    )
    torch.testing.assert_close(attention.o_proj.bias.grad, reference.out_proj.bias.grad)


@pytest.mark.parametrize("kv_heads", [4, 2, 1])
@pytest.mark.parametrize("causal", [False, True])
def test_gqa_matches_explicit_pytorch_attention(kv_heads, causal):
    check_gqa_parity(kv_heads, torch.device("cpu"), torch.float32, causal)


def test_swiglu_matches_huggingface(config):
    check_swiglu_parity(config, torch.device("cpu"), torch.float32)


def test_rope_matches_huggingface_at_nonzero_positions(config):
    reference_config = hf_config(config)
    attention = GroupedAttentionWithRoPE(hidden_size=32, num_attention_heads=4)
    x = torch.linspace(-2, 2, 2 * 4 * 5 * 8).reshape(2, 4, 5, 8).requires_grad_()
    reference_x = x.detach().clone().requires_grad_()
    positions = torch.tensor([[1, 2, 3, 4, 5], [0, 3, 5, 9, 15]])
    cos, sin = Olmo2RotaryEmbedding(reference_config)(x, positions)
    actual = attention._apply_rope(x, cos[:, None], sin[:, None])
    expected, _ = apply_rotary_pos_emb(reference_x, reference_x, cos, sin)
    torch.testing.assert_close(actual, expected)
    actual.square().mean().backward()
    expected.square().mean().backward()
    torch.testing.assert_close(x.grad, reference_x.grad)


@pytest.mark.parametrize("kv_heads", [4, 2, 1])
def test_flattened_qk_norm_and_rotary_attention_match_huggingface(config, kv_heads):
    config = replace(config, num_key_value_heads=kv_heads)
    reference_config = hf_config(config)
    reference_config._attn_implementation = "eager"
    attention = GroupedAttentionWithRoPE(
        hidden_size=32,
        num_attention_heads=4,
        num_key_value_heads=kv_heads,
        q_norm=nn.RMSNorm(32, eps=config.rms_norm_eps),
        k_norm=nn.RMSNorm(kv_heads * 8, eps=config.rms_norm_eps),
    )
    reference = Olmo2Attention(reference_config, layer_idx=0)
    reference.load_state_dict(attention.state_dict(), strict=True)
    x = torch.linspace(-2, 2, 2 * 5 * 32).reshape(2, 5, 32).requires_grad_()
    reference_x = x.detach().clone().requires_grad_()
    positions = torch.tensor([[1, 2, 3, 4, 5], [0, 1, 2, 3, 4]])
    cos, sin = Olmo2RotaryEmbedding(reference_config)(x, positions)
    mask = torch.ones(5, 5, dtype=torch.bool).tril()[None, None]
    actual = attention(x, attention_mask=mask, position_embeddings=(cos[:, None], sin[:, None]))
    expected, _ = reference(
        reference_x,
        position_embeddings=(cos, sin),
        attention_mask=torch.zeros(5, 5).masked_fill(~mask[0, 0], float("-inf")),
    )
    torch.testing.assert_close(actual, expected, atol=1e-6, rtol=1e-5)
    actual.square().mean().backward()
    expected.square().mean().backward()
    torch.testing.assert_close(x.grad, reference_x.grad, atol=2e-5, rtol=2e-4)
    for name, parameter in attention.named_parameters():
        torch.testing.assert_close(
            parameter.grad,
            reference.get_parameter(name).grad,
            atol=2e-5,
            rtol=2e-4,
            msg=lambda error, name=name: f"{name}: {error}",
        )
