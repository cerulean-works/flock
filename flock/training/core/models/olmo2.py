"""Implement full-sequence OLMo 2 models and fresh-initialization presets."""

from dataclasses import dataclass

import torch
from torch import nn

from flock.training.core.nn import GroupedAttentionWithRoPE, SwiGLU

from .base import BaseModel


@dataclass(frozen=True, kw_only=True)
class Olmo2Config:
    """Define a frozen OLMo 2 architecture and independent initialization seed.

    Args:
        vocab_size (`int`): Positive number of embedding and output vocabulary entries.
        hidden_size (`int`): Positive residual width, divisible by the query-head count.
        intermediate_size (`int`): Positive SwiGLU intermediate width.
        num_hidden_layers (`int`): Positive number of decoder layers.
        num_attention_heads (`int`): Positive query-head count; head width must be even.
        num_key_value_heads (`int`): Positive KV-head count dividing the query-head count.
        max_position_embeddings (`int`, *optional*, defaults to `4096`): Input length limit.
        rope_theta (`float`, *optional*, defaults to `500000.0`): Positive RoPE base.
        rms_norm_eps (`float`, *optional*, defaults to `1e-6`): Positive RMSNorm epsilon.
        initializer_range (`float`, *optional*, defaults to `0.02`):
            Positive normal-initialization standard deviation.
        init_seed (`int`, *optional*, defaults to `42`): Model weight initialization seed.
        hf_repo (`str`, *optional*): Tokenizer repository or directory, never pretrained weights.

    Raises:
        `ValueError`: If dimensions, head divisibility or positive scales are invalid.
    """

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

    def __post_init__(self) -> None:
        """Validate positive dimensions, grouped heads and an even RoPE head width."""
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
    """Apply normalized attention and SwiGLU branches with residual connections.

    Args:
        config (`Olmo2Config`): Attention, MLP and normalization dimensions.
    """

    def __init__(self, config: Olmo2Config) -> None:
        """Construct attention, MLP and branch-output normalization modules."""
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
        """Return updated hidden states with the same shape as `x`.

        Args:
            x (`torch.Tensor`): Hidden states shaped `[batch, sequence, hidden]`.
            cos (`torch.Tensor`): RoPE cosines broadcastable to attention heads.
            sin (`torch.Tensor`): RoPE sines with the same shape as `cos`.
            attention_mask (`torch.Tensor` or `None`): Complete SDPA attention mask.
                With `None`, use SDPA's causal mode.

        Returns:
            `torch.Tensor`: Hidden states shaped `[batch, sequence, hidden]`.
        """
        attention_output = self.self_attn(
            x,
            position_embeddings=(cos, sin),
            attention_mask=attention_mask,
            is_causal=attention_mask is None,
        )
        x = x + self.post_attention_layernorm(attention_output)
        return x + self.post_feedforward_layernorm(self.mlp(x))


class Olmo2Model(nn.Module):
    """Embed tokens and apply decoder layers followed by final RMS normalization.

    Args:
        config (`Olmo2Config`): Architecture and rotary embedding settings.
    """

    def __init__(self, config: Olmo2Config) -> None:
        """Construct embeddings, decoder layers and final normalization."""
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
        """Return hidden states using RoPE frequencies computed in FP32.

        Args:
            input_ids (`torch.Tensor`): Token IDs shaped `[batch, sequence]`.
            attention_mask (`torch.Tensor` or `None`): Complete SDPA mask, or
                `None` for causal attention without padding.
            position_ids (`torch.Tensor`): Positions shaped `[batch, sequence]`.

        Returns:
            `torch.Tensor`: Normalized states shaped `[batch, sequence, hidden]`.
        """
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
    """Compute full-sequence OLMo 2 causal language-model logits.

    `hf_repo` selects the tokenizer only; construction initializes fresh weights.
    `init_seed` controls initialization independently of the caller's RNG state.
    Parameter names match Hugging Face's Olmo2ForCausalLM state dict.

    Args:
        config ([`training.Olmo2Config`]): Architecture, tokenizer and initialization settings.

    The inherited `forward` returns `[batch, sequence, vocabulary]` logits.
    Input embeddings and the output projection have separate, untied weights.
    """

    def __init__(self, config: Olmo2Config) -> None:
        """Initialize the decoder and vocabulary projection with the configured seed."""
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
        """Project validated decoder outputs to full-sequence vocabulary logits."""
        return self.lm_head(self.model(input_ids, attention_mask, position_ids))
