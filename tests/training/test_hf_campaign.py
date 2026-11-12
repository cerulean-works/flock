from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest
import torch
from datasets import Dataset
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace
from transformers import (
    AutoModelForCausalLM,
    Olmo2Config,
    Olmo2ForCausalLM,
    PreTrainedTokenizerFast,
)

from flock.eval.model import ScoringModel
from flock.eval.requests import Request
from flock.training.experiments.hf_campaign.config import existing_config
from flock.training.experiments.hf_campaign.runtime import CpuExperiment
from flock.training.experiments.hf_campaign.stream import Pool, TargetStream

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any


class FixtureEncoder:
    def __init__(self) -> None:
        self.calls = []

    def encode(self, text: str, *, add_special_tokens: bool) -> list[int]:
        assert not add_special_tokens
        self.calls.append(text)
        return [int(word) for word in text.split()]


def record(name: str, text: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": name,
        "family_id": name,
        "text": text,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "source_pin": "fixture@immutable",
        "split": "train",
        "admitted": True,
        "rights_receipt": "fixture-rights-v1",
        "exclusion_receipt": "fixture-heldout-v1",
        **extra,
    }


def stream(block_size: int = 4, quota: int = 32) -> TargetStream:
    pools = []
    for name, bucket in [("native", "tc_native"), ("english", "en")]:
        rows = Dataset.from_list([record(f"{name}-{i}", "2 3 4 5 6 7 8") for i in range(12)])
        pools.append(Pool(name, bucket, rows, quota))
    return TargetStream(
        pools,
        FixtureEncoder(),
        eos_id=1,
        block_size=block_size,
        manifest_hash="fixture-membership-v1",
        exclusion_hash="fixture-exclusion-v1",
        tokenizer_hash="fixture-tokenizer-v1",
    )


def tiny_config() -> Olmo2Config:
    config = Olmo2Config(
        vocab_size=16,
        hidden_size=16,
        intermediate_size=32,
        num_hidden_layers=1,
        num_attention_heads=2,
        num_key_value_heads=2,
        max_position_embeddings=32,
        eos_token_id=1,
        pad_token_id=0,
        use_cache=False,
    )
    config._attn_implementation = "eager"
    return config


def test_selected_config_meta_count() -> None:
    config = existing_config()
    with torch.device("meta"):
        model = Olmo2ForCausalLM(config)
    total = sum(p.numel() for p in model.parameters())
    embeddings = model.model.embed_tokens.weight.numel() + model.lm_head.weight.numel()
    assert total == 1_484_916_736
    assert total - embeddings == 1_073_874_944
    assert config.hidden_size // config.num_attention_heads == 128


@pytest.mark.parametrize("length", [1, 3, 4, 7])
def test_stream_conserves_targets_and_resume(length: int) -> None:
    producer = stream(length, quota=17)
    first = producer.blocks("tc", 7)
    saved = producer.state_dict()
    expected = producer.blocks("tc", 10)
    producer.load_state_dict(saved)
    assert producer.blocks("tc", 10) == expected
    blocks = first + expected
    assert sum(len(b.labels) for b in blocks) == 17
    assert [identity for b in blocks for identity in b.identities] == [
        ("native", f"native-{i // 8}", i % 8) for i in range(1, 18)
    ]
    assert [token for b in blocks for token in b.labels] == ([2, 3, 4, 5, 6, 7, 8, 1] * 3)[1:18]
    assert producer.counters == {"tc_native": 17, "en": 0}
    assert sum(producer.state_dict()["eos"].values()) == 2
    with pytest.raises(RuntimeError, match="quota"):
        producer.blocks("tc", 1)
    changed = stream(length + 1, quota=17)
    with pytest.raises(ValueError, match="contract"):
        changed.load_state_dict(saved)


