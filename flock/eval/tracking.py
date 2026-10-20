"""Logging results to the flock tracker through the `wandb` SDK."""

import dataclasses

import wandb

from flock.eval.results import Results


def log_to_tracker(results: Results, project: str) -> None:
    """
    Logs one `eval` run to `project`: the provenance as its config and each task's metrics as its summary, named `<task>/<metric>`.

    The SDK's own settings decide where it goes (`wandb login --host` for the flock tracker).

    TODO: stream rolling scores while a task runs. Requests are scored longest first, so a running mean over the docs scored so far is biased toward long questions. Streaming needs the docs scored in shuffled chunks, each sorted by length on its own.
    """
    summary = {
        f"{task.task}/{name}": value
        for task in results.tasks
        for name, value in {**task.metrics, "docs_per_second": task.docs_per_second}.items()
    }
    config = {
        **dataclasses.asdict(results.provenance),
        "task_hashes": {task.task: task.task_hash for task in results.tasks},
    }
    run = wandb.init(project=project, job_type="eval", config=config)
    run.summary.update(summary)
    run.finish()
