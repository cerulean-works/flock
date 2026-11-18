"""Pinned existing OLMo 2 architecture, constructed without downloading weights."""

from transformers import Olmo2Config

from flock.training.experiments.proxy.olmo2_proxy import PROXY_SIZES

MODEL_REPO = "allenai/OLMo-2-0425-1B"
MODEL_REVISION = "a1847dff35000b4271fa70afc5db10fd29fedbdf"


def existing_config() -> Olmo2Config:
    """Return the selected existing architecture for fresh initialization."""
    config = Olmo2Config(
        vocab_size=100352,
        hidden_size=2048,
        intermediate_size=8192,
        num_hidden_layers=16,
        num_attention_heads=16,
        num_key_value_heads=16,
        max_position_embeddings=4096,
        rope_parameters={"rope_theta": 500000.0, "rope_type": "default"},
        rms_norm_eps=1e-6,
        initializer_range=0.02,
        tie_word_embeddings=False,
        eos_token_id=100257,
        pad_token_id=100277,
        use_cache=False,
    )
    config._attn_implementation = "sdpa"
    return config


def proxy_config(size: str) -> Olmo2Config:
    """Return a flock proxy preset, `"76m"` or `"190m"`, as an HF configuration."""
    preset = PROXY_SIZES[size].model
    base = existing_config()
    # The presets keep the 1B's vocabulary, context, RoPE base, norm and init: only the shape moves.
    if (
        base.vocab_size != preset.vocab_size
        or base.max_position_embeddings != preset.max_position_embeddings
        or base.rope_parameters != {"rope_theta": preset.rope_theta, "rope_type": "default"}
        or base.rms_norm_eps != preset.rms_norm_eps
        or base.initializer_range != preset.initializer_range
    ):
        raise ValueError(f"proxy preset {size} no longer shares the 1B's vocabulary and settings")
    config = Olmo2Config.from_dict(
        base.to_dict()
        | {
            "hidden_size": preset.hidden_size,
            "intermediate_size": preset.intermediate_size,
            "num_hidden_layers": preset.num_hidden_layers,
            "num_attention_heads": preset.num_attention_heads,
            "num_key_value_heads": preset.num_key_value_heads,
        }
    )
    config._attn_implementation = "sdpa"
    return config
