"""The `flock eval` command line."""

import json
import shlex
import sys
import unicodedata
from pathlib import Path
from typing import get_args

import click

from flock.eval import hub
from flock.eval.compare import Comparison, compare
from flock.eval.diff import ItemDiff, diff
from flock.eval.items import CELLS, ONE_RUN, Item, items
from flock.eval.model import DEFAULT_BATCH_TOKENS
from flock.eval.results import Results, read_results
from flock.eval.run import Dtype, EvalConfig, evaluate
from flock.eval.tasks import SUITES, TASKS


@click.group()
def main() -> None:
    """Evaluate language models."""


@main.command()
@click.option(
    "--model",
    required=True,
    help="Model as repo@revision, flock:org/path[@version] or flock:project:path[@version].",
)
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
@click.option(
    "--no-tokens",
    is_flag=True,
    help="Don't write each task's per-token values beside its predictions.",
)
@click.option(
    "--context-tokens",
    is_flag=True,
    help="Also write the per-token values of each doc's context, as Min-K%++ reads them.",
)
@click.option("--device", help="Torch device (default: cuda if available).")
@click.option(
    "--wandb-project",
    help="Also log the summary to this tracker project, with results.json and predictions/.",
)
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
    no_tokens: bool,
    context_tokens: bool,
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
            tokens=not no_tokens,
            context_tokens=context_tokens,
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


@main.command(name="diff")
@click.argument("a")
@click.argument("b")
@click.option("--json", "as_json", is_flag=True, help="Print the diff as JSON.")
def diff_command(a: str, b: str, as_json: bool) -> None:
    """Compare two runs doc by doc, each a folder holding results.json or a tracker run as run:ENTITY/PROJECT/ID, on every accuracy task both scored under the same task hash. Exits 2 if the runs can't be compared."""
    try:
        found = diff(read_results(a), read_results(b))
    # A missing or damaged results file, another instrument, or a refused or unreachable run.
    except (OSError, ValueError, click.ClickException) as error:
        message = error.format_message() if isinstance(error, click.ClickException) else error
        click.echo(f"refused: {message}", err=True)
        sys.exit(2)

    click.echo(json.dumps(found.to_dict(), indent=2) if as_json else format_diff(found))


@main.command(name="items")
@click.argument("runs", nargs=-1, required=True)
@click.option("--task", required=True, help="The task whose docs to print.")
@click.option(
    "--only",
    type=click.Choice(ONE_RUN),
    help="One run: print its wrong (the default), right or all docs.",
)
@click.option("--cell", type=click.Choice(CELLS), help="Two runs: print this cell of their diff.")
@click.option("--limit", type=click.IntRange(min=1), help="Print at most this many docs.")
@click.option("--seed", type=int, help="Sample --limit docs with this seed, not the first ones.")
@click.option("--json", "as_json", is_flag=True, help="Print one JSON object per doc.")
def items_command(
    runs: tuple[str, ...],
    task: str,
    only: str | None,
    cell: str | None,
    limit: int | None,
    seed: int | None,
    as_json: bool,
) -> None:
    """Print a task's docs with their question, choices and each run's pick and probabilities, from one run or two, each a folder holding results.json or a tracker run as run:ENTITY/PROJECT/ID. Two runs are compared as diff compares them. Exits 2 if the runs can't be read this way."""
    if len(runs) > 2:
        raise click.UsageError("give one run or two")
    if len(runs) == 1 and cell is not None:
        raise click.UsageError("--cell compares two runs; one run takes --only")
    if len(runs) == 2 and only is not None:
        raise click.UsageError("--only reads one run; two runs take --cell")
    if len(runs) == 2 and cell is None:
        raise click.UsageError(f"two runs need --cell, one of {', '.join(CELLS)}")

    try:
        found = items(
            [read_results(run) for run in runs], task, cell or only or "wrong", limit, seed
        )
    # A missing or damaged file, another instrument, no text, or a refused or unreachable run.
    except (OSError, ValueError, click.ClickException) as error:
        message = error.format_message() if isinstance(error, click.ClickException) else error
        # The message may quote a run's files.
        click.echo(f"refused: {_escaped(str(message))}", err=True)
        sys.exit(2)

    if as_json:
        for item in found:
            click.echo(json.dumps(item.to_dict(), ensure_ascii=False))
    else:
        click.echo("\n\n".join(format_item(item) for item in found))


