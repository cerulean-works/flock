from types import SimpleNamespace

import pytest
import wandb

from flock.eval.provenance import Provenance
from flock.eval.results import Results, SuiteResult, TaskResult, Truncation
from flock.eval.tracking import log_to_tracker

PROVENANCE = Provenance(
    model="tiny@0",
    flock_commit="abc123",
    command="test",
    device="cpu",
    dtype="float32",
    batch_tokens=64,
    max_length=128,
    attention="eager",
    torch="2.8.0",
    transformers="4.57.6",
    python="3.14.0",
    created_at="now",
)


def task_result(name: str, metrics: dict[str, float]) -> TaskResult:
    return TaskResult(
        task=name,
        task_hash="hash-" + name,
        primary_metric=next(iter(metrics)),
        metrics=metrics,
        num_docs=2,
        limit=None,
        seconds=1.0,
        truncation=Truncation(requests=0, docs=0, max_tokens_cut=0),
    )


@pytest.fixture
def logged(monkeypatch):
    """Captures what `wandb.init` and the run receive, without the SDK."""
    calls = {}

    def init(**kwargs):
        calls["init"] = kwargs
        calls["summary"] = {}
        return SimpleNamespace(
            summary=calls["summary"], finish=lambda: calls.setdefault("finished", True)
        )

    monkeypatch.setattr(wandb, "init", init)
    return calls


def test_log_to_tracker_logs_scored_suites_and_members(logged):
    results = Results(
        PROVENANCE,
        [task_result("toy", {"acc_raw": 0.5, "bpb": 1.0}), task_result("other", {"acc_raw": 0.7})],
        [
            SuiteResult("rc", ["toy", "other"], None, 0.6),
            SuiteResult("group", ["rc"], None, None),
        ],
    )

    log_to_tracker(results, "project")

    assert logged["init"]["project"] == "project"
    assert logged["init"]["config"]["task_hashes"] == {"toy": "hash-toy", "other": "hash-other"}
    assert logged["init"]["config"]["suite_members"] == {"rc": ["toy", "other"], "group": ["rc"]}
    assert logged["summary"]["toy/acc_raw"] == 0.5
    assert logged["summary"]["suites/rc"] == 0.6
    assert "suites/group" not in logged["summary"]  # unscored: nothing to log
    assert logged["finished"]
