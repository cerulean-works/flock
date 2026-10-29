import dataclasses
import hashlib
import json
import math
import sys
from types import SimpleNamespace

import datasets
import pytest
import tokenizers
import torch
from click.testing import CliRunner

from flock.eval import cli, run
from flock.eval.metrics import MULTIPLE_CHOICE_METRICS, ChoiceScores
from flock.eval.model import ScoringModel
from flock.eval.provenance import Provenance
from flock.eval.results import Results, prediction_rows, write_predictions
from flock.eval.run import EvalConfig, evaluate, score_task
from flock.eval.tasks import MCDoc, Suite, cloze

from .toy import TASK


@pytest.fixture
def offline_model(monkeypatch, scorer):
    monkeypatch.setitem(run.TASKS, TASK.name, TASK)
    monkeypatch.setattr(ScoringModel, "from_pretrained", lambda *args, **kwargs: scorer)
    monkeypatch.setenv("FLOCK_COMMIT", "abc123")


def test_evaluate_writes_results_and_predictions(offline_model, tmp_path):
    results = evaluate(EvalConfig(model="tiny@0", tasks=("toy",), out=tmp_path))

    data = json.loads((tmp_path / "results.json").read_text())
    assert data["schema_version"] == 5
    assert data["provenance"]["model"] == "tiny@0"
    assert data["provenance"]["flock_commit"] == "abc123"
    assert data["provenance"]["command"] is None  # the Python API names no command
    assert data["provenance"]["dtype"] == "float32"
    assert data["provenance"]["max_length"] == 128
    assert data["provenance"]["attention"] == "sdpa"
    assert data["provenance"]["tokenizers"] == tokenizers.__version__
    assert data["provenance"]["datasets"] == datasets.__version__
    [task] = data["tasks"]
    assert task["task"] == "toy"
    assert task["num_docs"] == 2
    assert task["task_hash"] == TASK.requests(*TASK.load()).hash
    assert task["dataset"] == dataclasses.asdict(TASK.dataset)
    assert set(task["metrics"]) == set(MULTIPLE_CHOICE_METRICS)
    assert all(0 <= task["metrics"][m] <= 1 for m in task["metrics"] if m.startswith("acc"))
    assert task["metrics"]["bpb"] > 0
    assert task["docs_per_second"] > 0
    assert task["truncation"] == {"requests": 0, "docs": 0, "max_tokens_cut": 0}
    assert results.tasks[0].metrics == task["metrics"]
    assert data["suites"] == []

    lines = (tmp_path / "predictions" / "toy.jsonl").read_text().splitlines()
    first, second = map(json.loads, lines)
    assert (first["id"], first["gold"], len(first["logprob"])) == ("q-1", 0, 3)
    assert len(second["logprob_uncond"]) == 4

    with pytest.raises(FileExistsError):  # a second run can't replace this one
        results.write(tmp_path)
    with pytest.raises(TypeError):  # a file can't claim another layout
        Results(results.provenance, [], schema_version=3)  # ty: ignore[unknown-argument]


