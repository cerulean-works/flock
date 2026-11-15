import pytest
import torch
from torch.nn import functional as F

from flock.training.core.attention import (
    prepare_flash_attention_mask,
    prepare_sdpa_mask,
    sdpa_attention,
)
from flock.training.core.attention.registry import _MaskPreparationFunction


@pytest.mark.parametrize("prepare", [prepare_sdpa_mask, prepare_flash_attention_mask])
@pytest.mark.parametrize("is_causal", [False, True])
@pytest.mark.parametrize("mask", [None, torch.ones(2, 4, dtype=torch.bool)])
def test_unpadded_masks_use_the_causal_flag(
    prepare: _MaskPreparationFunction, is_causal: bool, mask: torch.Tensor | None
) -> None:
    prepared = prepare(mask, is_causal=is_causal)
    assert prepared is None
    q, k, v = (torch.randn(2, 2, 4, 8) for _ in range(3))
    actual = sdpa_attention(q, k, v, attention_mask=prepared, is_causal=is_causal)
    expected = F.scaled_dot_product_attention(q, k, v, is_causal=is_causal)
    torch.testing.assert_close(actual, expected)


@pytest.mark.parametrize("prepare", [prepare_sdpa_mask, prepare_flash_attention_mask])
@pytest.mark.parametrize(
    "mask",
    [torch.ones(2, 4), torch.ones(2, 1, 4, 4, dtype=torch.bool), torch.ones(4, dtype=torch.bool)],
)
def test_preparers_require_boolean_padding_masks(
    prepare: _MaskPreparationFunction, mask: torch.Tensor
) -> None:
    with pytest.raises(ValueError, match="boolean.*padding mask"):
        prepare(mask, is_causal=True)


@pytest.mark.parametrize("is_causal", [False, True])
def test_sdpa_preparation_preserves_query_visibility_and_gradients(is_causal: bool) -> None:
    mask = torch.tensor([[False, True, True, False], [False] * 4])
    # Explicit per-query visibility includes the padded query after the valid span.
    visible = (
        [
            [False] * 4,
            [False, True, False, False],
            [False, True, True, False],
            [False, True, True, False],
        ]
        if is_causal
        else [[False, True, True, False]] * 4
    )
    expected_mask = torch.tensor([visible, [[False] * 4] * 4])[:, None]
    prepared = prepare_sdpa_mask(mask, is_causal=is_causal)
    assert prepared is not None
    torch.testing.assert_close(prepared.expand(2, 1, 4, 4), expected_mask)
    inputs = [torch.randn(2, 2, 4, 8, requires_grad=True) for _ in range(3)]
    references = [tensor.detach().clone().requires_grad_() for tensor in inputs]
    actual = sdpa_attention(*inputs, attention_mask=prepared, is_causal=is_causal)
    expected = F.scaled_dot_product_attention(*references, attn_mask=expected_mask)
    torch.testing.assert_close(actual, expected)
    upstream = torch.randn_like(actual)
    actual.backward(upstream)
    expected.backward(upstream)
    for tensor, reference in zip(inputs, references, strict=True):
        torch.testing.assert_close(tensor.grad, reference.grad)


@pytest.mark.parametrize("is_causal", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_flash_preparation_stores_host_spans(is_causal: bool, empty: bool) -> None:
    mask = torch.tensor([[False, True, True, False], [False] * 4])
    if empty:
        mask.fill_(False)
    prepared = prepare_flash_attention_mask(mask, is_causal=is_causal)
    assert prepared is not None
    expected = torch.tensor([[4, 4], [4, 4]] if empty else [[1, 3], [4, 4]])
    torch.testing.assert_close(prepared, expected)
