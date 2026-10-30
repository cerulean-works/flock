import pytest
import torch
from torch import nn

from flock.training.nn import GroupedAttentionWithRoPE


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
