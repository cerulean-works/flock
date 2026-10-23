import dataclasses
import json
import sys

import pytest
import torch
from click.testing import CliRunner

from flock.eval import cli, run
from flock.eval.metrics import MULTIPLE_CHOICE_METRICS, ChoiceScores
from flock.eval.model import ScoringModel
from flock.eval.results import prediction_rows
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
    assert data["schema_version"] == 4
    assert data["provenance"]["model"] == "tiny@0"
    assert data["provenance"]["flock_commit"] == "abc123"
    assert data["provenance"]["dtype"] == "float32"
    assert data["provenance"]["max_length"] == 128
    assert data["provenance"]["attention"] == "sdpa"
    [task] = data["tasks"]
    assert task["task"] == "toy"
    assert task["num_docs"] == 2
    assert task["task_hash"] == TASK.requests(*TASK.load()).hash
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


def test_evaluate_rejects_two_tasks_under_one_name(toy_suites, monkeypatch):
    monkeypatch.setitem(run.TASKS, "other", dataclasses.replace(OTHER, description="x"))
    with pytest.raises(ValueError, match="two different tasks are named other"):
        evaluate(EvalConfig(model="tiny@0", tasks=("other",), suites=("toy:rc",)))


def test_prediction_rows_drop_padded_choices():
    docs = [
        MCDoc(id="a", choices=cloze("?", ("x", "y")), gold=1),
        MCDoc(id="b", choices=cloze("?", ("x", "y", "z")), gold=0),
    ]
    counts = torch.tensor([[1, 2, 0], [3, 4, 5]])
    scores = ChoiceScores(
        logprob=-counts.float(),
        num_tokens=counts,
        num_chars=counts,
        num_bytes=counts,
        valid=counts > 0,
        gold=torch.tensor([1, 0]),
    )

    rows = prediction_rows(docs, scores)

    assert rows == [
        {"id": "a", "gold": 1, "logprob": [-1.0, -2.0], "num_tokens": [1, 2]},
        {"id": "b", "gold": 0, "logprob": [-3.0, -4.0, -5.0], "num_tokens": [3, 4, 5]},
    ]


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
    with pytest.raises(FileExistsError):
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


def test_cli_needs_a_task_or_suite():
    result = CliRunner().invoke(cli.main, ["run", "--model", "m@0", "--out", "o"])

    assert result.exit_code == 2
    assert "at least one --task or --suite" in result.output


def test_cli_prints_suite_scores(toy_suites, tmp_path, monkeypatch):
    # `--suite` only accepts registered names, fixed when the CLI is imported.
    monkeypatch.setitem(run.SUITES, "base_easy", GROUP)
    argv = ["run", "--model", "tiny@0", "--suite", "base_easy", "--out", str(tmp_path)]
    monkeypatch.setattr(sys, "argv", ["flock-eval", *argv])

    result = CliRunner().invoke(cli.main, argv, catch_exceptions=False)

    out = result.output.splitlines()
    assert out[-3].startswith("toy:rc  0.")
    assert out[-1] == "toys  unscored  (toy:rc, toy:bpb)"
