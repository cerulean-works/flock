"""The `flock-eval` command line."""

import shlex
import sys
from pathlib import Path

import click

from flock.eval.model import DEFAULT_BATCH_TOKENS
from flock.eval.results import Results
from flock.eval.run import EvalConfig, evaluate
from flock.eval.tasks import TASKS


@click.group(name="eval")
def main() -> None:
    """Evaluate language models."""


@main.command()
@click.option("--model", required=True, help="Model as repo@revision.")
@click.option(
    "--task",
    "tasks",
    multiple=True,
    required=True,
    type=click.Choice(sorted(TASKS)),
    help="Task to run; repeat for several.",
)
@click.option(
    "--out",
    type=click.Path(path_type=Path),
    required=True,
    help="Directory for results.json.",
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
    out: Path,
    limit: int | None,
    batch_tokens: int,
    device: str | None,
    wandb_project: str | None,
) -> None:
    """Score a model on tasks and write results.json."""
    config = EvalConfig(
        model=model,
        tasks=tasks,
        out=out,
        limit=limit,
        batch_tokens=batch_tokens,
        device=device,
        wandb_project=wandb_project,
        command=shlex.join(["flock-eval", *sys.argv[1:]]),
    )

    click.echo(format_results(evaluate(config)))


def format_results(results: Results) -> str:
    """One line per task and metric, primary metric first."""
    lines = [f"model {results.provenance.model}  flock {results.provenance.flock_commit}"]

    for task in results.tasks:
        lines.append(
            f"{task.task}  docs {task.num_docs}  {task.docs_per_second:.1f} docs/s  "
            f"hash {task.task_hash[:12]}"
        )
        names = [task.primary_metric] + [m for m in task.metrics if m != task.primary_metric]
        lines += [f"  {name:<14} {task.metrics[name]:.4f}" for name in names]

    return "\n".join(lines)
