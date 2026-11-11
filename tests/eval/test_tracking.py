import dataclasses
import json
from pathlib import Path
from typing import Self

import httpx
import pytest
import wandb
from click.testing import CliRunner

from flock.cli import api
from flock.eval.cli import main
from flock.eval.results import Results, SuiteResult
from flock.eval.tracking import log_results_file, log_to_tracker

from .toy import PROVENANCE, task_result

# Cut from banked results files: their provenance as written, and a suite with its tasks.
LOGGED = Path(__file__).parent / "logged"
HOST = "https://flock.test"


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


@dataclasses.dataclass
class FakeHub:
    """A Flock API whose run query finds the runs in `found`, or answers `status` with `error`, and records each query."""

    found: list[str] = dataclasses.field(default_factory=list)
    status: int = 200
    error: dict = dataclasses.field(default_factory=lambda: {"error": "refused"})
    queries: list[dict] = dataclasses.field(default_factory=list)

    def answer(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/me":
            return httpx.Response(200, json={"default_org": "acme"})
        assert request.url.path == "/api/v1/projects/acme/model-evals/runs/query"
        self.queries.append(json.loads(request.content))
        if self.status != 200:
            return httpx.Response(self.status, json=self.error)
        return httpx.Response(200, json={"total": len(self.found), "ids": self.found})


@pytest.fixture
def hub(monkeypatch) -> FakeHub:
    """A fake Flock API, signed in to through `FLOCK_TOKEN`."""
    fake = FakeHub()
    monkeypatch.setenv("FLOCK_HOST", HOST)
    monkeypatch.setenv("FLOCK_TOKEN", "flock-test")
    monkeypatch.setattr(api, "TRANSPORT", httpx.MockTransport(fake.answer))
    return fake


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

    log_to_tracker(results.to_dict(), "project")

    # A live run's row: the SDK's own entity and host, and the whole provenance.
    assert logged["init"]["project"] == "project"
    assert logged["init"]["entity"] is None
    assert logged["init"]["settings"] is None
    assert logged["init"]["job_type"] == "eval"
    assert logged["init"]["config"] == {
        **dataclasses.asdict(PROVENANCE),
        "task_hashes": {"toy": "hash-toy", "other": "hash-other"},
        "suite_members": {"rc": ["toy", "other"], "group": ["rc"]},
        "suite_hashes": {"rc": "hash-rc", "group": "hash-group"},
    }
    assert logged["summary"] == {
        "toy/acc_raw": 0.5,
        "toy/bpb": 1.0,
        "toy/docs_per_second": 2.0,
        "other/acc_raw": 0.7,  # timed at 0 s: no throughput
        "suites/rc": 0.6,  # `group` is unscored: nothing to log
    }
    assert logged["exit_code"] == 0


def test_a_failed_summary_update_finishes_the_run_as_failed(monkeypatch):
    calls = {}

    class FailingSummary:
        def update(self, values):
            raise ConnectionError("tracker unreachable")

    monkeypatch.setattr(wandb, "init", lambda **kwargs: FakeRun(calls, FailingSummary()))

    results = Results(PROVENANCE, [task_result("toy", {"acc_raw": 0.5})])
    with pytest.raises(ConnectionError):
        log_to_tracker(results.to_dict(), "project")

    assert calls["exit_code"] == 1


def test_log_to_tracker_sends_a_flock_model_and_its_digest_in_the_config(logged):
    # The run's config names the exact version scored, by number and digest.
    provenance = dataclasses.replace(
        PROVENANCE, model="flock:acme/olmo2-1b@12", model_digest="d" * 64
    )

    results = Results(provenance, [task_result("toy", {"acc_raw": 0.5})])
    log_to_tracker(results.to_dict(), "project")

    assert logged["init"]["config"]["model"] == "flock:acme/olmo2-1b@12"
    assert logged["init"]["config"]["model_digest"] == "d" * 64


def test_a_written_file_logs_the_run_its_live_run_logged(logged, hub, tmp_path):
    results = Results(
        PROVENANCE,
        [task_result("toy", {"acc_raw": 0.5, "bpb": 1.0}), task_result("other", {"acc_raw": 0.7})],
        [SuiteResult("rc", "hash-rc", ["toy", "other"], None, 0.6)],
    )
    log_to_tracker(results.to_dict(), "model-evals")
    live = (logged["init"]["config"], dict(logged["summary"]))

    logged["summary"].clear()
    log_results_file(results.write(tmp_path), "model-evals")

    assert (logged["init"]["config"], logged["summary"]) == live


@pytest.mark.parametrize("schema", [6, 7, 8])
def test_a_banked_file_logs_its_provenance_as_written(logged, hub, schema):
    path = LOGGED / f"schema{schema}.json"
    written = json.loads(path.read_text())

    assert log_results_file(path, "model-evals") == "acme/model-evals"

    # Nothing is added to the provenance: a field the file lacks stays absent.
    config = logged["init"]["config"]
    hashes = {task["task"]: task["task_hash"] for task in written["tasks"]}
    assert config == {
        **written["provenance"],
        "task_hashes": hashes,
        "suite_members": {suite["suite"]: suite["members"] for suite in written["suites"]},
        "suite_hashes": {suite["suite"]: suite["suite_hash"] for suite in written["suites"]},
    }
    for task in written["tasks"]:
        for name, value in task["metrics"].items():
            assert logged["summary"][f"{task['task']}/{name}"] == value

    # The run goes where the duplicate check looked: the signed-in API and its default org.
    assert logged["init"]["entity"] == "acme"
    assert logged["init"]["settings"].base_url == HOST
    assert logged["init"]["settings"].mode == "online"  # never a silent offline run
    (query,) = hub.queries
    assert query["filter"] == {
        "$and": [
            {"config.model": {"$eq": written["provenance"]["model"]}},
            {"config.created_at": {"$eq": written["provenance"]["created_at"]}},
            {"state": {"$nin": ["failed", "crashed"]}},  # a failed log may hold no scores
        ]
    }


def test_a_null_score_in_a_written_file_is_not_logged(logged, hub, tmp_path):
    # `Results.write` turns an infinite or NaN score into null; the run has no value for it.
    results = Results(
        PROVENANCE,
        [task_result("toy", {"bpb": float("inf"), "acc_raw": 0.5})],
        [SuiteResult("rc", "hash-rc", ["toy"], "bpb", float("inf"))],
    )

    log_results_file(results.write(tmp_path), "model-evals")

    assert logged["summary"] == {"toy/acc_raw": 0.5, "toy/docs_per_second": 2.0}


def test_a_schema_6_file_logs_no_kernel_and_no_bos(logged, hub):
    # Schema 6 predates both fields. Its missing `sdpa_backend` means an unknown kernel, which a
    # logged `None` (no pin applied) would misstate.
    log_results_file(LOGGED / "schema6.json", "model-evals")

    assert "sdpa_backend" not in logged["init"]["config"]
    assert "add_bos" not in logged["init"]["config"]


def test_a_file_already_logged_is_refused_before_any_run(logged, hub):
    hub.found = ["run-1"]

    with pytest.raises(ValueError, match="already logged as acme/model-evals/run-1"):
        log_results_file(LOGGED / "schema8.json", "model-evals")

    assert "init" not in logged


def test_a_project_that_does_not_exist_yet_holds_no_duplicate(logged, hub):
    hub.status, hub.error = 404, {"error": "project acme/model-evals not found"}

    log_results_file(LOGGED / "schema7.json", "model-evals")

    assert logged["init"]["project"] == "model-evals"


def test_a_404_from_anything_but_a_missing_project_logs_nothing(logged, hub):
    # A server without the run query answers a bare 404, which must not pass as "no project".
    hub.status, hub.error = 404, {"detail": "Not Found"}

    with pytest.raises(ValueError, match="refused the run query"):
        log_results_file(LOGGED / "schema7.json", "model-evals")

    assert "init" not in logged


def test_a_refused_query_logs_nothing(logged, hub):
    hub.status = 403

    with pytest.raises(ValueError, match="refused the run query"):
        log_results_file(LOGGED / "schema7.json", "model-evals")

    assert "init" not in logged


def test_schemas_before_6_are_refused_before_any_query(logged, hub, tmp_path):
    path = tmp_path / "results.json"
    written = json.loads((LOGGED / "schema6.json").read_text())
    path.write_text(json.dumps({**written, "schema_version": 5}))

    with pytest.raises(ValueError, match="schema 5; only 6 to 8 log"):
        log_results_file(path, "model-evals")

    assert hub.queries == []


def test_the_log_command_prints_where_the_run_went_or_why_not(logged, hub):
    path = str(LOGGED / "schema8.json")

    result = CliRunner().invoke(main, ["log", path, "--project", "model-evals"])
    assert result.exit_code == 0, result.output
    assert result.output == f"logged {path} to acme/model-evals\n"

    hub.found = ["run-1"]
    refused = CliRunner().invoke(main, ["log", path, "--project", "model-evals"])
    assert refused.exit_code == 1
    assert "already logged as acme/model-evals/run-1" in refused.output
