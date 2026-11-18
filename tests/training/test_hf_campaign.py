from __future__ import annotations

import hashlib
import json
import math
from functools import partial
from typing import TYPE_CHECKING

import lance
import pyarrow as pa
import pytest
import torch
from accelerate import Accelerator
from accelerate.state import AcceleratorState
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
from flock.training import DatasetConfig
from flock.training.experiments.hf_campaign import train
from flock.training.experiments.hf_campaign.config import existing_config, proxy_config
from flock.training.experiments.hf_campaign.provisional import ProvisionalMix
from flock.training.experiments.hf_campaign.runtime import (
    CpuExperiment,
    Experiment,
    GpuExperiment,
)
from flock.training.experiments.hf_campaign.stream import Pool, TargetStream

if TYPE_CHECKING:
    from collections.abc import Iterator
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


def word_tokenizer() -> PreTrainedTokenizerFast:
    backend = Tokenizer(
        WordLevel({"[UNK]": 0, "[EOS]": 1} | {str(i): i for i in range(2, 10)}, unk_token="[UNK]")
    )
    backend.pre_tokenizer = Whitespace()
    return PreTrainedTokenizerFast(tokenizer_object=backend, unk_token="[UNK]", eos_token="[EOS]")


def write_mix(path: Path, texts: list[str], **columns: list[Any]) -> DatasetConfig:
    # A mix's tokens column counts each document's EOS (harvest's pool writer).
    tokens = columns.pop("tokens", [len(text.split()) + 1 for text in texts])
    lance.write_dataset(pa.table({"text": texts, "tokens": tokens, **columns}), path)
    return DatasetConfig(uri=str(path), version=1)


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
    with pytest.raises(ValueError, match="contract"):  # the seed is part of the run
        CpuExperiment(tiny_config(), stream(), seed=999).resume(tmp_path / "checkpoint")
    restored = CpuExperiment(tiny_config(), stream(), seed=17)
    torch.rand(5)  # moves the global RNG, which the checkpoint must restore
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
    tokenizer = word_tokenizer()
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
    # Past the endpoint and the source quota alike: the endpoint is checked before any read.
    with pytest.raises(ValueError, match="overshoot"):
        experiment.update(tc_targets=40, en_targets=40)


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


def sdpa_config(context: int = 32) -> Olmo2Config:
    config = tiny_config()
    config.max_position_embeddings = context
    config._attn_implementation = "sdpa"
    return config


@pytest.fixture
def fresh_accelerator_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Accelerate keeps one process-wide precision; the GPU runtime asks for BF16, the CPU one FP32.
    # The GPU runtime's tests run on the CPU even where a GPU is visible.
    monkeypatch.setenv("ACCELERATE_USE_CPU", "1")
    AcceleratorState._reset_state(True)
    yield
    AcceleratorState._reset_state(True)


@pytest.mark.parametrize(("size", "count"), [("76m", 76_308_864), ("190m", 190_354_176)])
def test_proxy_configs_keep_the_flock_presets_parameter_counts(size: str, count: int) -> None:
    with torch.device("meta"):
        model = Olmo2ForCausalLM(proxy_config(size))
    total = sum(p.numel() for p in model.parameters())
    assert total - model.model.embed_tokens.weight.numel() == count  # the OLMo ladder's N
    assert model.config.max_position_embeddings == 4096
    assert model.config._attn_implementation == "sdpa"


def test_provisional_mix_packs_documents_in_stored_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Stream: 2 3 4 E | 5 E | 6 7 8 9 2 3 E | 4 5 6 E, 17 tokens and 16 targets.
    config = write_mix(tmp_path / "mix.lance", ["2 3 4", "5", "6 7 8 9 2 3", "4 5 6"])
    mix = ProvisionalMix(config, word_tokenizer(), window=3, rows_per_update=2)
    assert mix.targets_per_update == 6
    assert len(mix) == 2  # 16 // 6: the partial third update is never emitted
    assert mix[0].tolist() == [[2, 3, 4, 1], [1, 5, 1, 6]]  # rows share their boundary token
    assert mix[1].tolist() == [[6, 7, 8, 9], [9, 2, 3, 1]]
    with pytest.raises(IndexError):
        mix[2]
    assert mix.contract["provisional"] is True
    assert mix.contract["stream_tokens"] == 17
    # The ids, not only the tokenizer's name, are part of the contract.
    other = word_tokenizer()
    other.backend_tokenizer.add_tokens(["10"])
    remix = ProvisionalMix(config, other, window=3, rows_per_update=2)
    assert remix.contract["tokenizer_sha256"] != mix.contract["tokenizer_sha256"]

    # A tag and a relative path are pinned to the version number and an absolute path.
    lance.dataset(config.uri).tags.create("current", 1)
    monkeypatch.chdir(tmp_path)
    tagged = ProvisionalMix(
        DatasetConfig(uri="mix.lance", version="current"),
        word_tokenizer(),
        window=3,
        rows_per_update=2,
    )
    assert (tagged.contract["uri"], tagged.contract["version"]) == (config.uri, 1)
    assert tagged[1].tolist() == mix[1].tolist()


