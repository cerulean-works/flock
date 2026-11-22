"""Scores a model on a Modal H100 for an eval launched from the web app (026, D190).

Deploy it from a clean checkout of the commit the app runs, with the app's Modal token:

    uv run modal deploy app/api/eval_worker.py

Inside the container each function runs this file again in the workspace's environment, as
``tests/modal_gpu.py`` runs pytest, so the scoring code is the deployed commit's.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

import modal

REPO = "/repo"
# The instrument's settings (D112, D121): BF16 weights at 16384 batch tokens. Not the launcher's
# to choose, so a launched number is scored as the bank's are.
DTYPE = "bfloat16"
BATCH_TOKENS = 16384

image = modal.Image.debian_slim(python_version="3.14")
# Built only where it's deployed from: in the container this file sits alone in /root.
if modal.is_local():
    from flock.eval.provenance import flock_commit

    root = Path(__file__).resolve().parents[2]
    # Only what git tracks, so an ignored file (app/.env, flock-files/) never reaches Modal, and
    # not the web app, which scoring doesn't need. Each tracked file's folders are kept too.
    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, text=True, check=True
    )
    tracked = set()
    for name in listed.stdout.split("\0"):
        if name and not name.startswith("app/web/"):
            tracked.update(str(parent) for parent in Path(name).parents)
            tracked.add(name)
    members = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["uv"]["workspace"]
    # Built as tests/modal_gpu.py builds its image: dependencies in their own layer, keyed on
    # the lockfile and the workspace's pyproject files, so a code-only change reuses it.
    image = (
        image.pip_install("uv==0.12.17")
        .env({"UV_PROJECT_ENVIRONMENT": "/venv", "UV_LINK_MODE": "copy"})
        .workdir(REPO)
        .add_local_file(root / "pyproject.toml", f"{REPO}/pyproject.toml", copy=True)
        .add_local_file(root / "uv.lock", f"{REPO}/uv.lock", copy=True)
    )
    for member in members["members"]:
        image = image.add_local_file(
            root / member / "pyproject.toml", f"{REPO}/{member}/pyproject.toml", copy=True
        )
    # The image has no .git, so the results record the commit deployed, "-dirty" if it had
    # uncommitted changes under flock/.
    image = (
        image.run_commands("uv sync --all-packages --frozen --no-install-workspace")
        .add_local_dir(root, REPO, copy=True, ignore=lambda path: str(path) not in tracked)
        .run_commands("uv sync --all-packages --frozen")
        .env({"FLOCK_COMMIT": flock_commit()})
    )

app = modal.App("flock-eval", image=image)


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


def in_workspace(*args: str) -> str:
    """Run this file with ``args`` in the workspace's environment and return what it printed.
    Raises ``RuntimeError`` with the end of its errors when it fails, for the launch's row."""
    command = ["uv", "run", "--no-sync", "python", f"{REPO}/app/api/eval_worker.py", *args]
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    sys.stderr.write(done.stderr)  # to the Modal logs
    if done.returncode:
        raise RuntimeError(done.stderr.strip()[-1000:] or f"exited with {done.returncode}")
    return done.stdout


@app.function(timeout=10 * 60)
def catalogue() -> dict[str, list[str]]:
    """The suites and tasks a launch may run, for the launch form."""
    return json.loads(in_workspace("catalogue"))


@app.function(gpu="H100")
def run_eval(model: str, suites: list[str], tasks: list[str], add_bos: bool, project: str) -> str:
    """Score one launch; returns the flock commit that scored it. The app sets the timeout from
    the launch's cap and the tracker's ``WANDB_*`` variables, its key among them, per call."""
    spec = json.dumps({"model": model, "suites": suites, "tasks": tasks, "add_bos": add_bos})
    sys.stdout.write(in_workspace("score", spec, project))
    return os.environ["FLOCK_COMMIT"]


if __name__ == "__main__":
    if sys.argv[1] == "catalogue":
        print(json.dumps(public_names()))
    else:
        spec = json.loads(sys.argv[2])
        score(spec["model"], spec["suites"], spec["tasks"], spec["add_bos"], sys.argv[3])
