"""The `flock-eval` command line."""

import argparse
import shlex
import sys
from collections.abc import Sequence
from pathlib import Path

from flock.eval.model import DEFAULT_BATCH_TOKENS
from flock.eval.results import Results
from flock.eval.run import EvalConfig, evaluate
from flock.eval.tasks import TASKS


def positive_int(value: str) -> int:
    """An argparse type for integers of at least 1."""
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {number}")
    return number


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parses `flock-eval` arguments."""
    parser = argparse.ArgumentParser(prog="flock-eval", description="Evaluate language models.")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="score a model on tasks and write results.json")
    run.add_argument("--model", required=True, help="model as repo@revision")
    run.add_argument(
        "--task",
        dest="tasks",
        action="append",
        required=True,
        choices=sorted(TASKS),
        help="task to run; repeat for several",
    )
    run.add_argument("--out", type=Path, required=True, help="directory for results.json")
    run.add_argument("--limit", type=positive_int, help="score only each task's first N docs")
    run.add_argument(
        "--batch-tokens",
        type=positive_int,
        default=DEFAULT_BATCH_TOKENS,
        help=f"padded tokens per forward pass (default {DEFAULT_BATCH_TOKENS})",
    )
    run.add_argument("--device", help="torch device (default: cuda if available)")
    run.add_argument("--wandb-project", help="also log the summary to this tracker project")
    return parser.parse_args(argv)


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


def main(argv: Sequence[str] | None = None) -> None:
    """Runs `flock-eval`."""
    args = parse_args(argv)
    config = EvalConfig(
        model=args.model,
        tasks=tuple(args.tasks),
        out=args.out,
        limit=args.limit,
        batch_tokens=args.batch_tokens,
        device=args.device,
        wandb_project=args.wandb_project,
        command=shlex.join(["flock-eval", *(sys.argv[1:] if argv is None else argv)]),
    )
    print(format_results(evaluate(config)))
