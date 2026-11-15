import torch
from torch.nn import functional as F


def sdpa_attention(
    q: torch.Tensor,
    k: torch.Tensor,
    v: torch.Tensor,
    *,
    attention_mask: torch.Tensor | None = None,
    is_causal: bool = False,
) -> torch.Tensor:
    """Compute attention with a prepared mask, using the causal flag when absent.

    An explicit mask defines all visibility, including any causal restriction.
    `is_causal` applies only when `attention_mask` is `None`.
    """
    kernel_is_causal = is_causal and attention_mask is None
    return F.scaled_dot_product_attention(
        q,
        k,
        v,
        attn_mask=attention_mask,
        is_causal=kernel_is_causal,
        enable_gqa=q.shape[1] != k.shape[1],
    )
