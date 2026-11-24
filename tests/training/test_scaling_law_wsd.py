"""WSD scaling-law runs: token schedules, save lists, the one-pass guard, metrics and save evals."""

from __future__ import annotations

import itertools
import json
import math
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import lance
import pyarrow as pa
import pytest
import torch

from flock.eval.dataset import Dataset
from flock.eval.run import prepare_task, score_prepared
from flock.eval.tasks.perplexity import PerplexityTask, TextDoc
from flock.eval.tasks.suite import Suite
from flock.training.core.attention import AttentionInterface
from flock.training.core.distributed import DistributedRuntime
from flock.training.core.lr_scheduler import lr_multiplier
from flock.training.core.models import Llama, LlamaConfig
from flock.training.experiments.scaling_law.config import ExperimentConfig
from flock.training.experiments.scaling_law.evals import (
    EvalOutcome,
    SaveEvaluator,
    fp32_sdpa_scoring,
    shard_documents,
)
from flock.training.experiments.scaling_law.train import build_trainer
from flock.training.experiments.scaling_law.trainer import steps_for_tokens
from flock.training.experiments.scaling_law.wsd import (
    WSDPlan,
    WSDSettings,
    budget_tokens,
    describe,
    tokens_to_step,
)

if TYPE_CHECKING:
    from transformers import TokenizersBackend

    from flock.training.core.models.olmo2 import Olmo2Config

ONE_PASS = 191_900_000_000
TARGETS = [10, 15, 20, 40, 80, 120, 200, 400, 800, 1200, 1600, 2000]
SIZES = {"100M": (100_000_000, 8 * 32768), "200M": (200_000_000, 8 * 49152)}


def plan(size: str) -> WSDPlan:
    params, per_update = SIZES[size]
    return WSDPlan(
        nominal_params=params,
        stop_tokens=budget_tokens(ONE_PASS, per_update),
        targets=TARGETS,
        recovery_tokens=ONE_PASS // 20,
        per_update=per_update,
    )


# ------------------------------------------------------------------ schedule


def test_tokens_round_to_the_first_reaching_or_nearest_update() -> None:
    assert [tokens_to_step(t, 10, "first_reaching") for t in (0, 1, 10, 11, 15)] == [1, 1, 1, 2, 2]
    assert [tokens_to_step(t, 10, "nearest") for t in (0, 4, 5, 14, 15, 16)] == [1, 1, 1, 1, 2, 2]
    # The trainer's general mapping agrees on a stream's constant updates.
    counts = [10] * 10
    targets = [1, 4, 5, 14, 15, 16, 95]
    import numpy as np

    assert steps_for_tokens(np.array(counts), targets, "nearest") == [
        tokens_to_step(t, 10, "nearest") for t in targets
    ]


def test_wsd_warms_up_in_tokens_and_cosine_decays_to_one_percent() -> None:
    per_update = 8 * 32768
    stop = 1000 * per_update
    schedule = WSDSettings(warmup_tokens=100_000_000).schedule(stop, per_update)
    warmup, stable, decay = schedule.schedule
    assert (warmup.start, warmup.end) == (0, round(100_000_000 / per_update)) == (0, 381)
    assert (stable.end, decay.start, decay.end) == (900, 900, 1000)
    assert decay.interpolation == "cosine" and decay.end_ratio == 0.01
    ratio = [lr_multiplier(step, schedule, 1000) for step in range(1001)]
    assert ratio[0] == 0 and ratio[381] == 1 and ratio[900] == 1
    assert ratio[950] == pytest.approx(0.505)  # cosine midpoint between 1 and 0.01
    assert ratio[1000] == pytest.approx(0.01)
    # The last update applies the rate after 999 completed updates, one cosine point above 1%.
    assert ratio[999] == pytest.approx(0.01 + 0.99 * (1 - math.cos(math.pi / 100)) / 2)
    assert all(a >= b for a, b in itertools.pairwise(ratio[900:]))


def test_wsd_rejects_a_warmup_past_the_decay() -> None:
    with pytest.raises(ValueError, match="warmup"):
        WSDSettings(warmup_tokens=10**9).schedule(10**9, 2**18)


# ------------------------------------------------------------------ save lists