def test_predictions_recompute_every_metric_and_the_truncation(
    offline_model, monkeypatch, lm, tokenizer, tmp_path
):
    # A short `max_length` cuts the few-shot contexts, so the truncation counts aren't all 0.
    short = ScoringModel(lm, tokenizer, name="tiny@0", max_length=16)
    monkeypatch.setattr(ScoringModel, "from_pretrained", lambda *args, **kwargs: short)
    [task] = evaluate(EvalConfig(model="tiny@0", tasks=("toy",), out=tmp_path)).tasks
    lines = (tmp_path / "predictions" / "toy.jsonl").read_text().splitlines()
    rows = [json.loads(line) for line in lines]

    def accuracy(score) -> float:
        hits = 0
        for row in rows:
            scores = [score(row, i) for i in range(len(row["logprob"]))]
            hits += scores.index(max(scores)) == row["gold"]  # ties go to the first choice
        return hits / len(rows)

    def bpb(row, i) -> float:
        return -row["logprob"][i] / row["num_bytes"][i] / math.log(2)

    recomputed = {
        "acc_raw": accuracy(lambda row, i: row["logprob"][i]),
        "acc_per_token": accuracy(lambda row, i: row["logprob"][i] / row["num_tokens"][i]),
        "acc_per_char": accuracy(lambda row, i: row["logprob"][i] / row["num_chars"][i]),
        "acc_per_byte": accuracy(lambda row, i: -bpb(row, i)),
        "acc_uncond": accuracy(lambda row, i: row["logprob"][i] - row["logprob_uncond"][i]),
        "bpb": sum(bpb(row, row["gold"]) for row in rows) / len(rows),
    }
    assert recomputed == pytest.approx(task.metrics, abs=1e-6)

    cuts = [row["tokens_cut"] + row["tokens_cut_uncond"] for row in rows]
    assert task.truncation.requests == sum(sum(n > 0 for n in cut) for cut in cuts) > 0
    assert task.truncation.docs == sum(any(cut) for cut in cuts)
    assert task.truncation.max_tokens_cut == max(max(cut) for cut in cuts)


def test_provenance_is_collected_before_scoring(offline_model, scorer, monkeypatch):
    events = []
    collect = Provenance.collect
    loglikelihood = scorer.loglikelihood
    monkeypatch.setattr(
        Provenance, "collect", lambda **kwargs: events.append("provenance") or collect(**kwargs)
    )
    monkeypatch.setattr(
        scorer, "loglikelihood", lambda requests: events.append("score") or loglikelihood(requests)
    )

    evaluate(EvalConfig(model="tiny@0", tasks=("toy",)))

    assert events == ["provenance", "score"]


OTHER = dataclasses.replace(TASK, name="other", primary_metric="acc_raw")
RC = Suite("toy:rc", (TASK, OTHER))
BPB = Suite("toy:bpb", (TASK, OTHER), metric="bpb")
GROUP = Suite("toys", (RC, BPB), scored=False)


@pytest.fixture
def toy_suites(offline_model, monkeypatch):
    for suite in GROUP.suites:
        monkeypatch.setitem(run.SUITES, suite.name, suite)


def test_evaluate_scores_suites_and_each_task_once(toy_suites, tmp_path):
    results = evaluate(EvalConfig(model="tiny@0", tasks=("toy",), suites=("toys",), out=tmp_path))

    assert [task.task for task in results.tasks] == ["toy", "other"]
    toy, other = (task.metrics for task in results.tasks)
    data = json.loads((tmp_path / "results.json").read_text())
    hashes = {suite["suite"]: suite.pop("suite_hash") for suite in data["suites"]}
    assert data["suites"] == [
        {
            "suite": "toy:rc",
            "members": ["toy", "other"],
            "metric": None,
            "score": pytest.approx((toy["acc_per_char"] + other["acc_raw"]) / 2),
        },
        {
            "suite": "toy:bpb",
            "members": ["toy", "other"],
            "metric": "bpb",
            "score": pytest.approx((toy["bpb"] + other["bpb"]) / 2),
        },
        {"suite": "toys", "members": ["toy:rc", "toy:bpb"], "metric": None, "score": None},
    ]

    # The hash results.md defines, written out once.
    toy_hash, other_hash = (task.task_hash for task in results.tasks)
    rc = {
        "suite": "toy:rc",
        "metric": None,
        "scored": True,
        "members": [["toy", toy_hash], ["other", other_hash]],
    }
    assert hashes["toy:rc"] == hashlib.sha256(json.dumps(rc, sort_keys=True).encode()).hexdigest()


