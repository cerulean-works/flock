"""Logging results to the flock tracker through the `wandb` SDK."""

import dataclasses

import wandb

from flock.eval.results import Results


def log_to_tracker(results: Results, project: str) -> None:
    """
    Logs one `eval` run to `project`: the provenance, each task's hash (`task_hashes`) and each suite's members and hash (`suite_members`, `suite_hashes`) as its config, and each task's metrics and `docs_per_second` as its summary, named `<task>/<metric>`, with each scored suite's score as `suites/<suite>`.

    The SDK's own settings decide where it goes (`wandb login --host` for the flock tracker).
    """
    summary: dict[str, float] = {}
    for task in results.tasks:
        for name, value in task.metrics.items():
            summary[f"{task.task}/{name}"] = value
        if task.docs_per_second is not None:
            summary[f"{task.task}/docs_per_second"] = task.docs_per_second
    for suite in results.suites:
        if suite.score is not None:
            summary[f"suites/{suite.suite}"] = suite.score

    config = {
        **dataclasses.asdict(results.provenance),
        "task_hashes": {task.task: task.task_hash for task in results.tasks},
        # A suite's members grow as tasks are added, so a score is comparable only between runs
        # with the same members.
        "suite_members": {suite.suite: suite.members for suite in results.suites},
        # Two suite scores are comparable only when their hashes are equal.
        "suite_hashes": {suite.suite: suite.suite_hash for suite in results.suites},
    }

    # Leaving the block finishes the run, with exit code 1 if the update raised, so a failed log
    # isn't left running on the tracker.
    with wandb.init(project=project, job_type="eval", config=config) as run:
        run.summary.update(summary)
