"""Logging results and their files to the flock tracker through the `wandb` SDK, and reading the files back."""

import json
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import quote

import wandb

from flock.cli import credentials
from flock.cli.api import Api, error_of


def log_to_tracker(
    results: dict[str, Any],
    project: str,
    *,
    results_file: Path | None = None,
    entity: str | None = None,
    host: str | None = None,
) -> None:
    """
    Logs one `eval` run to `project`: the provenance, each task's hash (`task_hashes`) and each suite's members and hash (`suite_members`, `suite_hashes`) as its config, and each task's metrics and `docs_per_second` as its summary, named `<task>/<metric>`, with each scored suite's score as `suites/<suite>`. With `results_file`, the run also gets that file as `results.json` and, for schema 9, each task's `predictions/<task>.jsonl` found beside it.

    The SDK's own settings decide where it goes (`wandb login --host` for the flock tracker), unless `entity` or `host` name it. A run that uploads predictions of a task on a private dataset goes to the signed-in Flock API and, without `entity`, its user's default organization, where the project's visibility can be read.

    Args:
        results (`dict[str, Any]`):
            The results' JSON form: [`~eval.results.Results.to_dict`] for a live run, or a written `results.json` as read. Only the provenance fields it holds are logged.
        project (`str`):
            The tracker project.
        results_file (`Path`, *optional*):
            The written `results.json` of `results`, whose folder holds `predictions/`. Nothing is uploaded if not set.
        entity (`str`, *optional*):
            The organization to log to, or `None` for the SDK's default.
        host (`str`, *optional*):
            The tracker's URL, logged to online as a new run, or `None` for the SDK's settings.

    Raises:
        `ValueError`: if predictions of a task on a private dataset would go to a project that is public, or whose visibility can't be read (not signed in to Flock, or Flock refuses the query), before the run is created.
        `click.ClickException`: if Flock can't be reached for that check.
    """
    summary: dict[str, Any] = {}
    for task in results["tasks"]:
        for name, value in task["metrics"].items():
            # A written file holds an infinite or NaN score as null: absent, as a null suite is.
            if value is not None:
                summary[f"{task['task']}/{name}"] = value
        if task["docs_per_second"] is not None:
            summary[f"{task['task']}/docs_per_second"] = task["docs_per_second"]
    for suite in results["suites"]:
        if suite["score"] is not None:
            summary[f"suites/{suite['suite']}"] = suite["score"]

    config = {
        **results["provenance"],
        "task_hashes": {task["task"]: task["task_hash"] for task in results["tasks"]},
        # A suite's members grow as tasks are added, so a score is comparable only between runs
        # with the same members.
        "suite_members": {suite["suite"]: suite["members"] for suite in results["suites"]},
        # Two suite scores are comparable only when their hashes are equal.
        "suite_hashes": {suite["suite"]: suite["suite_hash"] for suite in results["suites"]},
    }

    # The files the tracker shows a run's docs from. An older file's predictions have no outcome
    # (`correct`) and its tasks no privacy mark, so only its results go.
    files = {}
    if results_file is not None:
        files["results.json"] = results_file
        for task in results["tasks"] if results["schema_version"] >= 9 else []:
            path = results_file.parent / "predictions" / f"{task['task']}.jsonl"
            if path.is_file():
                files[f"predictions/{task['task']}.jsonl"] = path

    # A run in a project has the project's visibility, so a private dataset's per-doc rows go
    # only to a project known to be private, checked before the run exists. A project the run
    # creates is private (the tracker's default).
    private = [
        task["task"]
        for task in results["tasks"]
        if task["dataset"].get("private") and f"predictions/{task['task']}.jsonl" in files
    ]
    if private:
        api, default_org = _signed_in(host)
        # The run goes where the visibility was read.
        entity, host = entity or default_org, api.host
        with api.client:
            answer = api.get(f"projects/{entity}/{project}")
        if answer.status_code == 200:
            visibility = answer.json()["visibility"]
        elif (
            answer.status_code == 404
            and error_of(answer) == f"project {entity}/{project} not found"
        ):
            visibility = "new"
        else:
            raise ValueError(f"{host} refused the project query ({error_of(answer)})")
        if visibility not in ("private", "new"):
            raise ValueError(
                f"{entity}/{project} is {visibility}, and the predictions of {', '.join(private)} "
                "read a private dataset: log to a private project"
            )

    # Leaving the block finishes the run, with exit code 1 if the update raised, so a failed log
    # isn't left running on the tracker.
    # Online even where `WANDB_MODE` says offline, so a backfill never reports a run it didn't
    # log, and never resumed, so a `WANDB_RUN_ID` left in the environment can't overwrite a run.
    settings = None
    if host is not None:
        settings = wandb.Settings(base_url=host, mode="online", resume="never")
    with wandb.init(
        entity=entity, project=project, job_type="eval", config=config, settings=settings
    ) as run:
        run.summary.update(summary)
        # Copied under their names on the run, so a banked copy named otherwise still arrives as
        # results.json. `now`: each starts uploading at once, and leaving the block waits for it.
        for name, path in files.items():
            copy = Path(run.dir) / name
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, copy)
            run.save(str(copy), base_path=run.dir, policy="now")


