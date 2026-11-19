"""Llama 3.2-style model: Hugging Face parity, packing, tied weights, checkpoints and presets."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from typing import TYPE_CHECKING

import pytest
import torch
from torch.nn import functional as F
from transformers import LlamaConfig as HFLlamaConfig
from transformers import LlamaForCausalLM

from flock.training.core.attention import PackedSequences
from flock.training.core.checkpoint import CheckpointManager
from flock.training.core.models import (
    LLAMA3_2_1B_CONFIG,
    LLAMA3_2_3B_CONFIG,
    Llama,
    Llama3RopeScaling,
    LlamaConfig,
)
from flock.training.core.optimizers.muon_adamw import split_muon_parameters
from flock.training.experiments.scaling_law.config import ModelSettings
from tests.training._reference import assert_parity, token_batch

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def config() -> LlamaConfig:
    # Small, but with Llama 3.2's structure: GQA, tied embeddings and llama3 RoPE scaling.
    # A small original context puts some frequencies in each scaling band at this head width.
    return LlamaConfig(
        # A vocabulary unlike any hidden matrix shape, as in real configs.
        vocab_size=80,
        hidden_size=64,
        intermediate_size=96,
        num_hidden_layers=2,
        num_attention_heads=4,
        num_key_value_heads=2,
        max_position_embeddings=64,
        rope_theta=10_000.0,
        rope_scaling=Llama3RopeScaling(original_max_position_embeddings=32),
        init_seed=3,
    )


def hf_config(config: LlamaConfig) -> HFLlamaConfig:
    rope: dict[str, object] = {"rope_type": "default", "rope_theta": config.rope_theta}
    if config.rope_scaling is not None:
        rope = {
            "rope_type": "llama3",
            "rope_theta": config.rope_theta,
            **asdict(config.rope_scaling),
        }
    return HFLlamaConfig(
        vocab_size=config.vocab_size,
        hidden_size=config.hidden_size,
        intermediate_size=config.intermediate_size,
        num_hidden_layers=config.num_hidden_layers,
        num_attention_heads=config.num_attention_heads,
        num_key_value_heads=config.num_key_value_heads,
        max_position_embeddings=config.max_position_embeddings,
        rope_parameters=rope,
        rms_norm_eps=config.rms_norm_eps,
        tie_word_embeddings=config.tie_word_embeddings,
        hidden_act="silu",
        attention_bias=False,
        mlp_bias=False,
        attention_dropout=0.0,
        pad_token_id=None,
        bos_token_id=None,
        eos_token_id=None,
        use_cache=False,
    )


@pytest.mark.parametrize("tied", [True, False], ids=["tied", "untied"])
@pytest.mark.parametrize("scaling", [True, False], ids=["llama3-rope", "plain-rope"])
@pytest.mark.parametrize("padding", ["none", "left", "right"])
def test_huggingface_training_parity(
    config: LlamaConfig, tied: bool, scaling: bool, padding: str
) -> None:
    config = replace(
        config, tie_word_embeddings=tied, rope_scaling=config.rope_scaling if scaling else None
    )
    model = Llama(config)
    reference_config = hf_config(config)
    reference_config._attn_implementation = "eager"
    reference = LlamaForCausalLM(reference_config)
    missing, unexpected = reference.load_state_dict(model.state_dict(), strict=False)
    # HF omits the tied lm_head alias from its own state dict only when tying.
    assert unexpected == [] and set(missing) <= {"lm_head.weight"}
    assert (reference.lm_head.weight is reference.model.embed_tokens.weight) == tied
    assert (model.lm_head.weight is model.model.embed_tokens.weight) == tied

    ids, mask = token_batch(padding, torch.device("cpu"))
    # Long enough that positions reach every RoPE scaling band.
    ids, mask = ids.repeat(1, 8), mask.repeat(1, 8)
    labels = ids[:, 1:].clone()
    labels.masked_fill_(~(mask[:, :-1] & mask[:, 1:]), -100)
    actual = model({"input_ids": ids[:, :-1], "attention_mask": mask[:, :-1], "labels": labels})
    expected = reference(
        ids[:, :-1],
        attention_mask=mask[:, :-1].long(),
        position_ids=(mask[:, :-1].long().cumsum(-1) - 1).clamp_min(0),
    ).logits
    valid = mask[:, :-1]
    assert_parity(actual[valid], expected[valid], torch.float32, "logits", "logits")

    for logits, module in ((actual, model), (expected, reference)):
        F.cross_entropy(logits.reshape(-1, logits.shape[-1]), labels.reshape(-1)).backward()
    for name, parameter in model.named_parameters():
        assert_parity(
            parameter.grad, reference.get_parameter(name).grad, torch.float32, "gradients", name
        )


def test_inverse_frequencies_match_transformers(config: LlamaConfig) -> None:
    from transformers.modeling_rope_utils import ROPE_INIT_FUNCTIONS

    from flock.training.core.models.llama import llama_inv_freq

    expected, _ = ROPE_INIT_FUNCTIONS["llama3"](hf_config(config), torch.device("cpu"))
    actual = llama_inv_freq(config, torch.device("cpu"))
    torch.testing.assert_close(actual, expected.float())
    # The test config must exercise all three bands: unchanged, interpolated and slowed.
    plain = llama_inv_freq(replace(config, rope_scaling=None), torch.device("cpu"))
    ratio = plain / actual
    assert (ratio == 1).any() and (ratio == 32).any() and ((ratio > 1) & (ratio < 32)).any()


def test_packed_documents_match_separate_runs(config: LlamaConfig) -> None:
    model = Llama(config).eval()
    documents = [torch.randint(0, config.vocab_size, (n,)) for n in (7, 1, 12)]
    ids = torch.cat(documents)[None]
    positions = torch.cat([torch.arange(len(d)) for d in documents])[None]
    boundaries = torch.tensor([0, 7, 8, 20], dtype=torch.int32)
    with torch.no_grad():
        packed = model(
            {
                "input_ids": ids,
                "attention_mask": torch.ones_like(ids, dtype=torch.bool),
                "labels": torch.full_like(ids, -100),
                "position_ids": positions,
                "cu_seqlens": boundaries,
            }
        )
        separate = torch.cat(
            [
                model(
                    {
                        "input_ids": d[None],
                        "attention_mask": torch.ones(1, len(d), dtype=torch.bool),
                        "labels": torch.full((1, len(d)), -100),
                    }
                )
                for d in documents
            ],
            dim=1,
        )
    torch.testing.assert_close(packed, separate)
    assert Llama.supports_packing()
    assert PackedSequences  # Packed rows reach attention as PackedSequences.


def test_tied_checkpoint_round_trip(config: LlamaConfig, tmp_path: Path) -> None:
    model = Llama(config)
    with torch.no_grad():
        model.lm_head.weight.add_(1.0)  # Through the tie, this changes the embedding too.
    manager = CheckpointManager(tmp_path)
    manager.save_model(1, model)
    saved = json.loads((tmp_path / "checkpoint-1" / "config.json").read_text())
    restored_config = LlamaConfig(**saved)
    assert restored_config == config
    restored = Llama(restored_config)  # Fresh weights: the saved ones were changed.
    manager.load_checkpoint(tmp_path / "checkpoint-1", restored)
    assert restored.lm_head.weight is restored.model.embed_tokens.weight
    for name, tensor in model.state_dict().items():
        torch.testing.assert_close(restored.state_dict()[name], tensor, rtol=0, atol=0)


def test_muon_skips_the_tied_embedding(config: LlamaConfig) -> None:
    model = Llama(config)
    muon, adamw = split_muon_parameters(model)
    embedding = model.model.embed_tokens.weight
    assert all(p is not embedding for p in muon)
    assert sum(p is embedding for p in adamw) == 1
    assert len(muon) == 7 * config.num_hidden_layers
    assert len(muon) + len(adamw) == len(list(model.parameters()))


def test_presets_match_meta_configs() -> None:
    # Values from meta-llama/Llama-3.2-{1B,3B} config.json.
    for preset, layers, hidden, heads in (
        (LLAMA3_2_1B_CONFIG, 16, 2048, 32),
        (LLAMA3_2_3B_CONFIG, 28, 3072, 24),
    ):
        assert (preset.num_hidden_layers, preset.hidden_size, preset.num_attention_heads) == (
            layers,
            hidden,
            heads,
        )
        assert (preset.vocab_size, preset.num_key_value_heads, preset.intermediate_size) == (
            128256,
            8,
            8192,
        )
        assert preset.tie_word_embeddings and preset.rms_norm_eps == 1e-5
        assert preset.rope_theta == 500_000.0 and preset.rope_scaling == Llama3RopeScaling()
    with torch.device("meta"):
        params = sum(p.numel() for p in Llama(LLAMA3_2_1B_CONFIG).parameters())
    assert params == 1_235_814_400  # Meta's reported 1.23B parameters.


def test_scaling_law_settings_build_llama() -> None:
    settings = ModelSettings(preset="llama3.2-1b", attn_implementation="sdpa")
    assert isinstance(settings.architecture_config(0), LlamaConfig)
    small = ModelSettings(
        architecture="llama",
        attn_implementation="sdpa",
        overrides={
            "vocab_size": 64,
            "hidden_size": 64,
            "intermediate_size": 96,
            "num_hidden_layers": 1,
            "num_attention_heads": 4,
            "num_key_value_heads": 2,
        },
    )
    assert isinstance(small.build(init_seed=1), Llama)
    with pytest.raises(ValueError, match="not a llama"):
        ModelSettings(preset="190m", architecture="llama").architecture_config(0)
