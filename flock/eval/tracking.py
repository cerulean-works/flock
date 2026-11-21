"""Logging results and their files to the flock tracker through the `wandb` SDK, and reading the files back."""

import json
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import quote

import wandb

from flock.cli import credentials
from flock.cli.api import Api, error_of
from flock.eval.results import predictions_name, stays_inside


def log_to_tracker(
    results: dict[str, Any],
    project: str,
    *,
    results_file: Path | None = None,
    entity: str | None = None,
    host: str | None = None,
) -> None:
    """
    Logs one `eval` run to `project`: the provenance, each task's hash (`task_hashes`) and each suite's members and hash (`suite_members`, `suite_hashes`) as its config, and each task's metrics and `docs_per_second` as its summary, named `<task>/<metric>`, with each scored suite's score as `suites/<suite>`. With `results_file`, the run also gets that file as `results.json` and, for schema 9, each task's `predictions/<task>.jsonl` found beside it, under `private/predictions/<task>.jsonl` for a task on a private dataset.

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
        `ValueError`: if predictions of a task on a private dataset would go to a project that is public, or whose visibility can't be read (not signed in to Flock, or Flock refuses the query), or a schema 9 `results_file` isn't named `results.json`, before the run is created.
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

    files = _run_files(results, results_file)
    private = [name for name in files if name.startswith("private/")]
    # Only a backfill, which names its host, goes online whatever `WANDB_MODE` says; a live run
    # keeps the SDK's mode even when the check below names its host.
    backfill = host is not None
    if private:
        # The run goes where the visibility was read.
        entity, host = _private_destination(private, project, entity, host)

    # Leaving the block finishes the run, with exit code 1 if the update raised, so a failed log
    # isn't left running on the tracker.
    # A backfill is online even where `WANDB_MODE` says offline, so it never reports a run it
    # didn't log. Never resumed, live or backfilled, so a `WANDB_RUN_ID` left in the environment
    # can't overwrite another run or its files.
    # Always a new run, even inside a training script's active run, which `wandb.init` would
    # otherwise return (`reinit`'s default outside notebooks), with its own project.
    settings = None
    if host is not None:
        settings = (
            wandb.Settings(base_url=host, mode="online")
            if backfill
            else wandb.Settings(base_url=host)
        )
    with wandb.init(
        entity=entity,
        project=project,
        job_type="eval",
        config=config,
        settings=settings,
        resume="never",
        reinit="create_new",
    ) as run:
        # `WANDB_MODE=disabled` gives a run that logs nothing, so nothing is staged for it either.
        if run.disabled:
            return
        # A sweep or launch agent can replace the entity and project `wandb.init` was given, so
        # private predictions go only to the project whose visibility was read.
        # An offline run is synced later, and `wandb sync` can send it to any project, past the
        # check; its private predictions wait for an online `flock eval log`.
        if private and run.offline:
            raise ValueError(
                "an offline run can be synced to any project: private predictions are uploaded "
                "only online; log the folder later with flock eval log"
            )
        if private and (run.entity, run.project) != (entity, project):
            raise ValueError(
                f"the run went to {run.entity}/{run.project}, not the checked {entity}/{project}: "
                "private predictions are not uploaded"
            )
        run.summary.update(summary)
        # Copied under their names on the run, so a banked copy named otherwise still arrives as
        # results.json. `now`: each starts uploading at once, and leaving the block waits for it.
        for name, path in files.items():
            copy = Path(run.dir) / name
            copy.parent.mkdir(parents=True, exist_ok=True)
            # Owner-only from the start: the staging folder may be readable by others where the
            # predictions' own folder isn't. `copyfile` keeps an existing file's mode.
            copy.touch(mode=0o600)
            shutil.copyfile(path, copy)
            # Literally, not as a glob: a task name may hold `[`.
            run.save(str(copy), base_path=run.dir, policy="now", glob=False)


def _run_files(results: dict[str, Any], results_file: Path | None) -> dict[str, Path]:
    """The files the tracker shows a run's docs from, by their names on the run: `results_file` as `results.json` and, for schema 9, each task's predictions file found beside it, under `private/` when its dataset is private. An older file's predictions have no outcome (`correct`) and its tasks no privacy mark, so only its results go."""
    files = {}
    if results_file is None:
        return files
    files["results.json"] = results_file
    if results["schema_version"] < 9:
        return files

    # The predictions are the ones beside `results.json`, so two renamed copies of results in
    # one folder can't both claim them, and a link can't pair one folder's tasks with another's.
    if results_file.name != "results.json" or results_file.is_symlink():
        raise ValueError(
            f"{results_file} isn't a file named results.json, so the predictions beside it may "
            "belong to another run: log it from its own folder as results.json"
        )
    # Two tasks reading one file (a repeated name, or names a case-insensitive disk takes as
    # one) with different privacy marks would send it under both names.
    seen = set()
    for task in results["tasks"]:
        name = predictions_name(task["task"])
        path = results_file.parent / name
        # A link could send any local file under a task's name.
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError(f"{path} is a link; upload the predictions themselves")
        if not path.is_file():
            continue
        identity = (path.stat().st_dev, path.stat().st_ino)
        if identity in seen:
            raise ValueError(f"{path} is the predictions file of two tasks")
        seen.add(identity)
        # A damaged mark must not pass as public.
        marked = task["dataset"].get("private")
        if not isinstance(marked, bool):
            raise ValueError(  # noqa: TRY004 - a damaged file, refused as a value
                f"{task['task']}: dataset.private is {marked!r}, not true or false"
            )
        # The tracker lists and serves a run's `private/` files to the organization's members
        # only, whatever the project's visibility.
        files[f"private/{name}" if marked else name] = path
    return files


