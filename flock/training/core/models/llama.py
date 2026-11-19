import math
from dataclasses import dataclass
from typing import Any

import torch
from torch import nn

from flock.training.core.attention import (
    AttentionInterface,
    GroupedAttentionWithRoPE,
    PackedSequences,
)
from flock.training.core.nn import SwiGLU

from .base import BaseModel


@dataclass(frozen=True, kw_only=True)
class Llama3RopeScaling:
    """Llama 3's frequency-dependent RoPE scaling, as in Llama 3.1 and 3.2 `config.json`.

    Rotations with wavelengths longer than `original_max_position_embeddings / low_freq_factor`
    slow down by `factor`; those shorter than `original_max_position_embeddings / high_freq_factor`
    are unchanged; wavelengths in between interpolate smoothly.

    Args:
        factor (`float`, *optional*, defaults to `32.0`): Slowdown of the lowest frequencies.
        low_freq_factor (`float`, *optional*, defaults to `1.0`): Lower wavelength threshold divisor.
        high_freq_factor (`float`, *optional*, defaults to `4.0`): Upper wavelength threshold divisor.
        original_max_position_embeddings (`int`, *optional*, defaults to `8192`):
            Context length the unscaled frequencies were trained for.
    """

    factor: float = 32.0
    low_freq_factor: float = 1.0
    high_freq_factor: float = 4.0
    original_max_position_embeddings: int = 8192

    def __post_init__(self) -> None:
        """Require positive scales and a high-frequency factor above the low one."""
        if (
            self.factor <= 0
            or self.low_freq_factor <= 0
            or self.original_max_position_embeddings <= 0
        ):
            raise ValueError("RoPE scaling factors and context length must be positive")
        if self.high_freq_factor <= self.low_freq_factor:
            raise ValueError("high_freq_factor must exceed low_freq_factor")


