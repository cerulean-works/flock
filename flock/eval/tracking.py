"""Logging results to the flock tracker through the `wandb` SDK."""

import dataclasses

import wandb

from flock.eval.results import Results


def log_to_tracker(results: Results, project: str) -> None:
    """
    Logs one `eval` run to `project`: the provenance and each task's hash (`task_hashes`) as its config, and each task's metrics and `docs_per_second` as its summary, named `<task>/<metric>`, with each scored suite's score as `<suite>/score`.

    The SDK's own settings decide where it goes (`wandb login --host` for the flock tracker).
    """
    summary: dict[str, float] = {}
    for task in results.tasks:
        for name, value in task.metrics.items():
            summary[f"{task.task}/{name}"] = value
        summary[f"{task.task}/docs_per_second"] = task.docs_per_second
    for suite in results.suites:
        if suite.score is not None:
            summary[f"{suite.suite}/score"] = suite.score

    config = {
        **dataclasses.asdict(results.provenance),
        "task_hashes": {task.task: task.task_hash for task in results.tasks},
    }

    run = wandb.init(project=project, job_type="eval", config=config)
    run.summary.update(summary)
    run.finish()
