from __future__ import annotations

from dataclasses import replace

import pytest
import torch
from transformers.models.llama.modeling_llama import LlamaRotaryEmbedding

from flock.training.core.models import registry
from flock.training.core.models.llama32 import LLAMA32_1B_CONFIG, Llama32, Llama32Config
from tests.training._reference import llama32_hf_config, llama32_training_parity
from tests.training.test_olmo2 import assert_packed_matches_documents_alone, make_batch

# Architecture fields of Meta's config.json for Llama 3.2 1B.
LLAMA32_1B_HF_CONFIG = {
    "vocab_size": 128256,
    "hidden_size": 2048,
    "intermediate_size": 8192,
    "num_hidden_layers": 16,
    "num_attention_heads": 32,
    "num_key_value_heads": 8,
    "head_dim": 64,
    "max_position_embeddings": 131072,
    "rope_parameters": {
        "rope_type": "llama3",
        "rope_theta": 500000.0,
        "factor": 32.0,
        "low_freq_factor": 1.0,
        "high_freq_factor": 4.0,
        "original_max_position_embeddings": 8192,
    },
    "rms_norm_eps": 1e-05,
    "tie_word_embeddings": True,
    "hidden_act": "silu",
    "attention_bias": False,
    "mlp_bias": False,
    "initializer_range": 0.02,
}


@pytest.mark.parametrize("kv_heads", [4, 2, 1], ids=["mha", "gqa", "mqa"])
@pytest.mark.parametrize("padding", ["none", "left", "right"])
def test_huggingface_training_parity(
    llama32_config: Llama32Config, kv_heads: int, padding: str
) -> None:
    llama32_training_parity(
        replace(llama32_config, num_key_value_heads=kv_heads),
        padding,
        torch.device("cpu"),
        torch.float32,
    )


@pytest.mark.parametrize(
    "changes",
    [{"tie_word_embeddings": False}, {"rope_scaling_factor": None}],
    ids=["untied", "unscaled-rope"],
)
def test_huggingface_training_parity_of_variants(
    llama32_config: Llama32Config, changes: dict[str, object]
) -> None:
    llama32_training_parity(
        replace(llama32_config, **changes), "left", torch.device("cpu"), torch.float32
    )


@pytest.mark.parametrize("scaling_factor", [32.0, None], ids=["llama3", "default"])
def test_rope_frequencies_equal_huggingface(scaling_factor: float | None) -> None:
    config = replace(LLAMA32_1B_CONFIG, rope_scaling_factor=scaling_factor)
    reference = LlamaRotaryEmbedding(llama32_hf_config(config))
    with torch.device("meta"):
        model = Llama32(config)
    assert reference.attention_scaling == 1.0
    assert torch.equal(model.model.rope_inv_freq(torch.device("cpu")), reference.inv_freq)


def test_preset_matches_the_published_config() -> None:
    hf_config = llama32_hf_config(LLAMA32_1B_CONFIG).to_dict()
    for name, value in LLAMA32_1B_HF_CONFIG.items():
        assert hf_config[name] == value, name


def test_preset_shapes_and_tied_embeddings() -> None:
    with torch.device("meta"):
        model = Llama32(LLAMA32_1B_CONFIG)
    assert sum(p.numel() for p in model.parameters()) == 1_235_814_400
    assert model.lm_head.weight is model.model.embed_tokens.weight
    assert set(model.state_dict()) >= {"lm_head.weight", "model.embed_tokens.weight"}
    assert registry["llama32"] is Llama32


def test_flops_per_token_count_as_torchtitan_does() -> None:
    # The tied output projection is a matmul, so the shared weight counts once:
    # 6 × 1,235,814,400 parameters, plus 12 × 16 layers × 2,048 wide × the sequence.
    with torch.device("meta"):
        model = Llama32(LLAMA32_1B_CONFIG)
    assert model.num_flops_per_token(4096) == 9_025_499_136
    assert model.num_flops_per_token(2048) == 9_025_499_136 - 12 * 16 * 2048 * 2048


def test_untied_output_projection(llama32_config: Llama32Config) -> None:
    model = Llama32(replace(llama32_config, tie_word_embeddings=False))
    assert model.lm_head.weight is not model.model.embed_tokens.weight
    assert not torch.equal(model.lm_head.weight, model.model.embed_tokens.weight)


@pytest.mark.parametrize("kv_heads", [4, 2, 1], ids=["mha", "gqa", "mqa"])
def test_packed_sdpa_batch_matches_each_document_alone(
    llama32_config: Llama32Config, kv_heads: int
) -> None:
    model = Llama32(replace(llama32_config, num_key_value_heads=kv_heads))
    assert_packed_matches_documents_alone(model)  # ty: ignore[invalid-argument-type]


def test_causality_and_padding_invariance(llama32_config: Llama32Config) -> None:
    model = Llama32(llama32_config).eval()
    with torch.no_grad():
        expected = model(make_batch(torch.tensor([[2, 3, 4, 5]])))
        changed = model(make_batch(torch.tensor([[2, 3, 20, 21]])))
        torch.testing.assert_close(expected[:, :2], changed[:, :2])
        padded = model(
            make_batch(torch.tensor([[0, 0, 2, 3, 4, 5]]), torch.tensor([[0, 0, 1, 1, 1, 1]]))
        )
        torch.testing.assert_close(padded[:, 2:], expected)


def test_initialization_is_seeded_and_preserves_global_rng(llama32_config: Llama32Config) -> None:
    before = torch.get_rng_state().clone()
    first, second = Llama32(llama32_config), Llama32(llama32_config)
    assert torch.equal(torch.get_rng_state(), before)
    for name, weight in first.state_dict().items():
        assert torch.equal(weight, second.state_dict()[name]), name
    different = Llama32(replace(llama32_config, init_seed=7))
    assert not torch.equal(first.lm_head.weight, different.lm_head.weight)


@pytest.mark.parametrize(
    "changes",
    [
        {"num_hidden_layers": 0},
        {"num_key_value_heads": 3},
        {"hidden_size": 28},
        {"rms_norm_eps": 0},
        {"rope_scaling_factor": 0},
        {"rope_low_freq_factor": 4.0},
        {"rope_original_max_position_embeddings": 0},
    ],
)
def test_invalid_config(llama32_config: Llama32Config, changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        replace(llama32_config, **changes)
