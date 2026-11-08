"""Pinned existing OLMo 2 architecture, constructed without downloading weights."""

from transformers import Olmo2Config

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