def test_a_suite_hash_moves_with_any_hash_below_it(toy_suites, monkeypatch):
    def suite_hashes(**kwargs) -> dict[str, str]:
        results = evaluate(EvalConfig(model="tiny@0", suites=("toys",), **kwargs))
        return {suite.suite: suite.suite_hash for suite in results.suites}

    before = suite_hashes()
    assert suite_hashes() == before
    assert before["toy:rc"] != before["toy:bpb"]  # same members, another metric

    # A limit changes both task hashes, so every suite above them moves.
    limited = suite_hashes(limit=1)
    assert all(limited[name] != before[name] for name in before)

    # One member suite changes: it and the group move, its sibling doesn't.
    bpb = Suite("toy:bpb", (TASK,), metric="bpb")
    monkeypatch.setitem(run.SUITES, "toys", Suite("toys", (RC, bpb), scored=False))
    after = suite_hashes()
    assert after["toy:rc"] == before["toy:rc"]
    assert after["toy:bpb"] != before["toy:bpb"]
    assert after["toys"] != before["toys"]

    # Only the metric differs: the hash still moves.
    rc_raw = Suite("toy:rc", (TASK, OTHER), metric="acc_raw")
    monkeypatch.setitem(run.SUITES, "toys", Suite("toys", (rc_raw, BPB), scored=False))
    assert suite_hashes()["toy:rc"] != before["toy:rc"]


def test_evaluate_rejects_two_tasks_under_one_name(toy_suites, monkeypatch):
    monkeypatch.setitem(run.TASKS, "other", dataclasses.replace(OTHER, description="x"))
    with pytest.raises(ValueError, match="two different definitions are named other"):
        evaluate(EvalConfig(model="tiny@0", tasks=("other",), suites=("toy:rc",)))


def test_evaluate_rejects_a_suite_named_like_a_task(toy_suites, monkeypatch):
    monkeypatch.setitem(run.SUITES, "toys", Suite("toys", (Suite("toy", (TASK,)), RC)))
    with pytest.raises(ValueError, match=r"share the names \['toy'\]"):
        evaluate(EvalConfig(model="tiny@0", suites=("toys",)))


def test_evaluate_rejects_a_task_named_suites(monkeypatch):
    monkeypatch.setitem(run.TASKS, "suites", dataclasses.replace(TASK, name="suites"))
    with pytest.raises(ValueError, match="named suites"):
        evaluate(EvalConfig(model="tiny@0", tasks=("suites",)))


def test_evaluate_rejects_two_suites_under_one_name(toy_suites, monkeypatch):
    clash = Suite("toy:rc", (TASK,))
    monkeypatch.setitem(run.SUITES, "other", Suite("other", (clash,)))
    with pytest.raises(ValueError, match="two different definitions are named toy:rc"):
        evaluate(EvalConfig(model="tiny@0", suites=("toys", "other")))


def test_prediction_rows_drop_padded_choices(tmp_path):
    docs = [
        MCDoc(id="a", choices=cloze("?", ("x", "y")), gold=1),
        MCDoc(id="b", choices=cloze("?", ("x", "y", "z")), gold=0),
    ]
    counts = torch.tensor([[1, 2, 0], [3, 4, 5]])
    scores = ChoiceScores(
        logprob=-counts.float(),
        num_tokens=counts,
        num_chars=counts + 10,
        num_bytes=counts + 20,
        valid=counts > 0,
        gold=torch.tensor([1, 0]),
        tokens_cut=counts + 30,
    )

    rows = prediction_rows(docs, scores)

    assert rows == [
        {
            "id": "a",
            "gold": 1,
            "logprob": [-1.0, -2.0],
            "num_tokens": [1, 2],
            "num_chars": [11, 12],
            "num_bytes": [21, 22],
            "tokens_cut": [31, 32],
        },
        {
            "id": "b",
            "gold": 0,
            "logprob": [-3.0, -4.0, -5.0],
            "num_tokens": [3, 4, 5],
            "num_chars": [13, 14, 15],
            "num_bytes": [23, 24, 25],
            "tokens_cut": [33, 34, 35],
        },
    ]

    with pytest.raises(ValueError, match="1 docs but scores for 2"):
        prediction_rows(docs[:1], scores)

    # A file another run wrote while this one scored is refused at write time, not replaced.
    path = tmp_path / "toy.jsonl"
    path.write_text("")
    with pytest.raises(FileExistsError):
        write_predictions(path, docs, scores)


