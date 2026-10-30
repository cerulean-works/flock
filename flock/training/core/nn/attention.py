import torch
from torch import nn
from torch.nn import functional as F


class GroupedAttentionWithRoPE(nn.Module):
    """Self-attention with MHA/GQA projections and optional Q/K normalization and RoPE.

    Q/K normalization modules act on the full projected widths before splitting
    into heads. Pass modules that accept those widths (hidden_size for Q and
    num_key_value_heads * head_dim for K).
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
    ):
        super().__init__()
        if num_key_value_heads is None:
            num_key_value_heads = num_attention_heads
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
