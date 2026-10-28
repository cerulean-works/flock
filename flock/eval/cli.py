"""The `flock-eval` command line."""

import shlex
import sys
from pathlib import Path

import click

from flock.eval.model import DEFAULT_BATCH_TOKENS
from flock.eval.results import Results
from flock.eval.run import EvalConfig, evaluate
from flock.eval.tasks import SUITES, TASKS


@click.group()
def main() -> None:
    """Evaluate language models."""


@main.command()
@click.option("--model", required=True, help="Model as repo@revision.")
@click.option(
    "--task",
    "tasks",
    multiple=True,
    type=click.Choice(sorted(TASKS)),
    help="Task to run; repeat for several.",
)
@click.option(
    "--suite",
    "suites",
    multiple=True,
    type=click.Choice(sorted(SUITES)),
    help="Suite to run and score; repeat for several. Each task runs once.",
)
@click.option(
    "--out",
    type=click.Path(path_type=Path),
    required=True,
    help="New or empty directory for results.json and predictions/.",
)
@click.option(
    "--limit",
    type=click.IntRange(min=1),
    help="Score only each task's first N docs.",
)
@click.option(
    "--batch-tokens",
    type=click.IntRange(min=1),
    default=DEFAULT_BATCH_TOKENS,
    show_default=True,
    help="Padded tokens per forward pass.",
)
@click.option("--device", help="Torch device (default: cuda if available).")
@click.option("--wandb-project", help="Also log the summary to this tracker project.")
def run(
    model: str,
    tasks: tuple[str, ...],
    suites: tuple[str, ...],
    out: Path,
    limit: int | None,
    batch_tokens: int,
    device: str | None,
    wandb_project: str | None,
) -> None:
    """Score a model on tasks and suites and write results.json."""
    try:
        config = EvalConfig(
            model=model,
            tasks=tasks,
            suites=suites,
            out=out,
            limit=limit,
            batch_tokens=batch_tokens,
            device=device,
            wandb_project=wandb_project,
            command=shlex.join(["flock-eval", *sys.argv[1:]]),
        )
    except ValueError as error:
        raise click.UsageError(str(error)) from None

    click.echo(format_results(evaluate(config)))


def format_results(results: Results) -> str:
    """Formats a header with the model and commit, then per task a summary line and one line per metric, primary first, then one line per suite."""
    lines = [f"model {results.provenance.model}  flock {results.provenance.flock_commit}"]

    for task in results.tasks:
        speed = "" if task.docs_per_second is None else f"{task.docs_per_second:.1f} docs/s  "
        lines.append(f"{task.task}  docs {task.num_docs}  {speed}hash {task.task_hash[:12]}")
        names = [task.primary_metric] + [
            name for name in task.metrics if name != task.primary_metric
        ]
        width = max(len(name) for name in names) + 1
        lines += [f"  {name:<{width}} {task.metrics[name]:.4f}" for name in names]

    for suite in results.suites:
        score = "unscored" if suite.score is None else f"{suite.score:.4f}"
        lines.append(f"{suite.suite}  {score}  ({', '.join(suite.members)})")

    return "\n".join(lines)
