import json

import pytest

from flock.eval import cli, run
from flock.eval.model import ScoringModel
from flock.eval.run import EvalConfig, evaluate, score_task

from .conftest import TASK


@pytest.fixture
def offline_model(monkeypatch, scorer):
    monkeypatch.setitem(run.TASKS, TASK.name, TASK)
    monkeypatch.setattr(ScoringModel, "from_pretrained", lambda *args, **kwargs: scorer)
    monkeypatch.setenv("FLOCK_COMMIT", "abc123")


def test_evaluate_writes_results_and_predictions(offline_model, tmp_path):
    results = evaluate(EvalConfig(model="tiny@0", tasks=("toy",), out=tmp_path))

    data = json.loads((tmp_path / "results.json").read_text())
    assert data["schema_version"] == 2
    assert data["provenance"]["model"] == "tiny@0"
    assert data["provenance"]["flock_commit"] == "abc123"
    assert data["provenance"]["dtype"] == "float32"
    assert data["provenance"]["max_length"] == 128
    assert data["provenance"]["attention"] == "sdpa"
    [task] = data["tasks"]
    assert task["task"] == "toy"
    assert task["num_docs"] == 2
    assert task["task_hash"] == TASK.requests(*TASK.load()).hash
    assert set(task["metrics"]) == {
        "acc_raw",
        "acc_per_token",
        "acc_per_char",
        "acc_per_byte",
        "acc_uncond",
        "bpb",
    }
    assert all(0 <= task["metrics"][m] <= 1 for m in task["metrics"] if m.startswith("acc"))
    assert task["metrics"]["bpb"] > 0
    assert task["docs_per_second"] > 0
    assert results.tasks[0].metrics == task["metrics"]

    lines = (tmp_path / "predictions" / "toy.jsonl").read_text().splitlines()
    first, second = map(json.loads, lines)
    assert (first["id"], first["gold"], len(first["logprob"])) == ("q-1", 0, 3)
    assert len(second["logprob_uncond"]) == 4


def test_score_task_does_not_depend_on_batching(scorer):
    alone = score_task(scorer, TASK)
    scorer.batch_tokens = 1
    assert score_task(scorer, TASK).metrics == pytest.approx(alone.metrics, abs=1e-5)


def test_evaluate_rejects_unknown_task():
    with pytest.raises(KeyError, match="nope"):
        evaluate(EvalConfig(model="tiny@0", tasks=("nope",)))


def test_eval_config_rejects_repeated_tasks():
    with pytest.raises(ValueError, match="tasks repeat"):
        EvalConfig(model="tiny@0", tasks=("toy", "toy"))


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
    with pytest.raises(SystemExit):
        cli.parse_args(["run", "--model", "m@0", "--task", "arc_easy", "--out", "o", flag, "0"])


def test_cli_runs_and_prints_primary_metric_first(offline_model, tmp_path, capsys, monkeypatch):
    argv = ["run", "--model", "tiny@0", "--task", "arc_easy", "--out", str(tmp_path)]
    args = cli.parse_args(argv)
    assert (args.model, args.tasks, args.limit) == ("tiny@0", ["arc_easy"], None)

    monkeypatch.setitem(run.TASKS, "arc_easy", TASK)
    cli.main(argv)
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "model tiny@0  flock abc123"
    assert out[2].split() == ["acc_per_char", out[2].split()[1]]
    data = json.loads((tmp_path / "results.json").read_text())
    assert data["provenance"]["command"] == "flock-eval " + " ".join(argv)