def test_whole_document_exclusion_precedes_tokenization() -> None:
    encoder = FixtureEncoder()
    rows = [record("heldout", "2 99 4"), record("dev", "5", split="test"), record("ok", "6 7")]
    source = TargetStream(
        [Pool("native", "tc_native", Dataset.from_list(rows), 2)],
        encoder,
        eos_id=1,
        block_size=4,
        manifest_hash="m",
        exclusion_hash="e",
        tokenizer_hash="fixture-tokenizer-v1",
        forbidden_spans=("99",),
    )
    assert source.blocks("tc", 2)[0].labels == [7, 1]
    assert encoder.calls == ["6 7"]
    assert source.state_dict()["rejected"] == 2


def test_converted_original_and_receipts_are_checked() -> None:
    rows = [
        record(
            "rejected",
            "2 3",
            original_text="99",
            original_sha256=hashlib.sha256(b"99").hexdigest(),
            conversion_receipt="conversion-v2",
        ),
        record(
            "ok",
            "4 5",
            original_text="4 5",
            original_sha256=hashlib.sha256(b"4 5").hexdigest(),
            conversion_receipt="conversion-v2",
        ),
    ]
    encoder = FixtureEncoder()
    source = TargetStream(
        [Pool("converted", "tc_converted", Dataset.from_list(rows), 2)],
        encoder,
        eos_id=1,
        block_size=4,
        manifest_hash="m",
        exclusion_hash="e",
        tokenizer_hash="fixture-tokenizer-v1",
        forbidden_spans=("99",),
    )
    assert source.blocks("tc", 2)[0].labels == [5, 1]
    assert encoder.calls == ["4 5"]
    assert source.counters == {"tc_converted": 2}


def test_empty_whitespace_exhaustion_and_literal_eos() -> None:
    source = TargetStream(
        [
            Pool(
                "native",
                "tc_native",
                Dataset.from_list(
                    [record("empty", ""), record("space", " "), record("literal", "1 2")]
                ),
                3,
            )
        ],
        FixtureEncoder(),
        eos_id=1,
        block_size=4,
        manifest_hash="m",
        exclusion_hash="e",
        tokenizer_hash="fixture-tokenizer-v1",
    )
    assert source.blocks("tc", 2)[0].labels == [2, 1]
    with pytest.raises(RuntimeError, match="exhausted"):
        source.blocks("tc", 1)


def test_optimizer_resume_prefetch_corruption_and_export(tmp_path: Path) -> None:
    experiment = CpuExperiment(tiny_config(), stream(), seed=17)
    experiment.update(tc_targets=5, en_targets=5)
    experiment.checkpoint(tmp_path / "checkpoint")
    expected_random = torch.rand(3)
    # Uncommitted prefetch must neither enter the checkpoint nor skip next-update data.
    experiment.stream.blocks("tc", 3)
    expected_loss, expected_blocks = experiment.update(tc_targets=3, en_targets=3)
    expected = {k: v.clone() for k, v in experiment.model.state_dict().items()}
    restored = CpuExperiment(tiny_config(), stream(), seed=999)
    restored.resume(tmp_path / "checkpoint")
    torch.testing.assert_close(torch.rand(3), expected_random, rtol=0, atol=0)
    actual_loss, actual_blocks = restored.update(tc_targets=3, en_targets=3)
    assert actual_loss == expected_loss
    assert actual_blocks == expected_blocks
    assert restored.targets == 16
    assert restored.stream.counters == {"tc_native": 8, "en": 8}
    for key, value in restored.model.state_dict().items():
        torch.testing.assert_close(value, expected[key], rtol=0, atol=0)
    assert (
        restored.optimizer.state_dict()["param_groups"]
        == experiment.optimizer.state_dict()["param_groups"]
    )
    expected_optimizer = experiment.optimizer.state_dict()["state"]
    for parameter, state in restored.optimizer.state_dict()["state"].items():
        for key, value in state.items():
            torch.testing.assert_close(value, expected_optimizer[parameter][key], rtol=0, atol=0)
    changed = CpuExperiment(tiny_config(), stream(), total_targets=128)
    with pytest.raises(ValueError, match="contract"):
        changed.resume(tmp_path / "checkpoint")
    (tmp_path / "interrupted.incomplete").mkdir()
    with pytest.raises(FileExistsError):
        restored.checkpoint(tmp_path / "interrupted")
    tokenizer_backend = Tokenizer(WordLevel({"[UNK]": 0, "[EOS]": 1, "2": 2}, unk_token="[UNK]"))
    tokenizer_backend.pre_tokenizer = Whitespace()
    tokenizer = PreTrainedTokenizerFast(
        tokenizer_object=tokenizer_backend, unk_token="[UNK]", eos_token="[EOS]"
    )
    restored.export(tmp_path / "export", tokenizer)
    reloaded = AutoModelForCausalLM.from_pretrained(tmp_path / "export", local_files_only=True)
    reloaded.set_attn_implementation("eager")
    scorer = ScoringModel(reloaded, tokenizer, name="synthetic-export@terminal")
    assert scorer.model is reloaded
    scores = scorer.loglikelihood([Request("2", " 2")])
    assert torch.isfinite(scores.logprob).all()
    assert scores.num_tokens.tolist() == [1]
    with torch.no_grad():
        ids = torch.tensor([[2, 3, 4]])
        torch.testing.assert_close(reloaded(ids).logits, restored.model(ids).logits, rtol=0, atol=0)
    with pytest.raises(FileExistsError):
        restored.checkpoint(tmp_path / "checkpoint")
    corrupt = tmp_path / "checkpoint" / "model.safetensors"
    corrupt.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        restored.resume(tmp_path / "checkpoint")


