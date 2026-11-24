import torch
from torch.nn import functional as F


def sdpa_attention(
    q: torch.Tensor,
    k: torch.Tensor,
    v: torch.Tensor,
    *,
    attention_metadata: torch.Tensor | None = None,
    is_causal: bool = False,
) -> torch.Tensor:
    """Compute attention with a prepared mask, using the causal flag when absent.

    `attention_metadata` is the dense mask from `prepare_sdpa_mask` or `prepare_packed_sdpa_mask`.
    An explicit mask defines all visibility, including any causal restriction.
    `is_causal` applies only when the mask is `None`.
    """
    kernel_is_causal = is_causal and attention_metadata is None
    return F.scaled_dot_product_attention(
        q,
        k,
        v,
        attn_mask=attention_metadata,
        is_causal=kernel_is_causal,
        enable_gqa=q.shape[1] != k.shape[1],
    )
