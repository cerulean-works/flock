from __future__ import annotations

import json
from dataclasses import asdict, replace
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
import torch
from huggingface_hub import constants
from torch.nn import functional as F

from flock.training.core.attention import AttentionInterface, prepare_sdpa_mask, sdpa_attention
from flock.training.core.data_utils import Batch, LanceBatchCollator
from flock.training.core.models.olmo2 import OLMO2_1B_CONFIG, OLMO2_7B_CONFIG, Olmo2
from tests.training._reference import olmo2_training_parity

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any

    from transformers import TokenizersBackend

    from flock.training.core.models.olmo2 import Olmo2Config


def make_batch(input_ids: torch.Tensor, attention_mask: torch.Tensor | None = None) -> Batch:
    return {
        "input_ids": input_ids,
        "attention_mask": torch.ones_like(input_ids) if attention_mask is None else attention_mask,
        "labels": torch.full_like(input_ids, -100),
    }


@pytest.mark.parametrize("kv_heads", [4, 2, 1], ids=["mha", "gqa", "mqa"])
@pytest.mark.parametrize("padding", ["none", "left", "right"])
def test_huggingface_training_parity(config: Olmo2Config, kv_heads: int, padding: str) -> None:
    olmo2_training_parity(
        replace(config, num_key_value_heads=kv_heads),
        padding,
        torch.device("cpu"),
        torch.float32,
    )


def test_causality_and_padding_invariance(config: Olmo2Config) -> None:
    model = Olmo2(config).eval()
    ids = torch.tensor([[2, 3, 4, 5]])
    with torch.no_grad():
        expected = model(make_batch(ids))
        changed = model(make_batch(torch.tensor([[2, 3, 20, 21]])))
        torch.testing.assert_close(expected[:, :2], changed[:, :2])
        for padded, mask, start in (
            ([[0, 0, 2, 3, 4, 5]], [[0, 0, 1, 1, 1, 1]], 2),
            ([[2, 3, 4, 5, 0, 0]], [[1, 1, 1, 1, 0, 0]], 0),
        ):
            actual = model(make_batch(torch.tensor(padded), torch.tensor(mask)))
            torch.testing.assert_close(actual[:, start : start + 4], expected)


def test_attn_implementation_survives_config_round_trip_and_reaches_every_layer(
    config, monkeypatch
):
    calls = []

    def recording_attention(q, k, v, *, attention_mask=None, is_causal=False):
        calls.append(attention_mask)
        return sdpa_attention(q, k, v, attention_mask=attention_mask, is_causal=is_causal)

    monkeypatch.setattr(
        AttentionInterface, "_global_mapping", AttentionInterface._global_mapping.copy()
    )
    AttentionInterface.register("recording", recording_attention, mask_preparer=prepare_sdpa_mask)
    selected_config = replace(config, attn_implementation="recording")
    restored_config = type(config)(**json.loads(json.dumps(asdict(selected_config))))
    assert restored_config.attn_implementation == "recording"
    model = Olmo2(restored_config)
    reference = Olmo2(config)
    reference.load_state_dict(model.state_dict(), strict=True)
    batch = make_batch(torch.tensor([[0, 2, 3, 4]]), torch.tensor([[0, 1, 1, 1]]))
    torch.testing.assert_close(model(batch), reference(batch))
    assert len(calls) == config.num_hidden_layers


def test_config_without_attn_implementation_defaults_to_sdpa(config):
    saved = asdict(config)
    del saved["attn_implementation"]
    restored = type(config)(**saved)
    model = Olmo2(restored)
    assert restored.attn_implementation == "sdpa"
    batch = make_batch(torch.tensor([[2, 3, 4]]))
    torch.testing.assert_close(model(batch), Olmo2(config)(batch))


def test_model_resolves_mask_preparer_at_construction_and_prepares_once_per_forward(
    config, monkeypatch
):
    preparations = []
    calls = []

    def prepare(padding_mask, *, is_causal):
        assert padding_mask.ndim == 2 and padding_mask.dtype == torch.bool
        assert is_causal is True
        result = prepare_sdpa_mask(padding_mask, is_causal=is_causal)
        preparations.append(result)
        return result

    def compute(q, k, v, *, attention_mask=None, is_causal=False):
        assert attention_mask is preparations[-1]
        assert is_causal is True
        calls.append((attention_mask, is_causal))
        return sdpa_attention(q, k, v, attention_mask=attention_mask, is_causal=is_causal)

    def replacement(*args, **kwargs):
        pytest.fail("an existing model looked up a replaced backend")

    monkeypatch.setattr(
        AttentionInterface, "_global_mapping", AttentionInterface._global_mapping.copy()
    )
    AttentionInterface.register("prepared", compute, mask_preparer=prepare)
    model = Olmo2(replace(config, attn_implementation="prepared"))
    reference = Olmo2(config)
    AttentionInterface.register("prepared", replacement, mask_preparer=replacement)
    assert AttentionInterface.get_interface("prepared") is replacement
    assert AttentionInterface.get_mask_preparer("prepared") is replacement

    for count, mask in enumerate(([[0, 1, 1, 0]], [[1, 1, 1, 1]]), start=1):
        batch = make_batch(torch.tensor([[0, 2, 3, 0]]), torch.tensor(mask))
        torch.testing.assert_close(model(batch), reference(batch))
        assert len(preparations) == count
        assert len(calls) == count * config.num_hidden_layers