def test_save_lists_for_both_sizes(capsys: pytest.CaptureFixture[str]) -> None:
    for size, (params, per_update) in SIZES.items():
        points = plan(size).saves()
        with capsys.disabled():
            print(f"\n{size} saves (per update {per_update:,})\n{describe(points, params)}")
        assert [p.step for p in points] == sorted({p.step for p in points})
        assert all(p.trained_tokens == p.step * per_update for p in points)
        assert all(abs(p.trained_tokens - p.tokens) <= per_update / 2 for p in points)
        assert points[-1].reasons == ("stop",)
        assert points[-1].trained_tokens <= ONE_PASS
        assert sum("recovery" in r for p in points for r in p.reasons) == 19
    hundred, two_hundred = plan("100M"), plan("200M")
    assert hundred.stop_tokens == 191_899_893_760 == 732_040 * 262_144
    assert two_hundred.stop_tokens == 191_899_631_616 == 488_026 * 393_216
    assert hundred.dropped_targets() == [2000]
    assert two_hundred.dropped_targets() == [1200, 1600, 2000]
    assert [b[0] for b in hundred.branches()] == [
        10,
        15,
        20,
        40,
        80,
        120,
        200,
        400,
        800,
        1200,
        1600,
    ]
    assert (hundred.branches()[0], two_hundred.branches()[-1]) == (
        (10, 3433, 1_000_000_000),
        (800, 366_211, 160_000_000_000),
    )


@pytest.mark.parametrize("size", SIZES)
def test_each_branch_decays_from_its_parent_save_to_its_target(size: str) -> None:
    run = plan(size)
    params, per_update = SIZES[size]
    saved = {p.step for p in run.saves()}
    main_decay = WSDSettings(warmup_tokens=params).schedule(run.stop_tokens, per_update)
    for target, parent_step, stop in run.branches():
        assert parent_step in saved and run.step(stop) in saved
        # The parent is still at peak there, so the branch inherits the main run's rates.
        assert parent_step <= main_decay.schedule[2].start
        decay = WSDSettings(warmup_tokens=params).schedule(stop, per_update).schedule[2]
        assert (decay.start, decay.end) == (parent_step, run.step(stop))
        assert decay.end * per_update == pytest.approx(target * params, abs=per_update / 2)


# ------------------------------------------------------------------ trainer


@dataclass
class RecordingRun:
    entity: str = "flock"
    project: str = "wsd"
    id: str = "run123"
    metrics: list = field(default_factory=list)
    config: dict = field(default_factory=dict)

    def define_metric(self, *args: Any, **kwargs: Any) -> None:
        pass

    def log(self, metrics: dict[str, Any]) -> None:
        self.metrics.append(dict(metrics))

    def finish(self) -> None:
        pass


class _Config(dict):
    def update(self, values: dict[str, Any], allow_val_change: bool = False) -> None:
        super().update(values)


