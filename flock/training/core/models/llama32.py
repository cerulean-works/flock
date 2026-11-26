import math
from dataclasses import dataclass
from typing import Any

import torch
from torch import nn

from flock.training.core.attention import (
    AttentionInterface,
    AttentionMetadata,
    GroupedAttentionWithRoPE,
)
from flock.training.core.models.base import BaseModel
from flock.training.core.nn import SwiGLU


@dataclass(frozen=True, kw_only=True)
class Llama32Config:
    """Define a frozen Llama 3 text architecture and independent initialization seed.

    Defaults follow Llama 3.2, including its scaled RoPE.

    Args:
        vocab_size (`int`): Positive number of embedding and output vocabulary entries.
        hidden_size (`int`): Positive residual width, divisible by the query-head count.
        intermediate_size (`int`): Positive SwiGLU intermediate width.
        num_hidden_layers (`int`): Positive number of decoder layers.
        num_attention_heads (`int`): Positive query-head count; head width must be even.
        num_key_value_heads (`int`): Positive KV-head count dividing the query-head count.
        max_position_embeddings (`int`, *optional*, defaults to `131072`): Input length limit.
        rope_theta (`float`, *optional*, defaults to `500000.0`): Positive RoPE base.
        rope_scaling_factor (`float`, *optional*, defaults to `32.0`):
            Llama 3 RoPE frequency scaling factor. `None` uses unscaled RoPE.
        rope_low_freq_factor (`float`, *optional*, defaults to `1.0`):
            Sets the wavelength above which frequencies are fully scaled.
        rope_high_freq_factor (`float`, *optional*, defaults to `4.0`):
            Sets the wavelength below which frequencies are left unscaled.
        rope_original_max_position_embeddings (`int`, *optional*, defaults to `8192`):
            Context length the scaled frequencies were derived from.
        rms_norm_eps (`float`, *optional*, defaults to `1e-5`): Positive RMSNorm epsilon.
        tie_word_embeddings (`bool`, *optional*, defaults to `True`):
            Share one weight between the input embedding and the output projection.
        initializer_range (`float`, *optional*, defaults to `0.02`):
            Positive normal-initialization standard deviation.
        init_seed (`int`, *optional*, defaults to `42`): Model weight initialization seed.
        hf_repo (`str`, *optional*): Tokenizer repository or directory, never pretrained weights.
        attn_implementation (`str`, *optional*, defaults to `"sdpa"`):
            Registered attention implementation to use.

    Raises:
        `ValueError`: If dimensions, head divisibility, positive scales or RoPE
            scaling factors are invalid.
    """

    vocab_size: int
    hidden_size: int
    intermediate_size: int
    num_hidden_layers: int
    num_attention_heads: int
    num_key_value_heads: int
    max_position_embeddings: int = 131072
    rope_theta: float = 500_000.0
    rope_scaling_factor: float | None = 32.0
    rope_low_freq_factor: float = 1.0
    rope_high_freq_factor: float = 4.0
    rope_original_max_position_embeddings: int = 8192
    rms_norm_eps: float = 1e-5
    tie_word_embeddings: bool = True
    initializer_range: float = 0.02
    init_seed: int = 42
    hf_repo: str | None = None
    attn_implementation: str = "sdpa"

    def __post_init__(self) -> None:
        """Validate positive dimensions, grouped heads, RoPE settings and scales."""
        for name in (
            "vocab_size",
            "hidden_size",
            "intermediate_size",
            "num_hidden_layers",
            "num_attention_heads",
            "num_key_value_heads",
            "max_position_embeddings",
            "rope_original_max_position_embeddings",
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
        if self.rope_scaling_factor is not None and self.rope_scaling_factor <= 0:
            raise ValueError("rope_scaling_factor must be positive")
        if not 0 < self.rope_low_freq_factor < self.rope_high_freq_factor:
            raise ValueError("RoPE frequency factors must satisfy 0 < low < high")


# The architecture of Meta's Hugging Face config.json for Llama 3.2 1B.
LLAMA32_1B_CONFIG = Llama32Config(
    vocab_size=128256,
    hidden_size=2048,
    intermediate_size=8192,
    num_hidden_layers=16,
    num_attention_heads=32,
    num_key_value_heads=8,
    max_position_embeddings=131072,
    rope_theta=500_000.0,
    rope_scaling_factor=32.0,
    rope_low_freq_factor=1.0,
    rope_high_freq_factor=4.0,
    rope_original_max_position_embeddings=8192,
    rms_norm_eps=1e-5,
    tie_word_embeddings=True,
    hf_repo="meta-llama/Llama-3.2-1B",
)


class Llama32DecoderLayer(nn.Module):
    """Apply pre-normalized attention and SwiGLU branches with residual connections.

    Args:
        config (`Llama32Config`): Attention, MLP and normalization dimensions.
    """

    def __init__(self, config: Llama32Config) -> None:
        """Construct branch-input normalization, attention and MLP modules."""
        super().__init__()
        self.input_layernorm = nn.RMSNorm(config.hidden_size, eps=config.rms_norm_eps)
        self.self_attn = GroupedAttentionWithRoPE(
            hidden_size=config.hidden_size,
            num_attention_heads=config.num_attention_heads,
            num_key_value_heads=config.num_key_value_heads,
            attn_implementation=config.attn_implementation,
        )
        self.post_attention_layernorm = nn.RMSNorm(config.hidden_size, eps=config.rms_norm_eps)
        self.mlp = SwiGLU(
            hidden_size=config.hidden_size, intermediate_size=config.intermediate_size
        )

    def forward(  # ty: ignore[invalid-mutable-override]
        self,
        x: torch.Tensor,
        cos: torch.Tensor,
        sin: torch.Tensor,
        attention_metadata: AttentionMetadata,
    ) -> torch.Tensor:
        """Return updated hidden states with the same shape as `x`.

        Args:
            x (`torch.Tensor`): Hidden states shaped `[batch, sequence, hidden]`.
            cos (`torch.Tensor`): RoPE cosines broadcastable to attention heads.
            sin (`torch.Tensor`): RoPE sines with the same shape as `cos`.
            attention_metadata (`AttentionMetadata`):
                Metadata prepared for the selected attention implementation.

        Returns:
            `torch.Tensor`: Hidden states shaped `[batch, sequence, hidden]`.
        """
        x = x + self.self_attn(
            self.input_layernorm(x),
            position_embeddings=(cos, sin),
            attention_metadata=attention_metadata,
            is_causal=True,
        )
        return x + self.mlp(self.post_attention_layernorm(x))


class Llama32Model(nn.Module):
    """Embed tokens and apply decoder layers followed by final RMS normalization.

    Args:
        config (`Llama32Config`): Architecture and rotary embedding settings.
    """

    def __init__(self, config: Llama32Config) -> None:
        """Construct embeddings, decoder layers and final normalization."""
        super().__init__()
        self.config = config
        self.prepare_attention_metadata = AttentionInterface.get_metadata_preparer(
            config.attn_implementation
        )
        self.embed_tokens = nn.Embedding(config.vocab_size, config.hidden_size)
        self.layers = nn.ModuleList(
            Llama32DecoderLayer(config) for _ in range(config.num_hidden_layers)
        )
        self.norm = nn.RMSNorm(config.hidden_size, eps=config.rms_norm_eps)

    def rope_inv_freq(self, device: torch.device) -> torch.Tensor:
        """Return float32 RoPE inverse frequencies with Llama 3 scaling, when enabled.

        Long wavelengths are divided by the scaling factor, short ones are kept, and
        those in between are smoothly interpolated.
        """
        config = self.config
        head_dim = config.hidden_size // config.num_attention_heads
        exponents = torch.arange(0, head_dim, 2, device=device, dtype=torch.int64).float()
        inv_freq = 1.0 / (config.rope_theta ** (exponents / head_dim))
        factor = config.rope_scaling_factor
        if factor is None:
            return inv_freq
        low, high = config.rope_low_freq_factor, config.rope_high_freq_factor
        context = config.rope_original_max_position_embeddings
        wavelength = 2 * math.pi / inv_freq
        smooth = (context / wavelength - low) / (high - low)
        smoothed = (1 - smooth) * inv_freq / factor + smooth * inv_freq
        unscaled = torch.where(wavelength < context / high, inv_freq, smoothed)
        return torch.where(wavelength > context / low, inv_freq / factor, unscaled)

    def forward(  # ty: ignore[invalid-mutable-override]
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor | None,
        position_ids: torch.Tensor,
        *,
        cu_seq_lens: torch.Tensor | None = None,
    ) -> torch.Tensor:
        """Return hidden states using RoPE frequencies computed in FP32.

        Args:
            input_ids (`torch.Tensor`): Token IDs shaped `[batch, sequence]`.
            attention_mask (`torch.Tensor` or `None`): Boolean padding mask shaped
                `[batch, sequence]`; `None` means every token is valid. Unused when
                `cu_seq_lens` is given.
            position_ids (`torch.Tensor`): Positions shaped `[batch, sequence]`.
            cu_seq_lens (`torch.Tensor`, *optional*): int32 document boundaries of a
                packed batch in the flattened `[batch * sequence]` axis. Attention
                stays inside each document.

        Returns:
            `torch.Tensor`: Normalized states shaped `[batch, sequence, hidden]`.

        Raises:
            `ValueError`: If `cu_seq_lens` is given and the attention implementation
                does not support packed batches.
        """
        # Prepare once per forward; every layer reuses the metadata.
        batch_size, seq_len = input_ids.shape
        metadata = self.prepare_attention_metadata(
            attention_mask,
            batch_size=batch_size,
            seq_len=seq_len,
            is_causal=True,
            cu_seq_lens=cu_seq_lens,
        )
        x: Any = self.embed_tokens(input_ids)
        # Build frequencies in float32 even when model weights are cast to bfloat16.
        freqs = position_ids[..., None].float() * self.rope_inv_freq(x.device)
        angles = torch.cat((freqs, freqs), dim=-1).unsqueeze(1)
        cos, sin = angles.cos(), angles.sin()
        for layer in self.layers:
            x: Any = layer(x, cos, sin, metadata)
        return self.norm(x)


class Llama32(BaseModel):
    """Compute full-sequence Llama 3 causal language-model logits.

    `hf_repo` selects the tokenizer only; construction initializes fresh weights.
    `init_seed` controls initialization independently of the caller's RNG state.
    Parameter names match Hugging Face's LlamaForCausalLM state dict, so its
    pretrained weights load directly.

    Args:
        config ([`training.Llama32Config`]): Architecture, tokenizer and initialization settings.

    The inherited `forward` returns `[batch, sequence, vocabulary]` logits.
    Input embeddings and the output projection share one weight unless
    `tie_word_embeddings` is off. Packed batches need an attention implementation
    with a packed preparer.
    """

    def __init__(self, config: Llama32Config) -> None:
        """Initialize the decoder and vocabulary projection with the configured seed."""
        super().__init__(
            max_position_embeddings=config.max_position_embeddings,
            hf_repo=config.hf_repo,
            initializer_range=config.initializer_range,
            init_seed=config.init_seed,
        )
        self.config = config
        with self._initialize_weights():
            self.model = Llama32Model(config)
            self.lm_head = nn.Linear(config.hidden_size, config.vocab_size, bias=False)
            if config.tie_word_embeddings:
                self.lm_head.weight = self.model.embed_tokens.weight

    def _forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
        position_ids: torch.Tensor,
        *,
        cu_seq_lens: torch.Tensor | None = None,
    ) -> torch.Tensor:
        """Project validated decoder outputs to full-sequence vocabulary logits.

        With `cu_seq_lens`, the decoder keeps attention inside each document.
        """
        hidden = self.model(input_ids, attention_mask, position_ids, cu_seq_lens=cu_seq_lens)
        return self.lm_head(hidden)
