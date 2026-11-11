import torch


def flash_attention_3(
    q: torch.Tensor,
    k: torch.Tensor,
    v: torch.Tensor,
    *,
    attention_mask: torch.Tensor | None = None,
    is_causal: bool = False,
) -> torch.Tensor:
    """Reserve the shared attention signature for FlashAttention-3."""
    raise NotImplementedError("still not implemented yet.")