class FakeEvaluator:
    """Stands in for `SaveEvaluator`: records calls, optionally failing."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    def evaluate(self, model: Any, tokenizer: Any, *, name: str) -> EvalOutcome:
        self.calls.append(name)
        if self.fail:
            raise RuntimeError("dataset missing")
        return EvalOutcome(
            {"eval/paloma/c4_en": 1.25, "eval/failed": 0.0}, {"flock_commit": "abc123"}
        )


def wsd_experiment(
    tmp_path: Path, config: Olmo2Config, tokenizer: TokenizersBackend, **overrides: Any
) -> ExperimentConfig:
    tokenizer.save_pretrained(tmp_path / "tokenizer")
    path = tmp_path / "text.lance"
    if not path.exists():
        lance.write_dataset(pa.table({"text": ["a b c d a b c d " * 4] * 40}), path)
    model = {**asdict(config), "hf_repo": str(tmp_path / "tokenizer")}
    model.pop("attn_implementation")
    fields: dict[str, Any] = {
        "name": "wsd",
        "output_dir": str(tmp_path / "checkpoints"),
        "data": {"text_path": str(path), "block_rows": 4, "chunk_length": 5},
        "model": {"attn_implementation": "sdpa", "overrides": model},
        "optimizer": {"type": "muon_adamw", "lr": 0.01, "muon_lr": 0.02},
        "wsd": {"warmup_tokens": 12, "decay_fraction": 0.2, "final_ratio": 0.01},
        "trainer": {
            "batch_tokens": 6,
            "dataloader_num_workers": 0,
            "dataloader_prefetch_factor": None,
            "logging_steps": 1,
            "wandb_mode": "disabled",
        },
        "stop_tokens": 60,
        "save_tokens": [47, 31],
        "token_rounding": "nearest",
        "one_pass_tokens": 600,
        "nominal_params": 3,
    }
    fields.update(overrides)
    return ExperimentConfig.model_validate(fields)


@pytest.fixture
def recording(monkeypatch: pytest.MonkeyPatch) -> RecordingRun:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    run = RecordingRun(config=_Config())
    monkeypatch.setattr("flock.training.core.trainer.wandb.init", lambda **kwargs: run)
    return run


def test_wsd_run_logs_epoch_share_and_evaluates_before_each_save(
    config: Olmo2Config,
    tokenizer: TokenizersBackend,
    recording: RecordingRun,
    tmp_path: Path,
) -> None:
    experiment = wsd_experiment(tmp_path, config, tokenizer)
    trainer = build_trainer(experiment)
    evaluator = FakeEvaluator()
    trainer.evaluator = evaluator  # ty: ignore[invalid-assignment]
    trainer.train()

    # 31 -> update 5 (30 tokens), 47 -> 8 (48), 60 -> 10: nearest updates, exact tokens recorded.
    records = json.loads((trainer.output_dir / "token_checkpoints.json").read_text())
    assert [(r["tokens"], r["step"], r["trained_tokens"]) for r in records] == [
        (31, 5, 30),
        (47, 8, 48),
        (60, 10, 60),
    ]
    saved = sorted(int(p.name.split("-")[1]) for p in trainer.output_dir.glob("checkpoint-*"))
    assert saved == [5, 8, 10]
    assert evaluator.calls == ["wsd@step-5", "wsd@step-8", "wsd@step-10"]

    train = [m for m in recording.metrics if "train/loss_mean" in m]
    for metrics in train:
        assert metrics["train/epoch_pct"] == pytest.approx(
            100 * metrics["train/total_tokens"] / 600
        )
        assert metrics["train/tokens_per_param"] == pytest.approx(metrics["train/total_tokens"] / 3)
    evals = [m for m in recording.metrics if "eval/paloma/c4_en" in m]
    assert [m["global_step"] for m in evals] == [5, 8, 10]
    assert [m["eval/total_tokens"] for m in evals] == [30, 48, 60]
    assert recording.config["eval_flock_commit"] == "abc123"
    lines = (trainer.output_dir / "evals.jsonl").read_text().splitlines()
    assert [json.loads(line)["step"] for line in lines] == [5, 8, 10]

    # Warmup to update 2 (12 tokens), decay from update 8 (48 tokens) to 1% at update 10.
    lr = {m["global_step"]: m["train/lr_muon"] for m in train}
    assert lr[1] == pytest.approx(0.0) and lr[3] == pytest.approx(0.02)
    # Update 9 applies the rate after 8 completed updates, the decay's start; update 10 decays.
    assert lr[9] == pytest.approx(0.02) and lr[10] == pytest.approx(0.02 * 0.505)
    assert trainer.lr_scheduler.optimizer.param_groups[0]["lr"] == pytest.approx(0.02 * 0.01)


def test_a_failed_evaluation_still_saves(
    config: Olmo2Config,
    tokenizer: TokenizersBackend,
    recording: RecordingRun,
    tmp_path: Path,
) -> None:
    trainer = build_trainer(wsd_experiment(tmp_path, config, tokenizer))
    trainer.evaluator = FakeEvaluator(fail=True)  # ty: ignore[invalid-assignment]
    trainer.train()
    saved = sorted(int(p.name.split("-")[1]) for p in trainer.output_dir.glob("checkpoint-*"))
    assert saved == [5, 8, 10]
    assert not any(key.startswith("eval/") for m in recording.metrics for key in m)


def test_a_decay_branch_ends_at_its_target_at_one_percent(
    config: Olmo2Config,
    tokenizer: TokenizersBackend,
    recording: RecordingRun,
    tmp_path: Path,
) -> None:
    parent = build_trainer(wsd_experiment(tmp_path, config, tokenizer))
    parent.train()
    # The parent saved at update 5 (30 tokens, still at peak). A branch to T = 36 tokens
    # decays over its last sixth, from that save, and ends at T at 1% of the peak.
    branch = wsd_experiment(
        tmp_path,
        config,
        tokenizer,
        name="branch",
        output_dir=str(tmp_path / "branch"),
        stop_tokens=36,
        save_tokens=[],
        wsd={"warmup_tokens": 12, "decay_fraction": 1 / 6, "final_ratio": 0.01},
        branch_from=str(parent.output_dir / "checkpoint-5"),
    )
    trainer = build_trainer(branch)
    assert trainer.lr_scheduler.config.schedule[2].start == 5
    trainer.train(branch_from=branch.branch_from)
    assert int(trainer.state.step) == 6
    assert int(trainer.state.total_tokens) == 36
    assert trainer.lr_scheduler.optimizer.param_groups[0]["lr"] == pytest.approx(0.02 * 0.01)
    logged = [m for m in recording.metrics if "train/loss_mean" in m][-1]
    assert logged["global_step"] == 6 and logged["train/total_tokens"] == 36


def test_budgets_beyond_one_pass_fail_at_startup(
    config: Olmo2Config,
    tokenizer: TokenizersBackend,
    recording: RecordingRun,
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="exceeds one pass"):
        wsd_experiment(tmp_path, config, tokenizer, one_pass_tokens=59)
    # Within the config's target, but nearest rounding would land one update past one pass.
    experiment = wsd_experiment(tmp_path, config, tokenizer, stop_tokens=58, one_pass_tokens=58)
    with pytest.raises(ValueError, match="more than one pass"):
        build_trainer(experiment)


def test_config_needs_exactly_one_schedule(
    config: Olmo2Config, tokenizer: TokenizersBackend, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="exactly one of schedule and wsd"):
        wsd_experiment(
            tmp_path,
            config,
            tokenizer,
            schedule=[{"start": 0, "end": 1.0, "start_ratio": 1.0, "end_ratio": 1.0}],
        )


# ------------------------------------------------------------------ save evals


def tiny_llama(attention: str = "sdpa") -> Llama:
    return Llama(
        LlamaConfig(
            vocab_size=32,
            hidden_size=32,
            intermediate_size=64,
            num_hidden_layers=2,
            num_attention_heads=4,
            num_key_value_heads=2,
            max_position_embeddings=16,
            attn_implementation=attention,
        )
    )


@dataclass
class Rank:
    rank: int = 0
    world_size: int = 1
    enabled: bool = False
    device: torch.device = field(default_factory=lambda: torch.device("cpu"))


DOCS = {
    "fake_one": ["a b c d a b c d a b c d a b c d a b", "b c", "c d a b c d a"],
    "fake_two": ["d d d a", "a b a b a b a b a b a b a b a b a b a b c", "c"],
}


@pytest.fixture
def fake_suite(monkeypatch: pytest.MonkeyPatch) -> Suite:
    dataset = Dataset(repo="fake/text", revision="0" * 40, subset=None, split="test")
    tasks = tuple(PerplexityTask(name, dataset, window=8) for name in DOCS)

    def load(task: PerplexityTask, limit: int | None = None) -> list[TextDoc]:
        texts = DOCS[task.name][:limit]
        return [TextDoc(f"{task.name}-{index}", text) for index, text in enumerate(texts)]

    monkeypatch.setattr(PerplexityTask, "load", load)
    return Suite("fake", tasks, metric="bits_per_byte")


def evaluator_for(suite: Suite, runtime: Rank) -> SaveEvaluator:
    evaluator = SaveEvaluator({"tw_text": "fake"}, runtime, batch_tokens=40)  # ty: ignore[invalid-argument-type]
    evaluator.suites = {"fake": suite}
    evaluator.tasks = [("fake", task) for task in suite.tasks]
    return evaluator


def test_save_evals_match_flock_scoring(
    fake_suite: Suite, tokenizer: TokenizersBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    model = tiny_llama()
    outcome = evaluator_for(fake_suite, Rank()).evaluate(model, tokenizer, name="tiny")
    assert outcome.error is None

    # `flock eval run`'s own path: one process, `score_prepared` on each prepared task.
    from flock.eval.model import FlockCausalLM, ScoringModel

    with torch.no_grad():
        scorer = ScoringModel(FlockCausalLM(model), tokenizer, name="tiny", batch_tokens=40)
        expected = {
            task.name: score_prepared(scorer, prepare_task(scorer, task)).metrics
            for task in fake_suite.tasks
        }
    for name, metrics in expected.items():
        logged = outcome.metrics[f"eval/fake/tasks/{name}"]
        assert logged == pytest.approx(metrics["bits_per_byte"], rel=1e-6)
    assert outcome.metrics["eval/fake/score"] == pytest.approx(fake_suite.score(expected), rel=1e-6)
    record = outcome.record["tasks"]["fake_one"]
    assert record["num_docs"] == 3 and record["num_bytes"] == sum(
        len(text.encode()) for text in DOCS["fake_one"]
    )
    assert outcome.record["flock_commit"]


def test_rank_shards_add_up_to_the_whole(
    fake_suite: Suite, tokenizer: TokenizersBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    model = tiny_llama()
    from flock.eval.model import FlockCausalLM, ScoringModel

    def sums(runtime: Rank) -> torch.Tensor:
        evaluator = evaluator_for(fake_suite, runtime)
        scorer = ScoringModel(FlockCausalLM(model), tokenizer, name="tiny", batch_tokens=40)
        with fp32_sdpa_scoring(model):
            evaluator._prepare(scorer)
            return evaluator._score_shard(scorer)

    whole = sums(Rank())
    split = sums(Rank(0, 2)) + sums(Rank(1, 2))
    torch.testing.assert_close(split, whole, rtol=1e-6, atol=1e-6)


def test_shards_cover_every_document_once_and_balance_tokens() -> None:
    sizes = [[100, 3, 3, 3], [50, 50, 1], [7] * 10]
    shards = shard_documents(sizes, 3)
    pairs = sorted(pair for shard in shards for pair in shard)
    assert pairs == sorted((t, d) for t, docs in enumerate(sizes) for d in range(len(docs)))
    loads = [sum(sizes[t][d] for t, d in shard) for shard in shards]
    assert max(loads) == 100 and min(loads) >= 55
    assert shard_documents(sizes, 3) == shards  # every rank computes the same split


def test_scoring_runs_fp32_sdpa_and_restores_flash_attention() -> None:
    model = tiny_llama("flash_attention_4")
    flash = AttentionInterface.get_interface("flash_attention_4")
    attention = model.model.layers[0].self_attn
    assert attention.attention_fn is flash
    precision = torch.get_float32_matmul_precision()
    torch.set_float32_matmul_precision("high")
    try:
        with fp32_sdpa_scoring(model):
            assert attention.attention_fn is AttentionInterface.get_interface("sdpa")
            assert model.config.attn_implementation == "sdpa"
            assert torch.get_float32_matmul_precision() == "highest"
            assert not torch.is_autocast_enabled("cpu")
        assert attention.attention_fn is flash
        assert model.config.attn_implementation == "flash_attention_4"
        assert torch.get_float32_matmul_precision() == "high"
    finally:
        torch.set_float32_matmul_precision(precision)


def test_a_failing_rank_returns_an_error_instead_of_raising(
    fake_suite: Suite, tokenizer: TokenizersBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    evaluator = evaluator_for(fake_suite, Rank())

    def broken(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError("no staged copy")

    monkeypatch.setattr("flock.training.experiments.scaling_law.evals.prepare_task", broken)
    outcome = evaluator.evaluate(tiny_llama(), tokenizer, name="tiny")
    assert outcome.error is not None and "no staged copy" in outcome.error
    assert outcome.metrics["eval/failed"] == 1.0
    assert replace(outcome, metrics={}).record["error"] == outcome.error


def test_default_suites_are_paloma_and_tw_text_v0() -> None:
    evaluator = SaveEvaluator(
        {"paloma": "paloma", "tw_text": "tw_text_v0"},
        DistributedRuntime.from_env(),
    )
    names = {task.name for _, task in evaluator.tasks}
    assert len([n for n in names if n.startswith("paloma_")]) == 309
    assert len([n for n in names if n.startswith("tw_text_")]) == 24
    assert all(task.dataset.split == "test" for _, task in evaluator.tasks)