def test_score_task_refuses_an_existing_predictions_file(scorer, tmp_path, monkeypatch):
    score_task(scorer, TASK, out=tmp_path)
    monkeypatch.setattr(scorer, "loglikelihood", None)  # scoring again would fail on None

    with pytest.raises(FileExistsError, match="Pass a new out directory"):
        score_task(scorer, TASK, out=tmp_path)


def test_a_task_timed_at_zero_seconds_has_no_throughput(offline_model, tmp_path, monkeypatch):
    # Only `run`'s clock stops: other libraries keep the real `time`.
    monkeypatch.setattr(run, "time", SimpleNamespace(perf_counter=lambda: 0.0))

    results = evaluate(EvalConfig(model="tiny@0", tasks=("toy",), out=tmp_path))

    [task] = json.loads((tmp_path / "results.json").read_text())["tasks"]
    assert task["docs_per_second"] is None
    assert "docs/s" not in cli.format_results(results)


def test_score_task_does_not_depend_on_batching(scorer):
    one_batch = score_task(scorer, TASK)
    scorer.batch_tokens = 1
    assert score_task(scorer, TASK).metrics == pytest.approx(one_batch.metrics, abs=1e-5)


def test_score_task_reports_only_the_tasks_metrics(scorer):
    gold_only = dataclasses.replace(TASK, metrics=("bpb",), primary_metric="bpb")
    assert list(score_task(scorer, gold_only).metrics) == ["bpb"]
    assert list(score_task(scorer, TASK).metrics) == list(MULTIPLE_CHOICE_METRICS)


def test_score_task_records_truncation(lm, tokenizer):
    scorer = ScoringModel(lm, tokenizer, name="tiny@0", max_length=16)
    built = TASK.requests(*TASK.load())
    conditional = sum(len(doc.choices) for doc in built.docs)
    # A window holds `max_length + 1` tokens: the model's input, then the last target.
    window = scorer.max_length + 1
    cuts = [max(0, sum(map(len, scorer.encode(r))) - window) for r in built.requests]
    # The few-shot contexts are cut, the short unconditional ones that follow them aren't.
    assert all(cuts[:conditional]) and not any(cuts[conditional:])

    truncation = score_task(scorer, TASK).truncation

    assert (truncation.requests, truncation.docs) == (conditional, len(built.docs))
    assert truncation.max_tokens_cut == max(cuts)


def test_evaluate_rejects_unknown_task_or_suite():
    with pytest.raises(KeyError, match="unknown tasks"):
        evaluate(EvalConfig(model="tiny@0", tasks=("nope",)))
    with pytest.raises(KeyError, match="unknown suites"):
        evaluate(EvalConfig(model="tiny@0", suites=("nope",)))


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        pytest.param({}, "at least one task or suite", id="nothing"),
        pytest.param({"tasks": ("toy", "toy")}, "tasks repeat", id="tasks"),
        pytest.param({"suites": ("toy", "toy")}, "suites repeat", id="suites"),
    ],
)
def test_eval_config_rejects_empty_or_repeated_names(kwargs, match):
    with pytest.raises(ValueError, match=match):
        EvalConfig(model="tiny@0", **kwargs)


def test_evaluate_refuses_a_non_empty_out(offline_model, tmp_path):
    (tmp_path / "results.json").write_text("{}")
    with pytest.raises(FileExistsError, match="one directory holds one run"):
        evaluate(EvalConfig(model="tiny@0", tasks=("toy",), out=tmp_path))
    with pytest.raises(FileExistsError):
        evaluate(EvalConfig(model="tiny@0", tasks=("toy",), out=tmp_path / "results.json"))