def test_duplicate_family_is_rejected_before_encoder() -> None:
    encoder = FixtureEncoder()
    rows = [record("first", "2 3", family_id="same"), record("mirror", "4 5", family_id="same")]
    producer = TargetStream(
        [Pool("native", "tc_native", Dataset.from_list(rows), 3)],
        encoder,
        eos_id=1,
        block_size=4,
        manifest_hash="m",
        exclusion_hash="e",
        tokenizer_hash="t",
    )
    producer.blocks("tc", 2)
    with pytest.raises(RuntimeError, match="exhausted"):
        producer.blocks("tc", 1)
    assert encoder.calls == ["2 3"]


def test_aligned_loss_uses_every_target_and_exact_terminal_budget() -> None:
    experiment = CpuExperiment(tiny_config(), stream(), seed=17, total_targets=10, warmup_targets=2)
    oracle = stream()
    blocks = oracle.blocks("tc", 3) + oracle.blocks("en", 3)
    expected = 0.0
    with torch.no_grad():
        for block in blocks:
            logits = experiment.model(torch.tensor([block.inputs]), use_cache=False).logits
            logprobs = logits.float().log_softmax(-1)[0]
            expected += (
                sum(-float(logprobs[i, target]) for i, target in enumerate(block.labels)) / 6
            )
    actual, _ = experiment.update(tc_targets=3, en_targets=3)
    assert actual == pytest.approx(expected, abs=1e-6)
    experiment.update(tc_targets=2, en_targets=2)
    assert experiment.targets == 10
    with pytest.raises(ValueError, match="overshoot"):
        experiment.update(tc_targets=1, en_targets=1)


def test_nullable_mixed_schema_and_missing_converted_original_quarantine() -> None:
    encoder = FixtureEncoder()
    native = Dataset.from_list([record("native", "2 3", original_text=None)])
    converted = Dataset.from_list(
        [
            record(
                "missing",
                "4 5",
                original_text=None,
                original_sha256="invalid",
                conversion_receipt="c",
            ),
            {**record("bad-text", "6"), "text": None},
            record(
                "valid",
                "7 8",
                original_text="7 8",
                original_sha256=hashlib.sha256(b"7 8").hexdigest(),
                conversion_receipt="c",
            ),
        ]
    )
    producer = TargetStream(
        [Pool("native", "tc_native", native, 1), Pool("converted", "tc_converted", converted, 2)],
        encoder,
        eos_id=1,
        block_size=2,
        manifest_hash="m",
        exclusion_hash="e",
        tokenizer_hash="t",
        forbidden_spans=("99",),
    )
    blocks = producer.blocks("tc", 3)
    assert sum(len(b.labels) for b in blocks) == 3
    assert encoder.calls == ["7 8", "2 3"]
    assert producer.state_dict()["rejected"] == 2
