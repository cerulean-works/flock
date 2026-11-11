"""Logging results to the flock tracker through the `wandb` SDK."""

import json
from pathlib import Path
from typing import Any

import wandb

from flock.cli import credentials
from flock.cli.api import Api, error_of


def log_to_tracker(
    results: dict[str, Any],
    project: str,
    *,
    entity: str | None = None,
    host: str | None = None,
) -> None:
    """
    Logs one `eval` run to `project`: the provenance, each task's hash (`task_hashes`) and each suite's members and hash (`suite_members`, `suite_hashes`) as its config, and each task's metrics and `docs_per_second` as its summary, named `<task>/<metric>`, with each scored suite's score as `suites/<suite>`.

    The SDK's own settings decide where it goes (`wandb login --host` for the flock tracker), unless `entity` or `host` name it.

    Args:
        results (`dict[str, Any]`):
            The results' JSON form: [`~eval.results.Results.to_dict`] for a live run, or a written `results.json` as read. Only the provenance fields it holds are logged.
        project (`str`):
            The tracker project.
        entity (`str`, *optional*):
            The organization to log to, or `None` for the SDK's default.
        host (`str`, *optional*):
            The tracker's URL, or `None` for the SDK's settings.
    """
    summary: dict[str, Any] = {}
    for task in results["tasks"]:
        for name, value in task["metrics"].items():
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

    # Leaving the block finishes the run, with exit code 1 if the update raised, so a failed log
    # isn't left running on the tracker.
    settings = wandb.Settings(base_url=host) if host is not None else None
    with wandb.init(
        entity=entity, project=project, job_type="eval", config=config, settings=settings
    ) as run:
        run.summary.update(summary)


def log_results_file(path: Path, project: str) -> str:
    """
    Logs a written `results.json` to `project` as written, in the default organization of the signed-in Flock user, on the Flock API that user signed in to, and returns `entity/project`.

    A field the file lacks stays absent from the run: an older file's missing `sdpa_backend` means an unknown kernel, not none. The tracker stamps the run with this machine's host, time and commit; the scoring job's are the config's `created_at` and `flock_commit`.

    Raises:
        `ValueError`: if the file's schema isn't 6 to 8, Flock refuses the sign-in or the query, or the project already has a run with the file's `model` and `created_at`.
    """
    results = json.loads(path.read_text())
    # Schemas 6 to 8 hold every field the run is built from. Older files lack suite hashes, and
    # some lack suites.
    if results.get("schema_version") not in (6, 7, 8):
        raise ValueError(f"{path} has schema {results.get('schema_version')}; only 6 to 8 log")
    provenance = results["provenance"]

    found = credentials.resolve()
    if found is None:
        raise ValueError("Not logged in: run flock login, or set FLOCK_TOKEN.")
    api = Api(found.host, found.token)
    me = api.get("me")
    if me.status_code != 200:
        raise ValueError(f"{found.host} refused your credential ({error_of(me)})")
    entity = me.json().get("default_org")
    if entity is None:
        raise ValueError("You don't belong to any organization.")

    # A run already logged, live or by an earlier backfill, has the same model and start time.
    # `task_hashes` isn't matched: the tracker keeps it as one column per task.
    query = {
        "filter": {
            "$and": [
                {"config.model": {"$eq": provenance["model"]}},
                {"config.created_at": {"$eq": provenance["created_at"]}},
            ]
        },
        "columns": [],
        "limit": 1,
    }
    answer = api.post(f"projects/{entity}/{project}/runs/query", query)
    # A 404 is a project that doesn't exist yet, so it holds no run; the first run creates it.
    if answer.status_code not in (200, 404):
        raise ValueError(f"{found.host} refused the run query ({error_of(answer)})")
    runs = answer.json()["ids"] if answer.status_code == 200 else []
    if runs:
        raise ValueError(f"{path} is already logged as {entity}/{project}/{runs[0]}")

    # The run goes to the API and organization the query searched, whatever the SDK's settings.
    log_to_tracker(results, project, entity=entity, host=found.host)
    return f"{entity}/{project}"