def test_provisional_mix_rejects_rows_its_counts_or_hashes_contradict(tmp_path: Path) -> None:
    texts = ["2 3 4", "5 6"]
    miscounted = write_mix(tmp_path / "miscounted.lance", texts, tokens=[4, 4])
    mix = ProvisionalMix(miscounted, word_tokenizer(), window=2, rows_per_update=1)
    with pytest.raises(ValueError, match="mix row 1 encodes to 3 tokens"):
        mix[1]

    digests = [hashlib.sha256(b"2 3 4").hexdigest(), hashlib.sha256(b"other").hexdigest()]
    hashed = write_mix(tmp_path / "hashed.lance", texts, text_sha256=digests)
    mix = ProvisionalMix(hashed, word_tokenizer(), window=2, rows_per_update=1)
    assert mix[0].tolist() == [[2, 3, 4]]
    with pytest.raises(ValueError, match="mix row 1 text does not match"):
        mix[2]

    # A leading zero count would otherwise be skipped by the offsets without being checked.
    for counts in [[0, 3], [5, -1]]:
        with pytest.raises(ValueError, match="at least 1"):
            ProvisionalMix(
                write_mix(tmp_path / f"counts{counts[1]}.lance", texts, tokens=counts),
                word_tokenizer(),
                window=2,
                rows_per_update=1,
            )


def test_micro_batches_accumulate_to_the_one_batch_gradient() -> None:
    windows = torch.tensor([[2, 3, 4, 1, 5], [6, 7, 8, 9, 2]])
    experiments = []
    for micro_batches in [[windows], list(windows.split(1))]:
        experiment = Experiment(
            tiny_config(),
            accelerator=Accelerator(cpu=True, mixed_precision="no"),
            source={},
            peak_lr=1e-3,
            betas=(0.9, 0.999),
            weight_decay=0.0,
            seed=3,
            total_targets=16,
            warmup_targets=1,
        )
        experiment._step([(rows[:, :-1], rows[:, 1:]) for rows in micro_batches])
        experiments.append(experiment)
    whole, split = experiments
    assert whole.targets == split.targets == 8
    for a, b in zip(whole.model.parameters(), split.model.parameters(), strict=True):
        torch.testing.assert_close(a.grad, b.grad, rtol=1e-5, atol=1e-7)


def test_gpu_runtime_resumes_to_the_same_next_update_on_cpu(
    tmp_path: Path, fresh_accelerator_state: None
) -> None:
    texts = ["2 3 4", "5", "6 7 8 9 2 3", "4 5 6"]
    mix = ProvisionalMix(
        write_mix(tmp_path / "mix.lance", texts), word_tokenizer(), window=3, rows_per_update=2
    )

    def build(seed: int) -> GpuExperiment:
        return GpuExperiment(
            sdpa_config(),
            mix,
            peak_lr=1e-3,
            seed=seed,
            total_targets=12,
            warmup_targets=3,
            micro_rows=1,
            device="cpu",
        )

    experiment = build(17)
    assert experiment.description["precision"] == "bf16"
    experiment.update(mix[0])
    experiment.checkpoint(tmp_path / "checkpoint")
    expected_loss = experiment.update(mix[1])
    expected = {k: v.clone() for k, v in experiment.model.state_dict().items()}

    with pytest.raises(ValueError, match="contract"):
        build(999).resume(tmp_path / "checkpoint")
    restored = build(17)
    restored.resume(tmp_path / "checkpoint")
    assert (restored.updates, restored.targets) == (1, 6)
    assert restored.update(mix[1]) == expected_loss
    for key, value in restored.model.state_dict().items():
        torch.testing.assert_close(value, expected[key], rtol=0, atol=0)
    with pytest.raises(ValueError, match="overshoot"):
        restored.update(mix[0])

    with pytest.raises(ValueError, match="whole updates"):
        GpuExperiment(
            sdpa_config(),
            mix,
            peak_lr=1e-3,
            seed=0,
            total_targets=7,
            warmup_targets=1,
            micro_rows=1,
            device="cpu",
        )
    with pytest.raises(ValueError, match="SDPA"):
        GpuExperiment(
            tiny_config(),
            mix,
            peak_lr=1e-3,
            seed=0,
            total_targets=6,
            warmup_targets=1,
            micro_rows=1,
            device="cpu",
        )