def _private_destination(
    private: list[str], project: str, entity: str | None, host: str | None
) -> tuple[str, str]:
    """
    The organization and host a run with the `private` predictions files goes to: `entity` (or the signed-in user's default organization) on the signed-in Flock API, once `project` there is known to be private.

    A run in a project has the project's visibility, so a private dataset's per-doc rows go only to a project known to be private, checked before the run exists. A project the run creates is private (the tracker's default). Their `private/` names keep them members-only if the project is made public later, on a tracker that enforces it.
    """
    api, default_org = _signed_in(host)
    entity = entity or default_org
    with api.client:
        answer = api.get(f"projects/{entity}/{project}")
    if answer.status_code == 200:
        visibility = answer.json()["visibility"]
    elif answer.status_code == 404 and error_of(answer) == f"project {entity}/{project} not found":
        visibility = "new"
    else:
        raise ValueError(f"{api.host} refused the project query ({error_of(answer)})")
    if visibility not in ("private", "new"):
        raise ValueError(
            f"{entity}/{project} is {visibility}, and {', '.join(private)} hold predictions "
            "on a private dataset: log to a private project"
        )
    return entity, api.host


def log_results_file(path: Path, project: str) -> str:
    """
    Logs a written `results.json` to `project` as written, in the default organization of the signed-in Flock user, on the Flock API that user signed in to, and returns `entity/project`. The file goes on the run, with the predictions beside it for schema 9, as [`log_to_tracker`] uploads them.

    A field the file lacks stays absent from the run: an older file's missing `sdpa_backend` means an unknown kernel, not none. The tracker stamps the run with this machine's host, time and commit; the scoring job's are the config's `created_at` and `flock_commit`.

    Raises:
        `ValueError`: if the file's schema isn't 6 to 9, Flock refuses the sign-in or the query, the project already has a run with the file's `model` and `created_at` that didn't fail or crash, the project is public and the predictions of a task on a private dataset would go to it, or the file is schema 9 and isn't named `results.json`.
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


def read_run_file(
    run: str, name: str, credential: credentials.Credentials | None = None
) -> str | None:
    """
    Returns a file the tracker run `run`, as `entity/project/id`, uploaded, such as `results.json`, or `None` if it has no file of that name. Reads with `credential`, on its server, or as the signed-in Flock user if not given.

    Raises:
        `ValueError`: if `run` isn't `entity/project/id`, you aren't signed in, or Flock refuses the read for another reason than a missing file (an unknown run, a project you can't read).
        `click.ClickException`: if Flock can't be reached.
    """
    # A client normalizes `..` in a URL, which would leave the run.
    if len(run.split("/")) != 3 or not stays_inside(run):
        raise ValueError(f"expected a run as entity/project/id, got {run!r}")
    if not stays_inside(name):
        raise ValueError(f"{name!r} isn't a file name inside {run}")

    entity, project, run_id = run.split("/")
    # No organization lookup: the run names its own, and a read is one request per file.
    found = credential or _credential()
    api = Api(found.host, found.token)
    with api.client:
        answer = api.get(f"projects/{entity}/{project}/runs/{run_id}/files/{quote(name)}")
    if answer.status_code == 200:
        return answer.text
    if answer.status_code == 404 and error_of(answer) == "file not found":
        return None
    raise ValueError(f"{api.host} refused {run}'s {name} ({error_of(answer)})")


def _signed_in(host: str | None = None) -> tuple[Api, str]:
    """The Flock API the user signed in to (at `host`, if given) and the user's default organization, or `ValueError` if there's no sign-in, Flock refuses it, or the user has no organization."""
    found = _credential(host)
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


def _credential(host: str | None = None) -> credentials.Credentials:
    """The Flock credential `flock login` saved or `FLOCK_TOKEN` sets (for `host`, if given), or `ValueError` if there's none."""
    found = credentials.resolve(host)
    if found is None:
        raise ValueError("Not logged in: run flock login, or set FLOCK_TOKEN.")
    return found
