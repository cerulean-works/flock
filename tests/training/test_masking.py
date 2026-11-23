import pytest
import torch
from torch.nn import functional as F

from flock.training.core.attention import (
    AttentionInterface,
    VarlenMetadata,
    prepare_packed_sdpa_mask,
    prepare_sdpa_mask,
    prepare_varlen_metadata,
    sdpa_attention,
)
from flock.training.core.attention.registry import _MaskPreparationFunction


def test_packed_sdpa_mask_is_causal_by_default() -> None:
    # Two documents followed by one padding token.
    boundaries = torch.tensor([0, 2, 4, 5], dtype=torch.int32)
    mask = prepare_packed_sdpa_mask(boundaries, batch_size=1, seq_len=5)
    block = torch.tensor([[True, False], [True, True]])
    expected = torch.block_diag(block, block, torch.ones(1, 1, dtype=torch.bool))
    torch.testing.assert_close(mask, expected[None, None])


@pytest.mark.parametrize("is_causal", [False, True])
def test_packed_sdpa_matches_independent_documents(is_causal: bool) -> None:
    # First row: A A B B PAD. Second row: C D D D D.
    lengths = [(2, 2, 1), (1, 4)]
    boundaries = torch.tensor([0, 2, 4, 5, 6, 10], dtype=torch.int32)
    prepare = AttentionInterface.get_packed_preparer("sdpa")
    assert prepare is prepare_packed_sdpa_mask
    mask = prepare(boundaries, batch_size=2, seq_len=5, is_causal=is_causal)
    torch.testing.assert_close(
        mask[0, 0, 2], torch.tensor([False, False, True, not is_causal, False])
    )

    inputs = [torch.randn(2, 2, 5, 8, dtype=torch.float64, requires_grad=True) for _ in range(3)]
    references = [tensor.detach().clone().requires_grad_() for tensor in inputs]
    actual = AttentionInterface.get_interface("sdpa")(
        *inputs, attention_metadata=mask, is_causal=is_causal
    )

    rows = []
    for row, segment_lengths in enumerate(lengths):
        q, k, v = (tensor[row].split(segment_lengths, dim=-2) for tensor in references)
        outputs = [
            F.scaled_dot_product_attention(qi, ki, vi, is_causal=is_causal)
            for qi, ki, vi in zip(q, k, v, strict=True)
        ]
        rows.append(torch.cat(outputs, dim=-2))
    expected = torch.stack(rows)
    torch.testing.assert_close(actual, expected)
    upstream = torch.randn_like(actual)
    torch.testing.assert_close(
        torch.autograd.grad(actual, inputs, upstream),
        torch.autograd.grad(expected, references, upstream),
    )


@pytest.mark.parametrize("prepare", [prepare_sdpa_mask, prepare_varlen_metadata])
@pytest.mark.parametrize("is_causal", [False, True])
@pytest.mark.parametrize("mask", [None, torch.ones(2, 4, dtype=torch.bool)])
def test_unpadded_masks_use_the_causal_flag(
    prepare: _MaskPreparationFunction, is_causal: bool, mask: torch.Tensor | None
) -> None:
    prepared = prepare(mask, is_causal=is_causal)
    assert prepared is None
    q, k, v = (torch.randn(2, 2, 4, 8) for _ in range(3))
    actual = sdpa_attention(q, k, v, attention_metadata=prepared, is_causal=is_causal)
    expected = F.scaled_dot_product_attention(q, k, v, is_causal=is_causal)
    torch.testing.assert_close(actual, expected)


@pytest.mark.parametrize("prepare", [prepare_sdpa_mask, prepare_varlen_metadata])
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
    actual = sdpa_attention(*inputs, attention_metadata=prepared, is_causal=is_causal)
    expected = F.scaled_dot_product_attention(*references, attn_mask=expected_mask)
    torch.testing.assert_close(actual, expected)
    upstream = torch.randn_like(actual)
    actual.backward(upstream)
    expected.backward(upstream)
    for tensor, reference in zip(inputs, references, strict=True):
        torch.testing.assert_close(tensor.grad, reference.grad)


@pytest.mark.parametrize("is_causal", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_varlen_preparation_builds_packing_metadata(is_causal: bool, empty: bool) -> None:
    mask = torch.tensor([[False, True, True, False], [False] * 4])
    if empty:
        mask.fill_(False)
    prepared = prepare_varlen_metadata(mask, is_causal=is_causal)
    assert isinstance(prepared, VarlenMetadata)
    expected_indices = torch.tensor([] if empty else [1, 2], dtype=torch.int64)
    expected_cu_seq_lens = torch.tensor([0] if empty else [0, 2], dtype=torch.int32)
    torch.testing.assert_close(prepared.indices, expected_indices)
    torch.testing.assert_close(prepared.cu_seq_lens_q, expected_cu_seq_lens)
    assert prepared.cu_seq_lens_k is prepared.cu_seq_lens_q
    assert prepared.max_length_q == prepared.max_length_k == (0 if empty else 2)
    assert (prepared.batch_size, prepared.seq_len) == (2, 4)