def test_cli_trains_resumes_and_marks_every_output_provisional(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fresh_accelerator_state: None
) -> None:
    # 30 documents of 9 tokens: 269 targets, 33 whole updates of two 4-target rows.
    config = write_mix(tmp_path / "mix.lance", ["2 3 4 5 6 7 8 9"] * 30)
    monkeypatch.setattr(train.AutoTokenizer, "from_pretrained", lambda *_, **__: word_tokenizer())
    monkeypatch.setattr(train, "proxy_config", lambda _: sdpa_config(context=4))
    monkeypatch.setattr(train, "GpuExperiment", partial(GpuExperiment, device="cpu"))
    output = tmp_path / "run"
    arguments = [
        "train",
        "--size=76m",
        f"--dataset-uri={config.uri}",
        "--dataset-version=1",
        "--warmup-targets=8",
        "--rows-per-update=2",
        "--micro-rows=1",
        f"--output-dir={output}",
        "--checkpoint-every=1",
        "--workers=0",
    ]
    monkeypatch.setattr("sys.argv", [*arguments, "--max-updates=2"])
    train.main()
    AcceleratorState._reset_state(True)
    monkeypatch.setattr("sys.argv", [*arguments, "--max-updates=3", "--resume"])
    train.main()

    lines = [json.loads(line) for line in (output / "log.jsonl").read_text().splitlines()]
    assert [(line["segment"], line["update"], line["targets"]) for line in lines] == [
        (0, 1, 8),
        (0, 2, 16),
        (2, 3, 24),
    ]
    # Rates by update-start targets: 0 before warmup's 8 targets, the peak at 8, then cosine.
    peak = 1.31e-3  # the 76M preset's
    progress = (16 - 8) / (33 * 8 - 8)
    assert [line["lr"] for line in lines] == pytest.approx(
        [0.0, peak, peak * (0.1 + 0.9 * (1 + math.cos(math.pi * progress)) / 2)]
    )
    first = json.loads((output / "run-000.json").read_text())
    resumed = json.loads((output / "run-001.json").read_text())
    assert (first["attempt"], resumed["attempt"]) == (0, 1)
    assert first["contract"] == resumed["contract"]
    assert first["provisional"] is resumed["provisional"] is True
    assert first["total"] == 33 * 8  # one pass over the mix's whole updates
    assert resumed["resumed_at_update"] == 2
    assert sorted(path.name for path in (output / "checkpoints").iterdir()) == [
        "update-000001",
        "update-000002",
        "update-000003",
    ]
    card = json.loads((output / "export-000003" / "experiment.json").read_text())
    assert card["provisional"] is card["source"]["provisional"] is True
    assert card["targets"] == 24
    summary = json.loads((output / "summary-000003.json").read_text())
    assert summary["provisional"] is True
    assert (summary["segment"], summary["segment_targets"]) == (2, 8)

    assert (summary["device"], resumed["device"]) == ("cpu", "cpu")

    # Resuming at the stop point finds the checkpoint, export and summary written and keeps them.
    AcceleratorState._reset_state(True)
    train.main()
    assert json.loads((output / "summary-000003.json").read_text()) == summary

    # A save or export killed partway leaves its lock, and a resume refuses to train towards it.
    for interrupted in [
        output / "checkpoints" / "update-000004.incomplete",
        output / "x.incomplete",
    ]:
        interrupted.mkdir()
        AcceleratorState._reset_state(True)
        monkeypatch.setattr("sys.argv", [*arguments, "--max-updates=5", "--resume"])
        with pytest.raises(FileExistsError, match="interrupted save"):
            train.main()
        interrupted.rmdir()

    # An explicit zero budget is an error, not a full pass.
    AcceleratorState._reset_state(True)
    monkeypatch.setattr("sys.argv", [*arguments, "--total-targets=0", "--max-updates=1"])
    with pytest.raises(ValueError, match="endpoint"):
        train.main()

    # Accelerate's own accumulation would divide the loss a second time.
    AcceleratorState._reset_state(True)
    monkeypatch.setattr("sys.argv", [*arguments, "--max-updates=5", "--resume"])
    monkeypatch.setenv("ACCELERATE_GRADIENT_ACCUMULATION_STEPS", "2")
    with pytest.raises(ValueError, match="accumulation"):
        train.main()
    monkeypatch.delenv("ACCELERATE_GRADIENT_ACCUMULATION_STEPS")

    # The launcher itself requires the device Accelerate selected to be a GPU.
    monkeypatch.setattr(train, "GpuExperiment", GpuExperiment)
    AcceleratorState._reset_state(True)
    with pytest.raises(RuntimeError, match="not cuda"):
        train.main()
