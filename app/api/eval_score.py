"""Scoring inside the eval worker's container, in the workspace's environment (026, D190): lists
what a launch may run, or scores one. ``app/api/eval_worker.py`` runs it there:

    python app/api/eval_score.py catalogue
    python app/api/eval_score.py score '<model, suites, tasks, add_bos as JSON>' <project>
"""

import json
import shlex
import sys
import tempfile
from pathlib import Path

# The instrument's settings (D112, D121): BF16 weights at 16384 batch tokens. Not the launcher's
# to choose, so a launched number is scored as the bank's are.
DTYPE = "bfloat16"
BATCH_TOKENS = 16384


def public_names() -> dict[str, list[str]]:
    """The suites and tasks whose every dataset is public, by registry name (D189)."""
    from flock.eval.tasks import SUITES, TASKS

    suites = [
        name for name, suite in SUITES.items() if not any(t.dataset.private for t in suite.tasks)
    ]
    return {
        "suites": suites,
        "tasks": [name for name, task in TASKS.items() if not task.dataset.private],
    }


def score(model: str, suites: list[str], tasks: list[str], add_bos: bool, project: str) -> None:
    """Score ``model`` (``repo@commit``) and log the run to ``project``, as the environment's
    ``WANDB_*`` variables say. Refuses a suite or task that isn't public before any model loads."""
    from flock.eval.run import EvalConfig, evaluate

    public = public_names()
    refused = [n for n in suites if n not in public["suites"]]
    refused += [n for n in tasks if n not in public["tasks"]]
    if refused:
        sys.exit(f"not public, or not a suite or task of this flock: {', '.join(refused)}")
    # The command that scores the same way from a checkout, recorded in the provenance.
    command = ["flock", "eval", "run", "--model", model]
    for name in suites:
        command += ["--suite", name]
    for name in tasks:
        command += ["--task", name]
    command += ["--dtype", DTYPE, "--batch-tokens", str(BATCH_TOKENS), "--wandb-project", project]
    if add_bos:
        command.append("--add-bos")
    evaluate(
        EvalConfig(
            model=model,
            suites=tuple(suites),
            tasks=tuple(tasks),
            # A fresh folder: a warm container keeps the previous call's files.
            out=Path(tempfile.mkdtemp()) / "eval",
            dtype=DTYPE,
            batch_tokens=BATCH_TOKENS,
            add_bos=add_bos,
            wandb_project=project,
            command=shlex.join(command),
        )
    )


if __name__ == "__main__":
    if sys.argv[1] == "catalogue":
        print(json.dumps(public_names()))
    else:
        spec = json.loads(sys.argv[2])
        score(spec["model"], spec["suites"], spec["tasks"], spec["add_bos"], sys.argv[3])
