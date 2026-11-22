from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import TYPE_CHECKING, Never, Self

import httpx
import pytest
import wandb
from click.testing import CliRunner

from flock.cli import api
from flock.eval.cli import main
from flock.eval.results import Results, SuiteResult, read_results
from flock.eval.tracking import log_results_file, log_to_tracker, read_run_file

from .toy import PROVENANCE, task_result

if TYPE_CHECKING:
    from types import TracebackType
    from typing import Any


# Cut from banked results files: their provenance as written, and a suite with its tasks.
LOGGED = Path(__file__).parent / "logged"
HOST = "https://flock.test"


class FakeRun:
    """A run that records its summary, the files it saves and how it was finished, as `wandb.Run` does on leaving a `with` block (`Run.__exit__` in wandb 0.31.0)."""

    def __init__(
        self, calls: dict, summary: object, folder: Path | None = None, **init: Any
    ) -> None:
        self.calls = calls
        self.summary = summary
        self.dir = str(folder)
        self.disabled = calls.get("disabled", False)
        self.offline = calls.get("offline", False)
        # Where the run went: where it was asked to go, unless a test overrides it.
        self.entity = calls.get("override", init).get("entity")
        self.project = calls.get("override", init).get("project")

    def save(self, glob_str: str, base_path: str, policy: str, glob: bool) -> None:
        assert glob is False  # a task name may hold `[`
        # What the tracker would receive: each file by its name on the run, and its contents.
        name = str(Path(glob_str).relative_to(base_path))
        self.calls.setdefault("files", {})[name] = (Path(glob_str).read_text(), policy)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.calls["exit_code"] = 1 if exc_type is not None else 0


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Captures what `wandb.init` and the run receive, without the SDK."""
    calls = {"summary": {}}

    def init(**kwargs: Any) -> FakeRun:
        calls["init"] = kwargs
        return FakeRun(calls, calls["summary"], tmp_path / "wandb-files", **kwargs)

    monkeypatch.setattr(wandb, "init", init)
    return calls


@dataclasses.dataclass
class FakeHub:
    """A Flock API whose run query finds the runs in `found`, or answers `status` with `error`, and records each query. Its project `acme/model-evals` answers `project` (its visibility, or an error as a status and body), and its run `acme/model-evals/run-1` holds `files`."""

    found: list[str] = dataclasses.field(default_factory=list)
    status: int = 200
    error: dict = dataclasses.field(default_factory=lambda: {"error": "refused"})
    queries: list[dict] = dataclasses.field(default_factory=list)
    project: str | tuple[int, dict] = "private"
    files: dict[str, str] = dataclasses.field(default_factory=dict)

    def answer(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/me":
            return httpx.Response(200, json={"default_org": "acme"})
        if path == "/api/v1/projects/acme/model-evals":
            if isinstance(self.project, tuple):
                return httpx.Response(self.project[0], json=self.project[1])
            return httpx.Response(200, json={"visibility": self.project})
        prefix = "/api/v1/projects/acme/model-evals/runs/run-1/files/"
        if path.startswith(prefix):
            name = path.removeprefix(prefix)
            if name in self.files:
                return httpx.Response(200, text=self.files[name])
            return httpx.Response(404, json={"error": "file not found"})
        if path.startswith("/api/v1/projects/acme/model-evals/runs/") and "/files/" in path:
            return httpx.Response(404, json={"error": "run not found"})
        assert path == "/api/v1/projects/acme/model-evals/runs/query"
        self.queries.append(json.loads(request.content))
        if self.status != 200:
            return httpx.Response(self.status, json=self.error)
        return httpx.Response(200, json={"total": len(self.found), "ids": self.found})


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> FakeHub:
    """A fake Flock API, signed in to through `FLOCK_TOKEN`."""
    fake = FakeHub()
    monkeypatch.setenv("FLOCK_HOST", HOST)
    monkeypatch.setenv("FLOCK_TOKEN", "flock-test")
    monkeypatch.setattr(api, "TRANSPORT", httpx.MockTransport(fake.answer))
    return fake


def test_log_to_tracker_logs_scored_suites_and_members(logged: dict[str, Any]) -> None:
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


def test_a_failed_summary_update_finishes_the_run_as_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {}

    class FailingSummary:
        def update(self, values: dict[str, Any]) -> Never:
            raise ConnectionError("tracker unreachable")

    monkeypatch.setattr(wandb, "init", lambda **kwargs: FakeRun(calls, FailingSummary()))

    results = Results(PROVENANCE, [task_result("toy", {"acc_raw": 0.5})])
    with pytest.raises(ConnectionError):
        log_to_tracker(results.to_dict(), "project")

    assert calls["exit_code"] == 1


def test_log_to_tracker_sends_a_flock_model_and_its_digest_in_the_config(
    logged: dict[str, Any],
) -> None:
    # The run's config names the exact version scored, by number and digest.
    provenance = dataclasses.replace(
        PROVENANCE, model="flock:acme/olmo2-1b@12", model_digest="d" * 64
    )

    results = Results(provenance, [task_result("toy", {"acc_raw": 0.5})])
    log_to_tracker(results.to_dict(), "project")

    assert logged["init"]["config"]["model"] == "flock:acme/olmo2-1b@12"
    assert logged["init"]["config"]["model_digest"] == "d" * 64


def test_a_written_file_logs_the_run_its_live_run_logged(
    logged: dict[str, Any], hub: FakeHub, tmp_path: Path
) -> None:
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
def test_a_banked_file_logs_its_provenance_as_written(
    logged: dict[str, Any], hub: FakeHub, schema: int
) -> None:
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
    assert logged["init"]["resume"] == "never"  # never into an existing run
    (query,) = hub.queries
    assert query["filter"] == {
        "$and": [
            {"config.model": {"$eq": written["provenance"]["model"]}},
            {"config.created_at": {"$eq": written["provenance"]["created_at"]}},
            {"state": {"$nin": ["failed", "crashed"]}},  # a failed log may hold no scores
        ]
    }


def test_a_null_score_in_a_written_file_is_not_logged(
    logged: dict[str, Any], hub: FakeHub, tmp_path: Path
) -> None:
    # `Results.write` turns an infinite or NaN score into null; the run has no value for it.
    results = Results(
        PROVENANCE,
        [task_result("toy", {"bpb": float("inf"), "acc_raw": 0.5})],
        [SuiteResult("rc", "hash-rc", ["toy"], "bpb", float("inf"))],
    )

    log_results_file(results.write(tmp_path), "model-evals")

    assert logged["summary"] == {"toy/acc_raw": 0.5, "toy/docs_per_second": 2.0}


def test_a_schema_6_file_logs_no_kernel_and_no_bos(logged: dict[str, Any], hub: FakeHub) -> None:
    # Schema 6 predates both fields. Its missing `sdpa_backend` means an unknown kernel, which a
    # logged `None` (no pin applied) would misstate.
    log_results_file(LOGGED / "schema6.json", "model-evals")

    assert "sdpa_backend" not in logged["init"]["config"]
    assert "add_bos" not in logged["init"]["config"]


def test_a_file_already_logged_is_refused_before_any_run(
    logged: dict[str, Any], hub: FakeHub
) -> None:
    hub.found = ["run-1"]

    with pytest.raises(ValueError, match="already logged as acme/model-evals/run-1"):
        log_results_file(LOGGED / "schema8.json", "model-evals")

    assert "init" not in logged


def test_a_project_that_does_not_exist_yet_holds_no_duplicate(
    logged: dict[str, Any], hub: FakeHub
) -> None:
    hub.status, hub.error = 404, {"error": "project acme/model-evals not found"}

    log_results_file(LOGGED / "schema7.json", "model-evals")

    assert logged["init"]["project"] == "model-evals"


def test_a_404_from_anything_but_a_missing_project_logs_nothing(
    logged: dict[str, Any], hub: FakeHub
) -> None:
    # A server without the run query answers a bare 404, which must not pass as "no project".
    hub.status, hub.error = 404, {"detail": "Not Found"}

    with pytest.raises(ValueError, match="refused the run query"):
        log_results_file(LOGGED / "schema7.json", "model-evals")

    assert "init" not in logged


def test_a_refused_query_logs_nothing(logged: dict[str, Any], hub: FakeHub) -> None:
    hub.status = 403

    with pytest.raises(ValueError, match="refused the run query"):
        log_results_file(LOGGED / "schema7.json", "model-evals")

    assert "init" not in logged


def test_schemas_before_6_are_refused_before_any_query(
    logged: dict[str, Any], hub: FakeHub, tmp_path: Path
) -> None:
    path = tmp_path / "results.json"
    written = json.loads((LOGGED / "schema6.json").read_text())
    path.write_text(json.dumps({**written, "schema_version": 5}))

    with pytest.raises(ValueError, match="schema 5; only 6 to 10 log"):
        log_results_file(path, "model-evals")

    assert hub.queries == []


def test_the_log_command_prints_where_the_run_went_or_why_not(
    logged: dict[str, Any], hub: FakeHub
) -> None:
    path = str(LOGGED / "schema8.json")

    result = CliRunner().invoke(main, ["log", path, "--project", "model-evals"])
    assert result.exit_code == 0, result.output
    assert result.output == f"logged {path} to acme/model-evals\n"

    hub.found = ["run-1"]
    refused = CliRunner().invoke(main, ["log", path, "--project", "model-evals"])
    assert refused.exit_code == 1
    assert "already logged as acme/model-evals/run-1" in refused.output


def test_the_log_command_reports_a_tracker_refusal_in_one_line(
    hub: FakeHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(**kwargs: Any) -> Never:
        raise wandb.errors.AuthenticationError("API key is invalid")

    monkeypatch.setattr(wandb, "init", refuse)

    result = CliRunner().invoke(
        main, ["log", str(LOGGED / "schema8.json"), "--project", "model-evals"]
    )

    assert result.exit_code == 1
    assert result.output == "Error: API key is invalid\n"


def written_run(folder: Path, private: bool = False) -> Path:
    """Writes a schema 9 run of two tasks with their predictions into `folder`, `exam` on a dataset that is private if `private` is set, and returns its results.json."""
    exam = task_result("exam", {"acc_per_char": 0.5})
    exam = dataclasses.replace(exam, dataset=dataclasses.replace(exam.dataset, private=private))
    path = Results(PROVENANCE, [task_result("toy", {"acc_raw": 0.5}), exam]).write(folder)
    for name in ("toy", "exam"):
        rows = [
            {"id": "q-1", "gold": 0, "correct": True},
            {"id": "q-2", "gold": 1, "correct": False},
        ]
        (folder / "predictions").mkdir(exist_ok=True)
        (folder / "predictions" / f"{name}.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows)
        )
    return path


def test_a_live_run_uploads_its_results_and_predictions(
    logged: dict[str, Any], tmp_path: Path
) -> None:
    path = written_run(tmp_path / "run")
    results = json.loads(path.read_text())

    log_to_tracker(results, "project", results_file=path)

    # Every file under its name in the folder, as written, uploading at once.
    assert logged["files"] == {
        "results.json": (path.read_text(), "now"),
        "predictions/toy.jsonl": ((tmp_path / "run/predictions/toy.jsonl").read_text(), "now"),
        "predictions/exam.jsonl": ((tmp_path / "run/predictions/exam.jsonl").read_text(), "now"),
    }
    # Nothing private, so the SDK's own settings still decide where the run goes.
    assert logged["init"]["entity"] is None
    assert logged["init"]["settings"] is None
    # A run of its own even inside a training script's active run, and never a resumed one.
    assert logged["init"]["reinit"] == "create_new"
    assert logged["init"]["resume"] == "never"
    # Staged copies are readable by their owner only.
    for staged in (tmp_path / "wandb-files").rglob("*.json*"):
        assert staged.stat().st_mode & 0o777 == 0o600


def test_a_task_name_that_leaves_the_predictions_folder_is_refused(
    logged: dict[str, Any], tmp_path: Path
) -> None:
    path = written_run(tmp_path / "run")
    results = json.loads(path.read_text())
    results["tasks"][0]["task"] = "../toy"

    with pytest.raises(ValueError, match="can't name a predictions file"):
        log_to_tracker(results, "project", results_file=path)

    assert "init" not in logged


def test_a_disabled_sdk_stages_no_files(logged: dict[str, Any], tmp_path: Path) -> None:
    logged["disabled"] = True  # as WANDB_MODE=disabled gives
    path = written_run(tmp_path / "run")

    log_to_tracker(json.loads(path.read_text()), "project", results_file=path)

    assert "files" not in logged
    assert not (tmp_path / "wandb-files").exists()


def test_a_linked_predictions_file_is_refused(logged: dict[str, Any], tmp_path: Path) -> None:
    path = written_run(tmp_path / "run")
    secret = tmp_path / "secret.txt"
    secret.write_text("not a prediction")
    linked = tmp_path / "run" / "predictions" / "toy.jsonl"
    linked.unlink()
    linked.symlink_to(secret)

    with pytest.raises(ValueError, match="is a link"):
        log_to_tracker(json.loads(path.read_text()), "project", results_file=path)

    assert "init" not in logged


def test_a_schema_9_task_without_a_privacy_mark_is_refused(
    logged: dict[str, Any], tmp_path: Path
) -> None:
    # A damaged file must not pass as public.
    path = written_run(tmp_path / "run")
    results = json.loads(path.read_text())
    del results["tasks"][1]["dataset"]["private"]

    with pytest.raises(ValueError, match="exam: dataset.private is None"):
        log_to_tracker(results, "project", results_file=path)

    assert "init" not in logged


def test_a_banked_copy_arrives_as_results_json_and_an_older_schema_without_predictions(
    logged: dict[str, Any], hub: FakeHub, tmp_path: Path
) -> None:
    # A schema 8 file beside predictions: they have no outcome, so only the results go.
    folder = tmp_path / "bank"
    written_run(folder)
    banked = folder / "olmo2-1b-results.json"
    banked.write_text((LOGGED / "schema8.json").read_text())

    log_results_file(banked, "model-evals")

    assert list(logged["files"]) == ["results.json"]
    assert logged["files"]["results.json"][0] == banked.read_text()


def test_a_renamed_schema_9_file_is_refused_since_the_predictions_beside_it_may_not_be_its(
    logged: dict[str, Any], hub: FakeHub, tmp_path: Path
) -> None:
    path = written_run(tmp_path / "run")
    renamed = path.with_name("olmo2-1b-results.json")
    path.rename(renamed)

    with pytest.raises(ValueError, match="isn't a file named results.json"):
        log_results_file(renamed, "model-evals")

    assert "init" not in logged


def test_a_linked_results_file_or_a_repeated_task_is_refused_before_any_run(
    logged: dict[str, Any], hub: FakeHub, tmp_path: Path
) -> None:
    # A link would pair the target's privacy marks with the predictions beside the link.
    path = written_run(tmp_path / "run", private=True)
    other = tmp_path / "other"
    other.mkdir()
    (other / "results.json").symlink_to(path)
    with pytest.raises(ValueError, match="isn't a file named results.json"):
        log_results_file(other / "results.json", "model-evals")

    # Repeated with another mark, one file would go up under both names.
    results = json.loads(path.read_text())
    results["tasks"].append({**results["tasks"][1], "dataset": {"private": False}})
    with pytest.raises(ValueError, match="exam.jsonl is the predictions file of two tasks"):
        log_to_tracker(results, "model-evals", results_file=path)

    assert "init" not in logged


def test_private_predictions_go_only_to_a_private_project(
    logged: dict[str, Any], hub: FakeHub, tmp_path: Path
) -> None:
    path = written_run(tmp_path / "run", private=True)
    results = json.loads(path.read_text())

    log_to_tracker(results, "model-evals", results_file=path)

    # The run goes to the API and organization whose project was read as private.
    assert logged["init"]["entity"] == "acme"
    assert logged["init"]["settings"].base_url == HOST
    # Under `private/`, which the tracker keeps to members if the project is made public.
    assert set(logged["files"]) == {
        "results.json",
        "predictions/toy.jsonl",
        "private/predictions/exam.jsonl",
    }


@pytest.mark.parametrize(
    ("project", "match"),
    [
        ("public", "acme/model-evals is public, and private/predictions/exam.jsonl hold"),
        ((403, {"error": "forbidden"}), "refused the project query \\(forbidden\\)"),
        ((404, {"detail": "Not Found"}), "refused the project query"),
    ],
)
def test_private_predictions_are_refused_before_any_run_unless_the_project_is_private(
    logged: dict[str, Any],
    hub: FakeHub,
    tmp_path: Path,
    project: str | tuple[int, dict],
    match: str,
) -> None:
    hub.project = project
    path = written_run(tmp_path / "run", private=True)

    with pytest.raises(ValueError, match=match):
        log_to_tracker(json.loads(path.read_text()), "model-evals", results_file=path)

    assert "init" not in logged  # no half-uploaded run


def test_private_predictions_never_follow_a_run_sent_elsewhere(
    logged: dict[str, Any], hub: FakeHub, tmp_path: Path
) -> None:
    # As a sweep agent's settings would send it.
    logged["override"] = {"entity": "acme", "project": "public-sweep"}
    path = written_run(tmp_path / "run", private=True)

    with pytest.raises(ValueError, match="went to acme/public-sweep, not the checked"):
        log_to_tracker(json.loads(path.read_text()), "model-evals", results_file=path)

    assert "files" not in logged
    assert logged["exit_code"] == 1  # finished as failed


def test_an_offline_run_stages_no_private_predictions(
    logged: dict[str, Any], hub: FakeHub, tmp_path: Path
) -> None:
    # `wandb sync` could later send it anywhere.
    logged["offline"] = True
    path = written_run(tmp_path / "run", private=True)

    with pytest.raises(ValueError, match="private predictions are uploaded only online"):
        log_to_tracker(json.loads(path.read_text()), "model-evals", results_file=path)

    assert "files" not in logged


def test_a_live_private_run_keeps_the_sdk_mode(
    logged: dict[str, Any], hub: FakeHub, tmp_path: Path
) -> None:
    # Only a backfill is forced online; a live run under WANDB_MODE=offline stays offline.
    path = written_run(tmp_path / "run", private=True)

    log_to_tracker(json.loads(path.read_text()), "model-evals", results_file=path)

    assert logged["init"]["settings"].base_url == HOST
    assert "mode" not in logged["init"]["settings"].model_fields_set  # left to WANDB_MODE


def test_private_predictions_may_create_a_project_which_starts_private(
    logged: dict[str, Any], hub: FakeHub, tmp_path: Path
) -> None:
    hub.project = (404, {"error": "project acme/model-evals not found"})
    path = written_run(tmp_path / "run", private=True)

    log_to_tracker(json.loads(path.read_text()), "model-evals", results_file=path)

    assert logged["init"]["project"] == "model-evals"


def test_private_predictions_need_a_flock_sign_in(
    logged: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FLOCK_TOKEN", raising=False)
    monkeypatch.setattr("flock.cli.credentials.load", lambda: None)
    path = written_run(tmp_path / "run", private=True)

    with pytest.raises(ValueError, match="Not logged in"):
        log_to_tracker(json.loads(path.read_text()), "project", results_file=path)

    assert "init" not in logged


def test_a_run_reads_back_as_its_folder_does(hub: FakeHub, tmp_path: Path) -> None:
    path = written_run(tmp_path / "run")
    for name in ("results.json", "predictions/toy.jsonl"):
        hub.files[name] = (tmp_path / "run" / name).read_text()

    remote = read_results("run:acme/model-evals/run-1")
    local = read_results(str(tmp_path / "run"))

    assert (remote.provenance, remote.tasks) == (local.provenance, local.tasks)
    assert remote.predictions("toy") == local.predictions("toy")
    assert remote.predictions("exam") is None  # not uploaded
    assert read_run_file("acme/model-evals/run-1", "results.json") == path.read_text()


def test_a_run_reads_a_private_tasks_predictions_from_its_private_folder(
    hub: FakeHub, tmp_path: Path
) -> None:
    written_run(tmp_path / "run", private=True)
    folder = tmp_path / "run"
    hub.files["results.json"] = (folder / "results.json").read_text()
    hub.files["private/predictions/exam.jsonl"] = (folder / "predictions/exam.jsonl").read_text()
    hub.files["predictions/toy.jsonl"] = (folder / "predictions/toy.jsonl").read_text()

    remote = read_results("run:acme/model-evals/run-1")
    local = read_results(str(folder))

    # The folder keeps every task in predictions/; only the run names private ones apart.
    assert remote.predictions("exam") == local.predictions("exam")
    assert remote.predictions("toy") == local.predictions("toy")
    # A copy under the public name is never read for a private task.
    hub.files["predictions/exam.jsonl"] = "not read\n"
    del hub.files["private/predictions/exam.jsonl"]
    assert remote.predictions("exam") is None


def test_a_run_keeps_reading_with_the_sign_in_it_was_opened_with(
    hub: FakeHub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written_run(tmp_path / "run")
    for name in ("results.json", "predictions/toy.jsonl"):
        hub.files[name] = (tmp_path / "run" / name).read_text()
    remote = read_results("run:acme/model-evals/run-1")

    # Signing out (or into another server) after opening doesn't change where rows come from.
    monkeypatch.delenv("FLOCK_TOKEN")
    monkeypatch.setattr("flock.cli.credentials.load", lambda: None)

    assert remote.predictions("toy") is not None


def test_reading_a_run_refuses_a_bad_path_and_an_unknown_run(hub: FakeHub) -> None:
    with pytest.raises(ValueError, match="expected a run as entity/project/id"):
        read_run_file("acme/model-evals", "results.json")
    with pytest.raises(ValueError, match="expected a run as entity/project/id"):
        read_run_file("acme/model-evals/..", "results.json")
    with pytest.raises(ValueError, match="isn't a file name inside"):
        read_run_file("acme/model-evals/run-1", "../run-2/results.json")
    with pytest.raises(ValueError, match="run not found"):
        read_run_file("acme/model-evals/run-2", "results.json")
    with pytest.raises(FileNotFoundError, match="has no results.json"):
        read_results("run:acme/model-evals/run-1")
