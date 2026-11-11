"""The `flock eval` command line."""

import shlex
import sys
from pathlib import Path
from typing import get_args

import click

from flock.eval import hub
from flock.eval.compare import Comparison, compare
from flock.eval.model import DEFAULT_BATCH_TOKENS
from flock.eval.results import Results
from flock.eval.run import Dtype, EvalConfig, evaluate
from flock.eval.tasks import SUITES, TASKS


@click.group()
def main() -> None:
    """Evaluate language models."""


@main.command()
@click.option("--model", required=True, help="Model as repo@revision or flock:org/name[@version].")
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
@click.option(
    "--add-bos",
    is_flag=True,
    help="Start every non-empty context with the tokenizer's BOS token, as OLMES does for Gemma.",
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
    add_bos: bool,
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
            add_bos=add_bos,
            device=device,
            wandb_project=wandb_project,
            # The program's name as invoked (`flock` for the script) and its arguments.
            command=shlex.join([Path(sys.argv[0]).name, *sys.argv[1:]]),
        )
    except ValueError as error:
        raise click.UsageError(str(error)) from None

    click.echo(format_results(evaluate(config), verbose=verbose))


@main.command(name="compare")
@click.argument("banked", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("new", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def compare_command(banked: Path, new: Path) -> None:
    """Compare a new results.json with a banked one. Exits 2 if the runs are different instruments or a task or suite in both was scored differently, else 1 if any score moved past its tolerance."""
    try:
        comparison = compare(banked, new)
    except ValueError as error:
        click.echo(f"refused: {error}", err=True)
        sys.exit(2)

    click.echo(format_comparison(comparison))
    # A changed task or suite still fails the check, as the refusal it replaces did (021.D1).
    sys.exit(2 if comparison.changed else 1 if comparison.breaches else 0)


@main.command(name="log")
@click.argument("results", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--project", required=True, help="The tracker project to log to.")
def log_command(results: Path, project: str) -> None:
    """Log a written results.json to the tracker as written, in your default organization. Refuses a file whose model and start time already have a run in the project."""
    # Imported here so the other commands don't import wandb.
    from wandb.errors import Error as WandbError

    from flock.eval.tracking import log_results_file

    try:
        logged = log_results_file(results, project)
    except (ValueError, WandbError) as error:
        raise click.ClickException(str(error)) from None

    click.echo(f"logged {results} to {logged}")


@main.group(name="hub")
def hub_group() -> None:
    """Copies of the evaluation datasets on the Flock hub."""


@hub_group.command(name="pull")
@click.argument("ref")
@click.option(
    "--to",
    "root",
    envvar=hub.ROOT,
    required=True,
    type=click.Path(file_okay=False, path_type=Path, resolve_path=True),
    help="The staging folder jobs read with FLOCK_EVAL_HUB set (default: $FLOCK_EVAL_HUB).",
)
def pull_command(ref: str, root: Path) -> None:
    """Stage one pinned dataset, ORG/OWNER/NAME@COMMIT, from the Flock hub, for jobs that can't reach the network. Prints the staged folder."""
    org, _, pinned = ref.partition("/")
    repo, _, revision = pinned.partition("@")
    if not org:
        raise click.UsageError(f"expected ORG/OWNER/NAME@COMMIT, got {ref!r}")

    from flock.hub import s3

    try:
        click.echo(hub.pull(root, org, repo, revision))
    # A repo or commit `pull` refuses; not signed in or refused; no such copy or tag, or a tag on
    # another revision; a staging folder that can't be written, or that others could write to.
    except (s3.S3Error, ValueError, OSError) as error:
        raise click.ClickException(str(error)) from None


def format_comparison(comparison: Comparison) -> str:
    """Formats the provenance fields that differ, the one-sided and the changed tasks and suites, one line per breach, then a count."""
    lines = []
    for key, (banked, new) in comparison.provenance.items():
        lines.append(f"{key}  {banked}  ->  {new}")
    if comparison.only_banked:
        lines.append(f"only in the bank: {', '.join(comparison.only_banked)}")
    if comparison.only_new:
        lines.append(f"only in the new run: {', '.join(comparison.only_new)}")
    if comparison.changed:
        names = ", ".join(f"{name} ({field})" for name, field in comparison.changed)
        lines.append(f"changed, not compared: {names}")

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
    """Formats a header with the model and commit, then per suite, each before its members, its score, metric and member count, then per task that no scored suite holds its primary metric and doc count. `verbose` prints instead per task a summary line and one line per metric, primary first, then one line per suite with its members."""
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

    # A task in a suite is in a scored one (an unscored suite holds only suites), and that
    # suite's line stands for it, so it prints only with `verbose`.
    nested = {name for suite in results.suites for name in suite.members}
    tasks = [task for task in results.tasks if task.task not in nested]

    # One name column for suites and tasks, so every score sits in one column.
    names = [suite.suite for suite in results.suites] + [task.task for task in tasks]
    width = max((len(name) for name in names), default=0)

    # Results hold each suite after its members. Printed the other way round, each suite before
    # its members in declared order, the suites that were asked for come first.
    by_name = {suite.suite: suite for suite in results.suites}
    todo = [suite for suite in results.suites if suite.suite not in nested]
    ordered = []
    while todo:
        suite = todo.pop(0)
        if suite not in ordered:  # a suite held by two others prints once
            ordered.append(suite)
            todo = [by_name[name] for name in suite.members if name in by_name] + todo

    for suite in ordered:
        score = "unscored" if suite.score is None else f"{suite.score:.4f}"
        metric = suite.metric or "primary"  # the members' primary metrics
        lines.append(f"{suite.suite:<{width}}  {score:<8}  {metric}  members {len(suite.members)}")

    for task in tasks:
        score = f"{task.metrics[task.primary_metric]:.4f}"
        lines.append(
            f"{task.task:<{width}}  {score:<8}  {task.primary_metric}  docs {task.num_docs}"
        )

    return "\n".join(lines)