@main.command(name="log")
@click.argument("results", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--project", required=True, help="The tracker project to log to.")
def log_command(results: Path, project: str) -> None:
    """Log a written results.json to the tracker as written, in your default organization, with the predictions beside it from schema 9 on. Refuses a file whose model and start time already have a run in the project, and a private dataset's predictions for a public project."""
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


def format_diff(found: ItemDiff) -> str:
    """Formats the two models, one line per compared task with its four cell counts, then the tasks not compared with why."""
    lines = [f"a  {found.a}", f"b  {found.b}"]

    width = max([len("task")] + [len(task.task) for task in found.tasks])
    lines.append(f"{'task':<{width}}  docs  both right  both wrong  only a  only b")
    for task in found.tasks:
        lines.append(
            f"{task.task:<{width}}  {task.num_docs:>4}  {task.both_right:>10}  "
            f"{task.both_wrong:>10}  {len(task.only_a):>6}  {len(task.only_b):>6}"
        )

    if found.not_compared:
        names = ", ".join(f"{name} ({reason})" for name, reason in found.not_compared)
        lines.append(f"not compared: {names}")
    return "\n".join(lines)


def format_item(item: Item) -> str:
    """Formats a doc: its task, id and metric, where it comes from when the run says, each run's model and verdict, its question, then one line per choice with each run's probability and share, `*` on gold and `<` on each run's pick."""
    letters = "ab"[: len(item.runs)]
    lines = [f"{_escaped(item.task)}  {_escaped(item.id)}  {_escaped(item.metric)}"]
    # The hashes and `meta` are for joining and filtering, and print with `--json`.
    shown = [key for key in ("split", "source_index", "native_id", "subject") if key in item.source]
    if shown:
        lines.append("  ".join(f"{key} {_escaped(str(item.source[key]))}" for key in shown))
    for letter, answer in zip(letters, item.runs, strict=True):
        verdict = "right" if answer.correct else "wrong"
        lines.append(f"{letter}  {_escaped(answer.model)}  {verdict}, picks {answer.pick}")
    # Each choice's own context stands in for a shared question.
    if item.question is not None:
        lines.append(_escaped(item.question, keep="\n"))

    # Per run, the probability then the share; a share is `-` where the metric ranks no choices.
    header = "".join(f"  p {letter}    s {letter}  " for letter in letters)
    lines.append(f"     {header}")
    for i, choice in enumerate(item.choices):
        cells = ""
        for answer in item.runs:
            share = "    -" if answer.share is None else f"{answer.share[i]:.3f}"
            mark = "<" if answer.pick == i else " "
            cells += f"  {answer.prob[i]:.3f}  {share} {mark}"
        text = choice if item.contexts is None else item.contexts[i] + choice
        gold = "*" if i == item.gold else " "
        # One line per choice, so its own line breaks are escaped too.
        lines.append(f"{gold}{i:>3} {cells}  {_escaped(text.strip())}")
    return "\n".join(lines)


def _escaped(text: str, keep: str = "") -> str:
    """`text` with each control, format or line-separator character (`Cc`, `Cf`, `Zl`, `Zp`) but those in `keep` written as its escape, so text from a run's files can't move the cursor or reorder a line. Chinese text, its ideographic space included, stays as it is."""
    hidden = ("Cc", "Cf", "Zl", "Zp")
    return "".join(
        repr(c)[1:-1] if c not in keep and unicodedata.category(c) in hidden else c for c in text
    )


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