@dataclass(frozen=True, kw_only=True)
class LlamaConfig:
    """Define a frozen Llama 3.2-style architecture and independent initialization seed.

    Decoder layers normalize before attention and before the MLP (pre-norm), use grouped-query
    attention without query/key normalization or biases, and a SwiGLU MLP.

    Args:
        vocab_size (`int`): Positive number of embedding and output vocabulary entries.
        hidden_size (`int`): Positive residual width, divisible by the query-head count.
        intermediate_size (`int`): Positive SwiGLU intermediate width.
        num_hidden_layers (`int`): Positive number of decoder layers.
        num_attention_heads (`int`): Positive query-head count; head width must be even.
        num_key_value_heads (`int`): Positive KV-head count dividing the query-head count.
        max_position_embeddings (`int`, *optional*, defaults to `4096`): Input length limit
            for training. Llama 3.2 itself supports 131072 tokens.
        rope_theta (`float`, *optional*, defaults to `500000.0`): Positive RoPE base.
        rope_scaling (`Llama3RopeScaling`, *optional*): Llama 3 frequency scaling. Defaults to
            Llama 3.2's settings; `None` uses plain RoPE.
        rms_norm_eps (`float`, *optional*, defaults to `1e-5`): Positive RMSNorm epsilon.
        tie_word_embeddings (`bool`, *optional*, defaults to `True`): Share the input
            embedding with the output projection, as Llama 3.2 1B and 3B do.
        initializer_range (`float`, *optional*, defaults to `0.02`):
            Positive normal-initialization standard deviation.
        init_seed (`int`, *optional*, defaults to `42`): Model weight initialization seed.
        hf_repo (`str`, *optional*): Tokenizer repository or directory, never pretrained weights.
        attn_implementation (`str`, *optional*, defaults to `"sdpa"`):
            Registered attention implementation to use.

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
    rope_scaling: Llama3RopeScaling | None = Llama3RopeScaling()
    rms_norm_eps: float = 1e-5
    tie_word_embeddings: bool = True
    initializer_range: float = 0.02
    init_seed: int = 42
    hf_repo: str | None = None
    attn_implementation: str = "sdpa"

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
        if isinstance(self.rope_scaling, dict):
            # config.json stores the scaling as a table; rebuild the dataclass when loading.
            object.__setattr__(self, "rope_scaling", Llama3RopeScaling(**self.rope_scaling))


# Architecture from Meta's Llama 3.2 config.json files, trained here at a 4096-token context.
LLAMA3_2_1B_CONFIG = LlamaConfig(
    vocab_size=128256,
    hidden_size=2048,
    intermediate_size=8192,
    num_hidden_layers=16,
    num_attention_heads=32,
    num_key_value_heads=8,
    hf_repo="meta-llama/Llama-3.2-1B",
)

LLAMA3_2_3B_CONFIG = LlamaConfig(
    vocab_size=128256,
    hidden_size=3072,
    intermediate_size=8192,
    num_hidden_layers=28,
    num_attention_heads=24,
    num_key_value_heads=8,
    hf_repo="meta-llama/Llama-3.2-3B",
)


def llama_inv_freq(config: LlamaConfig, device: torch.device) -> torch.Tensor:
    """Return FP32 RoPE inverse frequencies with Llama 3 scaling applied, as Transformers does."""
    head_dim = config.hidden_size // config.num_attention_heads
    inv_freq = config.rope_theta ** (
        -torch.arange(0, head_dim, 2, device=device, dtype=torch.float32) / head_dim
    )
    scaling = config.rope_scaling
    if scaling is None:
        return inv_freq
    old_context = scaling.original_max_position_embeddings
    low_freq_wavelen = old_context / scaling.low_freq_factor
    high_freq_wavelen = old_context / scaling.high_freq_factor
    wavelen = 2 * math.pi / inv_freq
    scaled = torch.where(wavelen > low_freq_wavelen, inv_freq / scaling.factor, inv_freq)
    smooth = (old_context / wavelen - scaling.low_freq_factor) / (
        scaling.high_freq_factor - scaling.low_freq_factor
    )
    smoothed = (1 - smooth) * scaled / scaling.factor + smooth * scaled
    medium = (wavelen >= high_freq_wavelen) & (wavelen <= low_freq_wavelen)
    return torch.where(medium, smoothed, scaled)


class LlamaDecoderLayer(nn.Module):
    """Apply pre-normalized attention and SwiGLU branches with residual connections.

    Args:
        config (`LlamaConfig`): Attention, MLP and normalization dimensions.
    """

    def __init__(self, config: LlamaConfig) -> None:
        """Construct the input norm, attention, post-attention norm and MLP."""
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
        attention_mask: torch.Tensor | PackedSequences | None,
    ) -> torch.Tensor:
        """Return updated hidden states with the same shape as `x`.

        Args:
            x (`torch.Tensor`): Hidden states shaped `[batch, sequence, hidden]`.
            cos (`torch.Tensor`): RoPE cosines broadcastable to attention heads.
            sin (`torch.Tensor`): RoPE sines with the same shape as `cos`.
            attention_mask (`torch.Tensor`, `PackedSequences` or `None`):
                Mask prepared for the selected attention implementation.

        Returns:
            `torch.Tensor`: Hidden states shaped `[batch, sequence, hidden]`.
        """
        x = x + self.self_attn(
            self.input_layernorm(x),
            position_embeddings=(cos, sin),
            attention_mask=attention_mask,
            is_causal=True,
        )
        return x + self.mlp(self.post_attention_layernorm(x))


class LlamaModel(nn.Module):
    """Embed tokens and apply decoder layers followed by final RMS normalization.

    Args:
        config (`LlamaConfig`): Architecture and rotary embedding settings.
    """

    def __init__(self, config: LlamaConfig) -> None:
        """Construct embeddings, decoder layers and final normalization."""
        super().__init__()
        self.config = config
        self.mask_preparer = AttentionInterface.get_mask_preparer(config.attn_implementation)
        self.embed_tokens = nn.Embedding(config.vocab_size, config.hidden_size)
        self.layers = nn.ModuleList(
            LlamaDecoderLayer(config) for _ in range(config.num_hidden_layers)
        )
        self.norm = nn.RMSNorm(config.hidden_size, eps=config.rms_norm_eps)

    def forward(  # ty: ignore[invalid-mutable-override]
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor | None,
        position_ids: torch.Tensor,
        packed: PackedSequences | None = None,
    ) -> torch.Tensor:
        """Return hidden states using RoPE frequencies computed in FP32.

        Args:
            input_ids (`torch.Tensor`): Token IDs shaped `[batch, sequence]`.
            attention_mask (`torch.Tensor` or `None`): Boolean padding mask shaped
                `[batch, sequence]`; `None` means every token is valid.
            position_ids (`torch.Tensor`): Positions shaped `[batch, sequence]`.
            packed (`PackedSequences`, *optional*): Document boundaries of a packed row.

        Returns:
            `torch.Tensor`: Normalized states shaped `[batch, sequence, hidden]`.
        """
        mask = self.mask_preparer(attention_mask, is_causal=True, packed=packed)
        x: Any = self.embed_tokens(input_ids)
        # Build frequencies in float32 even when model weights are cast to bfloat16.
        freqs = position_ids[..., None].float() * llama_inv_freq(self.config, x.device)
        angles = torch.cat((freqs, freqs), dim=-1).unsqueeze(1)
        cos, sin = angles.cos(), angles.sin()
        for layer in self.layers:
            x: Any = layer(x, cos, sin, mask)
        return self.norm(x)


class Llama(BaseModel):
    """Compute full-sequence Llama 3.2-style causal language-model logits.

    `hf_repo` selects the tokenizer only; construction initializes fresh weights.
    `init_seed` controls initialization independently of the caller's RNG state.
    Parameter names match Hugging Face's LlamaForCausalLM state dict.

    Args:
        config ([`training.LlamaConfig`]): Architecture, tokenizer and initialization settings.

    The inherited `forward` returns `[batch, sequence, vocabulary]` logits. With
    `tie_word_embeddings`, the output projection reuses the input embedding.
    Packed batches are supported with every registered attention backend.
    """

    def __init__(self, config: LlamaConfig) -> None:
        """Initialize the decoder and vocabulary projection with the configured seed."""
        super().__init__(
            max_position_embeddings=config.max_position_embeddings,
            hf_repo=config.hf_repo,
            initializer_range=config.initializer_range,
            init_seed=config.init_seed,
        )
        self.config = config
        with self._initialize_weights():
            self.model = LlamaModel(config)
            self.lm_head = nn.Linear(config.hidden_size, config.vocab_size, bias=False)
        if config.tie_word_embeddings:
            # Tie after initialization so the shared weight keeps the embedding's draw.
            self.lm_head.weight = self.model.embed_tokens.weight

    def _forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
        position_ids: torch.Tensor,
    ) -> torch.Tensor:
        """Project validated decoder outputs to full-sequence vocabulary logits."""
        return self.lm_head(self.model(input_ids, attention_mask, position_ids))

    def _forward_packed(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
        position_ids: torch.Tensor,
        packed: PackedSequences,
    ) -> torch.Tensor:
        """Project a packed row's decoder outputs, keeping attention inside documents."""
        return self.lm_head(self.model(input_ids, attention_mask, position_ids, packed))