def log_results_file(path: Path, project: str) -> str:
    """
    Logs a written `results.json` to `project` as written, in the default organization of the signed-in Flock user, on the Flock API that user signed in to, and returns `entity/project`. The file goes on the run, with the predictions beside it for schema 9, as [`log_to_tracker`] uploads them.

    A field the file lacks stays absent from the run: an older file's missing `sdpa_backend` means an unknown kernel, not none. The tracker stamps the run with this machine's host, time and commit; the scoring job's are the config's `created_at` and `flock_commit`.

    Raises:
        `ValueError`: if the file's schema isn't 6 to 9, Flock refuses the sign-in or the query, the project already has a run with the file's `model` and `created_at` that didn't fail or crash, or the project is public and the predictions of a task on a private dataset would go to it.
        `click.ClickException`: if Flock can't be reached.
        `wandb.errors.Error`: if the tracker refuses the run, for example for a wandb key set for another server.
    """
    results = json.loads(path.read_text())
    # Schemas 6 to 9 hold every field the run is built from. Older files lack suite hashes, and
    # some lack suites.
    if results.get("schema_version") not in (6, 7, 8, 9):
        raise ValueError(f"{path} has schema {results.get('schema_version')}; only 6 to 9 log")
    provenance = results["provenance"]

    api, entity = _signed_in()
    with api.client:
        # A run already logged, live or by an earlier backfill, has the same model and start
        # time. `task_hashes` isn't matched: the tracker keeps it as one column per task. A run
        # that failed or crashed may hold no scores, so it doesn't count.
        query = {
            "filter": {
                "$and": [
                    {"config.model": {"$eq": provenance["model"]}},
                    {"config.created_at": {"$eq": provenance["created_at"]}},
                    {"state": {"$nin": ["failed", "crashed"]}},
                ]
            },
            "columns": [],
            "limit": 1,
        }
        answer = api.post(f"projects/{entity}/{project}/runs/query", query)

    # A project that doesn't exist yet holds no run; the first run creates it. Any other 404
    # (a server without the route) would skip the check, so it refuses.
    if answer.status_code == 200:
        runs = answer.json()["ids"]
    elif answer.status_code == 404 and error_of(answer) == f"project {entity}/{project} not found":
        runs = []
    else:
        raise ValueError(f"{api.host} refused the run query ({error_of(answer)})")
    if runs:
        raise ValueError(f"{path} is already logged as {entity}/{project}/{runs[0]}")

    # The run goes to the API and organization the query searched, whatever the SDK's settings.
    log_to_tracker(results, project, results_file=path, entity=entity, host=api.host)
    return f"{entity}/{project}"


def read_run_file(run: str, name: str) -> str | None:
    """
    Returns a file the tracker run `run`, as `entity/project/id`, uploaded, such as `results.json`, or `None` if it has no file of that name. Reads as the signed-in Flock user, on the Flock API that user signed in to.

    Raises:
        `ValueError`: if `run` isn't `entity/project/id`, you aren't signed in, or Flock refuses the read for another reason than a missing file (an unknown run, a project you can't read).
        `click.ClickException`: if Flock can't be reached.
    """
    parts = run.split("/")
    if len(parts) != 3 or not all(parts):
        raise ValueError(f"expected a run as entity/project/id, got {run!r}")

    entity, project, run_id = parts
    api, _ = _signed_in()
    with api.client:
        answer = api.get(f"projects/{entity}/{project}/runs/{run_id}/files/{quote(name)}")
    if answer.status_code == 200:
        return answer.text
    if answer.status_code == 404 and error_of(answer) == "file not found":
        return None
    raise ValueError(f"{api.host} refused {run}'s {name} ({error_of(answer)})")


def _signed_in(host: str | None = None) -> tuple[Api, str]:
    """The Flock API the user signed in to (at `host`, if given) and the user's default organization, or `ValueError` if there's no sign-in, Flock refuses it, or the user has no organization."""
    found = credentials.resolve(host)
    if found is None:
        raise ValueError("Not logged in: run flock login, or set FLOCK_TOKEN.")
    api = Api(found.host, found.token)
    with api.client:
        me = api.get("me")
    if me.status_code != 200:
        raise ValueError(f"{found.host} refused your credential ({error_of(me)})")
    entity = me.json().get("default_org")
    if entity is None:
        raise ValueError("You don't belong to any organization.")
    # A new client for the caller's requests: a closed one can't be reopened.
    return Api(found.host, found.token), entity
