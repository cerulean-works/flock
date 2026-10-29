"""The `flock-eval` command line."""

import shlex
import sys
from pathlib import Path
from typing import get_args

import click

from flock.eval.compare import Comparison, compare
from flock.eval.model import DEFAULT_BATCH_TOKENS
from flock.eval.results import Results
from flock.eval.run import Dtype, EvalConfig, evaluate
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
@click.option(
    "--dtype",
    type=click.Choice(get_args(Dtype)),
    default="bfloat16",
    show_default=True,
    help="The weights' dtype.",
)
@click.option("--device", help="Torch device (default: cuda if available).")
@click.option("--wandb-project", help="Also log the summary to this tracker project.")
@click.option(
    "--verbose",
    is_flag=True,
    help="Print every metric of every task and each suite's members.",
)
def run(
    model: str,
    tasks: tuple[str, ...],
    suites: tuple[str, ...],
    out: Path,
    limit: int | None,
    batch_tokens: int,
    dtype: Dtype,
    device: str | None,
    wandb_project: str | None,
    verbose: bool,
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
            dtype=dtype,
            device=device,
            wandb_project=wandb_project,
            command=shlex.join(["flock-eval", *sys.argv[1:]]),
        )
    except ValueError as error:
        raise click.UsageError(str(error)) from None

    click.echo(format_results(evaluate(config), verbose=verbose))


@main.command(name="compare")
@click.argument("banked", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("new", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def compare_command(banked: Path, new: Path) -> None:
    """Compare a new results.json with a banked one. Exits 1 if any score moved past its tolerance, 2 if the runs are different instruments."""
    try:
        comparison = compare(banked, new)
    except ValueError as error:
        click.echo(f"refused: {error}", err=True)
        sys.exit(2)

    click.echo(format_comparison(comparison))
    sys.exit(1 if comparison.breaches else 0)


def format_comparison(comparison: Comparison) -> str:
    """Formats the provenance fields that differ, the one-sided tasks and suites, one line per breach, then a count."""
    lines = []
    for key, (banked, new) in comparison.provenance.items():
        lines.append(f"{key}  {banked}  ->  {new}")
    if comparison.only_banked:
        lines.append(f"only in the bank: {', '.join(comparison.only_banked)}")
    if comparison.only_new:
        lines.append(f"only in the new run: {', '.join(comparison.only_new)}")

    for breach in comparison.breaches:
        line = f"{breach.name}  {breach.field}  {breach.banked}  ->  {breach.new}"
        # A difference is printed for two numbers only: a missing value is None, members a list.
        numbers = all(type(value) in (int, float) for value in (breach.banked, breach.new))
        lines.append(f"{line}  ({breach.new - breach.banked:+.3g})" if numbers else line)

    lines.append(
        f"compared: tasks {len(comparison.tasks)}, suites {len(comparison.suites)}, "
        f"breaches {len(comparison.breaches)}"
    )
    return "\n".join(lines)


def format_results(results: Results, verbose: bool = False) -> str:
    """Formats a header with the model and commit, then per suite, outermost first, its score and member count, then per task its primary metric and doc count. `verbose` prints instead per task a summary line and one line per metric, primary first, then one line per suite with its members."""
    lines = [f"model {results.provenance.model}  flock {results.provenance.flock_commit}"]

    if verbose:
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

    # One name column for suites and tasks, so every score sits in one column.
    names = [suite.suite for suite in results.suites] + [task.task for task in results.tasks]
    width = max(len(name) for name in names)

    # Results hold each suite after its members, so reversed the suite that was asked for is first.
    for suite in reversed(results.suites):
        score = "unscored" if suite.score is None else f"{suite.score:.4f}"
        lines.append(f"{suite.suite:<{width}}  {score:<8}  members {len(suite.members)}")

    for task in results.tasks:
        score = f"{task.metrics[task.primary_metric]:.4f}"
        lines.append(
            f"{task.task:<{width}}  {score:<8}  {task.primary_metric}  docs {task.num_docs}"
        )

    return "\n".join(lines)