@pytest.fixture
def sentencepiece_class(monkeypatch: pytest.MonkeyPatch) -> None:
    """`AutoTokenizer` gives a tokenizer whose class is built on a SentencePiece model."""
    tokenizer = SimpleNamespace(vocab_files_names={"vocab_file": "spiece.model"})
    monkeypatch.setattr(
        "flock.training.core.models.base.AutoTokenizer.from_pretrained",
        lambda repo, **kwargs: tokenizer,
    )


def test_a_conversion_in_a_local_folder_is_refused(
    config: Olmo2Config, tmp_path: Path, sentencepiece_class: None
) -> None:
    model = Olmo2(replace(config, hf_repo=str(tmp_path)))
    with pytest.raises(ValueError, match="without tokenizer.json: SimpleNamespace would be"):
        model.get_hf_tokenizer()


@pytest.mark.parametrize("record", [False, True], ids=["unrecorded", "recorded"])
def test_a_conversion_from_the_cache_is_refused(
    config: Olmo2Config,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sentencepiece_class: None,
    record: bool,
) -> None:
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(tmp_path))
    sha = "a" * 40
    repo = tmp_path / "models--org--repo"
    (repo / "refs").mkdir(parents=True)
    (repo / "refs" / "main").write_text(sha)
    (repo / "snapshots" / sha).mkdir(parents=True)
    (repo / "snapshots" / sha / "spiece.model").write_bytes(b"")
    if record:
        (repo / ".no_exist" / sha).mkdir(parents=True)
        (repo / ".no_exist" / sha / "tokenizer.json").touch()
    model = Olmo2(replace(config, hf_repo="org/repo"))
    with pytest.raises(ValueError, match="without tokenizer.json"):
        model.get_hf_tokenizer()


def test_a_sentencepiece_class_with_tokenizer_json_is_not_a_conversion(
    config: Olmo2Config,
    tokenizer: TokenizersBackend,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "flock.training.core.models.base.AutoTokenizer.from_pretrained",
        lambda repo, **kwargs: tokenizer,
    )
    assert tokenizer.vocab_files_names["vocab_file"].endswith(".model")
    (tmp_path / "tokenizer.json").write_text("{}")
    model = Olmo2(replace(config, hf_repo=str(tmp_path)))
    assert model.get_hf_tokenizer() is tokenizer


