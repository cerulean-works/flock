from dataclasses import dataclass

import torch
from torch import nn

from flock.training.core.nn import GroupedAttentionWithRoPE, SwiGLU

from .base import BaseModel


@dataclass(frozen=True, kw_only=True)
class Olmo2Config:
    vocab_size: int
    hidden_size: int
    intermediate_size: int
    num_hidden_layers: int
    num_attention_heads: int
    num_key_value_heads: int
    max_position_embeddings: int = 4096
    rope_theta: float = 500_000.0
    rms_norm_eps: float = 1e-6
    initializer_range: float = 0.02
    init_seed: int = 42
    hf_repo: str | None = None

    def __post_init__(self):
        for name in (
            "vocab_size",
            "hidden_size",
            "intermediate_size",
            "num_hidden_layers",
            "num_attention_heads",
            "num_key_value_heads",
            "max_position_embeddings",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.hidden_size % self.num_attention_heads:
            raise ValueError("hidden_size must be divisible by num_attention_heads")
        if self.num_attention_heads % self.num_key_value_heads:
            raise ValueError("num_attention_heads must be divisible by num_key_value_heads")
        if (self.hidden_size // self.num_attention_heads) % 2:
            raise ValueError("RoPE requires an even head dimension")
        if self.rope_theta <= 0 or self.rms_norm_eps <= 0 or self.initializer_range <= 0:
            raise ValueError("rope_theta, rms_norm_eps and initializer_range must be positive")


# Architecture defaults from the corresponding Ai2 Hugging Face config.json files.
OLMO2_1B_CONFIG = Olmo2Config(
    vocab_size=100352,
    hidden_size=2048,
    intermediate_size=8192,
    num_hidden_layers=16,
    num_attention_heads=16,
    num_key_value_heads=16,
    hf_repo="allenai/OLMo-2-0425-1B",
)

OLMO2_7B_CONFIG = Olmo2Config(
    vocab_size=100352,
    hidden_size=4096,
    intermediate_size=11008,
    num_hidden_layers=32,
    num_attention_heads=32,
    num_key_value_heads=32,
    hf_repo="allenai/OLMo-2-1124-7B",
)


class Olmo2DecoderLayer(nn.Module):
    def __init__(self, config: Olmo2Config):
        super().__init__()
        head_dim = config.hidden_size // config.num_attention_heads
        self.self_attn = GroupedAttentionWithRoPE(
            hidden_size=config.hidden_size,
            num_attention_heads=config.num_attention_heads,
            num_key_value_heads=config.num_key_value_heads,
            q_norm=nn.RMSNorm(config.hidden_size, eps=config.rms_norm_eps),
            k_norm=nn.RMSNorm(config.num_key_value_heads * head_dim, eps=config.rms_norm_eps),
        )
        self.mlp = SwiGLU(
            hidden_size=config.hidden_size, intermediate_size=config.intermediate_size
        )
        self.post_attention_layernorm = nn.RMSNorm(config.hidden_size, eps=config.rms_norm_eps)
        self.post_feedforward_layernorm = nn.RMSNorm(config.hidden_size, eps=config.rms_norm_eps)

    def forward(  # ty: ignore[invalid-mutable-override]
        self,
        x: torch.Tensor,
        cos: torch.Tensor,
        sin: torch.Tensor,
        attention_mask: torch.Tensor | None,
    ) -> torch.Tensor:
        attention_output = self.self_attn(
            x,
            position_embeddings=(cos, sin),
            attention_mask=attention_mask,
            is_causal=attention_mask is None,
        )
        x = x + self.post_attention_layernorm(attention_output)
        return x + self.post_feedforward_layernorm(self.mlp(x))


class Olmo2Model(nn.Module):
    def __init__(self, config: Olmo2Config):
        super().__init__()
        self.embed_tokens = nn.Embedding(config.vocab_size, config.hidden_size)
        self.layers = nn.ModuleList(
            Olmo2DecoderLayer(config) for _ in range(config.num_hidden_layers)
        )
        self.norm = nn.RMSNorm(config.hidden_size, eps=config.rms_norm_eps)
        self.head_dim = config.hidden_size // config.num_attention_heads
        self.rope_theta = config.rope_theta

    def forward(  # ty: ignore[invalid-mutable-override]
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor | None,
        position_ids: torch.Tensor,
    ) -> torch.Tensor:
        x = self.embed_tokens(input_ids)
        # Build frequencies in float32 even when model weights are cast to bfloat16.
        inv_freq = self.rope_theta ** (
            -torch.arange(0, self.head_dim, 2, device=x.device, dtype=torch.float32) / self.head_dim
        )
        freqs = position_ids[..., None].float() * inv_freq
        angles = torch.cat((freqs, freqs), dim=-1).unsqueeze(1)
        cos, sin = angles.cos(), angles.sin()
        for layer in self.layers:
            x = layer(x, cos, sin, attention_mask)
        return self.norm(x)


class Olmo2(BaseModel):
    """Full-sequence OLMo 2 causal LM for training, returning [batch, sequence, vocab] logits.

    ``hf_repo`` selects the tokenizer only; construction initializes fresh weights.
    ``init_seed`` controls initialization independently of the caller's RNG state.
    Parameter names match Hugging Face's Olmo2ForCausalLM state dict.
    """

    def __init__(self, config: Olmo2Config):
        super().__init__(
            max_position_embeddings=config.max_position_embeddings,
            hf_repo=config.hf_repo,
            initializer_range=config.initializer_range,
            init_seed=config.init_seed,
        )
        self.config = config
        with self._initialize_weights():
            self.model = Olmo2Model(config)
            self.lm_head = nn.Linear(config.hidden_size, config.vocab_size, bias=False)

    def _forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
        position_ids: torch.Tensor,
    ) -> torch.Tensor:
        return self.lm_head(self.model(input_ids, attention_mask, position_ids))
