from typing import Self

import pytest
import wandb

from flock.eval.results import Results, SuiteResult
from flock.eval.tracking import log_to_tracker

from .toy import PROVENANCE, task_result


class FakeRun:
    """A run that records its summary and how it was finished, as `wandb.Run` does on leaving a `with` block (`Run.__exit__` in wandb 0.31.0)."""

    def __init__(self, calls: dict, summary: object) -> None:
        self.calls = calls
        self.summary = summary

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.calls["exit_code"] = 1 if exc_type is not None else 0


@pytest.fixture
def logged(monkeypatch):
    """Captures what `wandb.init` and the run receive, without the SDK."""
    calls = {"summary": {}}

    def init(**kwargs):
        calls["init"] = kwargs
        return FakeRun(calls, calls["summary"])

    monkeypatch.setattr(wandb, "init", init)
    return calls


def test_log_to_tracker_logs_scored_suites_and_members(logged):
    results = Results(
        PROVENANCE,
        [
            task_result("toy", {"acc_raw": 0.5, "bpb": 1.0}),
            task_result("other", {"acc_raw": 0.7}, seconds=0.0),
        ],
        [
            SuiteResult("rc", "hash-rc", ["toy", "other"], None, 0.6),
            SuiteResult("group", "hash-group", ["rc"], None, None),
        ],
    )

    log_to_tracker(results, "project")

    assert logged["init"]["project"] == "project"
    assert logged["init"]["config"]["task_hashes"] == {"toy": "hash-toy", "other": "hash-other"}
    assert logged["init"]["config"]["suite_members"] == {"rc": ["toy", "other"], "group": ["rc"]}
    assert logged["init"]["config"]["suite_hashes"] == {"rc": "hash-rc", "group": "hash-group"}
    assert logged["summary"]["toy/acc_raw"] == 0.5
    assert logged["summary"]["suites/rc"] == 0.6
    assert "suites/group" not in logged["summary"]  # unscored: nothing to log
    assert logged["summary"]["toy/docs_per_second"] == 2.0
    assert "other/docs_per_second" not in logged["summary"]  # timed at 0 s: no throughput
    assert logged["exit_code"] == 0


def test_a_failed_summary_update_finishes_the_run_as_failed(monkeypatch):
    calls = {}

    class FailingSummary:
        def update(self, values):
            raise ConnectionError("tracker unreachable")

    monkeypatch.setattr(wandb, "init", lambda **kwargs: FakeRun(calls, FailingSummary()))

    with pytest.raises(ConnectionError):
        log_to_tracker(Results(PROVENANCE, [task_result("toy", {"acc_raw": 0.5})]), "project")

    assert calls["exit_code"] == 1
