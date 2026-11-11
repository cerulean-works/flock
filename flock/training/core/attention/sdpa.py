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

    return F.scaled_dot_product_attention(
        q,
        k,
        v,
        attn_mask=attention_mask,
        is_causal=is_causal,
        enable_gqa=q.shape[1] != k.shape[1],
    )