def test_a_class_with_other_vocabulary_files_is_not_a_conversion(
    config: Olmo2Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The check reads the class, so one naming no SentencePiece file passes. The type check is next.
    tokenizer = SimpleNamespace(vocab_files_names={"vocab_file": "vocab.json"})
    monkeypatch.setattr(
        "flock.training.core.models.base.AutoTokenizer.from_pretrained",
        lambda repo, **kwargs: tokenizer,
    )
    (tmp_path / "tokenizer.model").write_bytes(b"")
    model = Olmo2(replace(config, hf_repo=str(tmp_path)))
    with pytest.raises(TypeError, match="did not produce a TokenizersBackend"):
        model.get_hf_tokenizer()


def test_collated_training_batch(
    config: Olmo2Config,
    tokenizer: TokenizersBackend,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "flock.training.core.models.base.AutoTokenizer.from_pretrained",
        lambda repo, **kwargs: tokenizer,
    )
    (tmp_path / "tokenizer.json").write_text("{}")
    model = Olmo2(replace(config, hf_repo=str(tmp_path)))
    loaded_tokenizer = model.get_hf_tokenizer()
    assert loaded_tokenizer is not None
    loaded_tokenizer.padding_side = "left"
    batch = LanceBatchCollator(loaded_tokenizer, "text").collate_fn(
        [{"text": "a b c"}, {"text": "b c"}]
    )
    assert batch["input_ids"].shape == batch["attention_mask"].shape == batch["labels"].shape
    assert batch["labels"].tolist() == [[3, 4], [-100, 4]]
    logits = model(batch)
    loss = F.cross_entropy(logits.flatten(0, 1), batch["labels"].flatten())
    loss.backward()
    assert torch.isfinite(loss)
    assert all(p.grad is not None and p.grad.isfinite().all() for p in model.parameters())


@pytest.mark.parametrize("dtype", [torch.float32, torch.bfloat16])
def test_fully_masked_rows_are_finite(config: Olmo2Config, dtype: torch.dtype) -> None:
    model = Olmo2(config).to(dtype=dtype)
    ids = torch.tensor([[0, 0, 0], [2, 3, 4]])
    logits = model(make_batch(ids, torch.tensor([[0, 0, 0], [1, 1, 1]])))
    assert logits.dtype == dtype
    assert logits.isfinite().all()
    logits.float().square().mean().backward()
    assert all(p.grad is not None and p.grad.isfinite().all() for p in model.parameters())


@pytest.mark.parametrize(
    "config, expected_count",
    [
        (OLMO2_1B_CONFIG, 1_484_916_736),
        (OLMO2_7B_CONFIG, 7_298_617_344),
    ],
)
def test_presets(config: Olmo2Config, expected_count: int) -> None:
    # Validate full-size shapes without allocating billions of weights.
    with torch.device("meta"):
        model = Olmo2(config)
    assert sum(p.numel() for p in model.parameters()) == expected_count
    assert model.lm_head.weight is not model.model.embed_tokens.weight
    assert config.max_position_embeddings == 4096


@pytest.mark.parametrize(
    "changes",
    [
        {"num_hidden_layers": 0},
        {"num_key_value_heads": 0},
        {"hidden_size": 31},
        {"num_key_value_heads": 3},
        {"hidden_size": 28},
        {"rms_norm_eps": 0},
    ],
)
def test_invalid_config(config: Olmo2Config, changes: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        replace(config, **changes)


def test_invalid_input_shapes(config: Olmo2Config) -> None:
    model = Olmo2(config)
    for ids in (torch.ones(2, dtype=torch.long), torch.ones(1, 17, dtype=torch.long)):
        with pytest.raises(ValueError):
            model(make_batch(ids))
    ids = torch.ones(1, 3, dtype=torch.long)
    with pytest.raises(ValueError, match="attention_mask"):
        model(make_batch(ids, attention_mask=torch.ones(1, 4)))


def test_tokenizer_config(
    config: Olmo2Config,
    tokenizer: TokenizersBackend,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    tokenizer.pad_token = None

    def load(repo: str, **kwargs: Any) -> TokenizersBackend:
        calls.append((repo, kwargs))
        return tokenizer

    monkeypatch.setattr("flock.training.core.models.base.AutoTokenizer.from_pretrained", load)
    assert Olmo2(config).get_hf_tokenizer() is None
    (tmp_path / "tokenizer.json").write_text("{}")
    model = Olmo2(replace(config, hf_repo=str(tmp_path)))
    assert model.get_hf_tokenizer() is tokenizer
    assert tokenizer.pad_token_id == tokenizer.eos_token_id
    assert calls == [
        (str(tmp_path), {"padding_side": "left", "model_max_length": 16, "add_eos_token": True})
    ]


def test_initialization_is_independent_of_global_rng(config: Olmo2Config) -> None:
    torch.manual_seed(1)
    first = Olmo2(config)
    torch.randn(100)
    torch.manual_seed(999)
    second = Olmo2(config)
    for name, weight in first.state_dict().items():
        assert torch.equal(weight, second.state_dict()[name]), name
    # A single stream gives distinct weights to different layers/projections.
    assert not torch.equal(
        first.get_parameter("model.layers.0.self_attn.q_proj.weight"),
        first.get_parameter("model.layers.1.self_attn.q_proj.weight"),
    )
    assert not torch.equal(first.model.embed_tokens.weight, first.lm_head.weight)


def test_explicit_init_seed(config: Olmo2Config) -> None:
    first = Olmo2(replace(config, init_seed=123))
    second = Olmo2(replace(config, init_seed=123))
    different = Olmo2(replace(config, init_seed=456))
    for name, weight in first.state_dict().items():
        assert torch.equal(weight, second.state_dict()[name]), name
    assert not torch.equal(first.lm_head.weight, different.lm_head.weight)


@pytest.mark.parametrize("device", ["cpu", "meta"])
def test_initialization_preserves_global_rng(config: Olmo2Config, device: str) -> None:
    before = torch.get_rng_state().clone()
    with torch.device(device):
        Olmo2(config)
    assert torch.equal(torch.get_rng_state(), before)
