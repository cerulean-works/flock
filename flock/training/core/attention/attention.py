"""Provide grouped self-attention with optional rotary position embeddings."""

import torch
from torch import nn
from torch.nn import functional as F


class GroupedAttentionWithRoPE(nn.Module):
    """Apply MHA/GQA projections with optional Q/K normalization and RoPE.

    Q/K normalization modules act on the full projected widths before splitting
    into heads. Pass modules that accept those widths (hidden_size for Q and
    num_key_value_heads * head_dim for K).

    Args:
        hidden_size (`int`): Positive input/output width divisible by query-head count.
        num_attention_heads (`int`): Positive query-head count.
        num_key_value_heads (`int`, *optional*):
            Positive KV-head count dividing query-head count. Defaults to query-head count.
        bias (`bool`, *optional*, defaults to `False`): Add biases to all projections.
        q_norm (`nn.Module`, *optional*): Normalization over the projected query width.
        k_norm (`nn.Module`, *optional*): Normalization over the projected key width.

    Raises:
        `ValueError`: If widths/head counts are nonpositive or not divisible.
    """

    def __init__(
        self,
        *,
        hidden_size: int,
        num_attention_heads: int,
        num_key_value_heads: int | None = None,
        bias: bool = False,
        q_norm: nn.Module | None = None,
        k_norm: nn.Module | None = None,
    ) -> None:
        """Validate head dimensions and construct projections and normalization."""
        super().__init__()
        if num_key_value_heads is None:
            num_key_value_heads: int = num_attention_heads
        if hidden_size <= 0 or num_attention_heads <= 0 or num_key_value_heads <= 0:
            raise ValueError("hidden_size and attention head counts must be positive")
        if hidden_size % num_attention_heads:
            raise ValueError("hidden_size must be divisible by num_attention_heads")
        if num_attention_heads % num_key_value_heads:
            raise ValueError("num_attention_heads must be divisible by num_key_value_heads")

        self.num_heads = num_attention_heads
        self.num_kv_heads = num_key_value_heads
        self.head_dim = hidden_size // num_attention_heads

        kv_size = num_key_value_heads * self.head_dim

        self.q_proj = nn.Linear(hidden_size, hidden_size, bias=bias)
        self.k_proj = nn.Linear(hidden_size, kv_size, bias=bias)
        self.v_proj = nn.Linear(hidden_size, kv_size, bias=bias)
        self.o_proj = nn.Linear(hidden_size, hidden_size, bias=bias)

        self.q_norm = q_norm if q_norm is not None else nn.Identity()
        self.k_norm = k_norm if k_norm is not None else nn.Identity()

    def _apply_rope(self, x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor) -> torch.Tensor:
        """Apply split-half rotary coordinates and restore the input tensor's dtype."""
        # Split-half coordinates match Hugging Face's rotary checkpoint layout.
        first, second = x.chunk(2, dim=-1)
        rotated = torch.cat((-second, first), dim=-1)
        return (x * cos + rotated * sin).to(x.dtype)

    def forward(  # ty: ignore[invalid-mutable-override]
        self,
        x: torch.Tensor,
        *,
        position_embeddings: tuple[torch.Tensor, torch.Tensor] | None = None,
        attention_mask: torch.Tensor | None = None,
        is_causal: bool = False,
    ) -> torch.Tensor:
        """Map [batch, sequence, hidden] to the same shape using PyTorch SDPA.

        RoPE cos/sin must broadcast to [batch, heads, sequence, head_dim].
        Masks follow SDPA semantics: True allows attention; floats are additive.
        Supply either a complete attention mask (including causal constraints)
        or is_causal=True for unmasked causal attention.

        Args:
            x (`torch.Tensor`): Hidden states shaped `[batch, sequence, hidden]`.
            position_embeddings (`tuple[torch.Tensor, torch.Tensor]`, *optional*):
                Cosines and sines for split-half RoPE. Omit to skip rotary embeddings.
            attention_mask (`torch.Tensor`, *optional*): SDPA mask broadcastable to
                `[batch, heads, sequence, sequence]`, including causal constraints.
            is_causal (`bool`, *optional*, defaults to `False`):
                Use SDPA causal attention when no explicit mask is supplied.

        Returns:
            `torch.Tensor`: Projected states with the same shape as `x`.

        Raises:
            `ValueError`: If rotary embeddings are supplied with an odd head width.
        """
        batch_size, seq_len, _ = x.shape

        q = self.q_norm(self.q_proj(x)).view(batch_size, seq_len, self.num_heads, self.head_dim)
        k = self.k_norm(self.k_proj(x)).view(batch_size, seq_len, self.num_kv_heads, self.head_dim)
        v = self.v_proj(x).view(batch_size, seq_len, self.num_kv_heads, self.head_dim)

        q, k = q.transpose(1, 2), k.transpose(1, 2)

        if position_embeddings is not None:
            if self.head_dim % 2:
                raise ValueError("RoPE requires an even head dimension")
            cos, sin = position_embeddings
            q = self._apply_rope(q, cos, sin)
            k = self._apply_rope(k, cos, sin)

        output = F.scaled_dot_product_attention(
            q,
            k,
            v.transpose(1, 2),
            attn_mask=attention_mask,
            is_causal=is_causal,
            enable_gqa=self.num_heads != self.num_kv_heads,
        )

        return self.o_proj(output.transpose(1, 2).reshape(batch_size, seq_len, -1))