def test_eval_config_rejects_counts_below_one():
    with pytest.raises(ValueError, match="limit must be at least 1"):
        EvalConfig(model="tiny@0", tasks=("toy",), limit=0)
    with pytest.raises(ValueError, match="batch_tokens must be at least 1"):
        EvalConfig(model="tiny@0", tasks=("toy",), batch_tokens=0)


@pytest.mark.parametrize("flag", ["--limit", "--batch-tokens"])
def test_cli_rejects_counts_below_one(flag):
    argv = ["run", "--model", "m@0", "--task", "arc_easy", "--out", "o", flag, "0"]
    result = CliRunner().invoke(cli.main, argv)

    assert result.exit_code == 2
    assert "x>=1" in result.output


def test_cli_runs_and_prints_primary_metric_first(offline_model, tmp_path, monkeypatch):
    argv = ["run", "--model", "tiny@0", "--task", "arc_easy", "--out", str(tmp_path)]
    # `--task` only accepts registered names, fixed when the CLI is imported, so the toy task
    # runs under the name `arc_easy`.
    monkeypatch.setitem(run.TASKS, "arc_easy", TASK)
    # The CLI records `sys.argv`, which `CliRunner` leaves alone.
    monkeypatch.setattr(sys, "argv", ["flock-eval", *argv])

    result = CliRunner().invoke(cli.main, argv, catch_exceptions=False)

    out = result.output.splitlines()
    assert out[0] == "model tiny@0  flock abc123"
    assert out[2].split()[0] == "acc_per_char"
    data = json.loads((tmp_path / "results.json").read_text())
    assert data["provenance"]["command"] == "flock-eval " + " ".join(argv)


def test_run_loads_the_weights_in_the_dtype_asked_for(offline_model, scorer, monkeypatch, tmp_path):
    loaded = []

    def from_pretrained(*args, **kwargs):
        loaded.append(kwargs["dtype"])
        return scorer

    monkeypatch.setattr(ScoringModel, "from_pretrained", from_pretrained)
    monkeypatch.setitem(run.TASKS, "arc_easy", TASK)
    argv = ["run", "--model", "tiny@0", "--task", "arc_easy", "--out", str(tmp_path)]

    evaluate(EvalConfig(model="tiny@0", tasks=("arc_easy",)))
    CliRunner().invoke(cli.main, [*argv, "--dtype", "float32"], catch_exceptions=False)

    assert loaded == [torch.bfloat16, torch.float32]  # bfloat16 unless asked
    with pytest.raises(ValueError, match="dtype must be bfloat16 or float32"):
        EvalConfig(model="tiny@0", tasks=("arc_easy",), dtype="float16")  # ty: ignore[invalid-argument-type]


def test_cli_needs_a_task_or_suite():
    result = CliRunner().invoke(cli.main, ["run", "--model", "m@0", "--out", "o"])

    assert result.exit_code == 2
    assert "at least one task or suite" in result.output


def test_cli_prints_suite_scores(toy_suites, tmp_path, monkeypatch):
    # `--suite` only accepts names registered when the CLI was imported, so the toy group takes
    # a registered name.
    monkeypatch.setitem(run.SUITES, "base_easy", dataclasses.replace(GROUP, name="base_easy"))
    argv = ["run", "--model", "tiny@0", "--suite", "base_easy", "--out", str(tmp_path)]
    monkeypatch.setattr(sys, "argv", ["flock-eval", *argv])

    result = CliRunner().invoke(cli.main, argv, catch_exceptions=False)

    out = result.output.splitlines()
    assert out[-3].startswith("toy:rc  0.")
    assert out[-1] == "base_easy  unscored  (toy:rc, toy:bpb)"
