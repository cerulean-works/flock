"""Compare two runs doc by doc: which docs each model gets right on the tasks both scored the same way."""

import dataclasses
import math
from dataclasses import dataclass, field
from typing import Any

from flock.eval.metrics import scale
from flock.eval.results import WrittenResults


@dataclass(frozen=True, slots=True)
class TaskDiff:
    """
    One task's docs, split by which of two runs got each right.

    Args:
        task (`str`):
            The task's registry name.
        task_hash (`str`):
            The hash both runs scored it under.
        metric (`str`):
            The task's primary metric, the accuracy each row's `correct` records.
        num_docs (`int`):
            How many docs both runs scored.
        both_right (`int`), both_wrong (`int`):
            How many docs both runs got right, and both got wrong.
        only_a (`list[str]`), only_b (`list[str]`):
            The ids of the docs only run A got right, and only run B, in A's doc order.
    """

    task: str
    task_hash: str
    metric: str
    num_docs: int
    both_right: int
    both_wrong: int
    only_a: list[str]
    only_b: list[str]


@dataclass(frozen=True, slots=True)
class ItemDiff:
    """
    What [`diff`] found. Its `to_dict` form is the JSON `flock eval diff --json` prints.

    `schema_version`, 1, is the version of that JSON's layout, bumped when a field changes.

    Args:
        a (`str`), b (`str`):
            Each run's `provenance.model`.
        tasks (`list[TaskDiff]`):
            The compared tasks, in A's run order.
        not_compared (`list[tuple[str, str]]`):
            Every other task of either run, with why: `only_a` or `only_b` (scored by one run), `task_hash` (scored differently), `not_accuracy` (a primary metric such as `bpb`, with no per-doc outcome), `no_predictions` (a run has no predictions file for it) or `no_outcome` (a run's file is older than schema 9, so its rows have no `correct`). A's tasks first, in its order, then B's.
    """

    a: str
    b: str
    tasks: list[TaskDiff]
    not_compared: list[tuple[str, str]]
    schema_version: int = field(default=1, init=False)

    def to_dict(self) -> dict[str, Any]:
        """Returns the diff as plain JSON-ready data."""
        return dataclasses.asdict(self)


def diff(a: WrittenResults, b: WrittenResults) -> ItemDiff:
    """
    Compares two runs doc by doc on every task both scored under the same `task_hash` with an accuracy as its primary metric, from each doc's `correct` in the predictions. The runs may score different models; that is the point.

    Raises:
        `ValueError`: if the runs are different instruments (another `dtype`, `batch_tokens` or `attention`, or another `sdpa_backend`), or a compared task's predictions don't match its results: another row count than its `num_docs`, a row without a string `id` or a boolean `correct`, another set of doc ids, a repeated id, or a count of right docs that isn't its finite accuracy over its docs.
    """
    # Settings of the instrument, not of the model: two models scored under different ones
    # aren't compared (principle 3, as `compare` refuses them). `max_length` and `add_bos` belong
    # to the model (OLMES adds BOS for Gemma only).
    for key in ("dtype", "batch_tokens", "attention", "sdpa_backend"):
        settings = getattr(a.provenance, key), getattr(b.provenance, key)
        if settings[0] != settings[1]:
            raise ValueError(
                f"different instrument: provenance.{key} is {settings[0]!r} in {a.source}, "
                f"{settings[1]!r} in {b.source}"
            )

    b_tasks = {task.task: task for task in b.tasks}
    a_names = {task.task for task in a.tasks}
    tasks, not_compared = [], []
    for task in a.tasks:
        other = b_tasks.get(task.task)
        if other is None:
            not_compared.append((task.task, "only_a"))
            continue
        if task.task_hash != other.task_hash:
            not_compared.append((task.task, "task_hash"))
            continue
        # Equal hashes mean an equal primary metric for every task but a perplexity task, whose
        # hash leaves the metric out; it has no per-doc outcome either way.
        if scale(task.primary_metric) != "accuracy" or task.primary_metric != other.primary_metric:
            not_compared.append((task.task, "not_accuracy"))
            continue
        # Rows older than schema 9 have no outcome, so they aren't read. A schema 9 accuracy row
        # without one is damaged, and the checks below refuse it.
        if a.schema_version < 9 or b.schema_version < 9:
            not_compared.append((task.task, "no_outcome"))
            continue
        # B's file is read only when A has one: a run's files may be downloads.
        rows_a = a.predictions(task.task)
        rows_b = b.predictions(task.task) if rows_a is not None else None
        if rows_a is None or rows_b is None:
            not_compared.append((task.task, "no_predictions"))
            continue

        # Each run's outcome by doc id. Equal hashes mean the same docs, so any other set of ids,
        # or rows that disagree with the task's own accuracy, means a damaged or mixed-up file.
        outcomes = []
        for run, result, rows in ((a, task, rows_a), (b, other, rows_b)):
            if len(rows) != result.num_docs:
                raise ValueError(
                    f"{run.source}: {task.task} has {len(rows)} prediction rows for "
                    f"{result.num_docs} docs"
                )
            by_id = {}
            for row in rows:
                if not (
                    isinstance(row, dict)
                    and isinstance(row.get("id"), str)
                    and isinstance(row.get("correct"), bool)
                ):
                    # A damaged file, refused as every other mismatch is.
                    raise ValueError(  # noqa: TRY004
                        f"{run.source}: {task.task} has a malformed row: {row!r}"
                    )
                by_id[row["id"]] = row["correct"]
            if len(by_id) != len(rows):
                raise ValueError(f"{run.source}: {task.task}'s predictions repeat a doc id")
            right = sum(by_id.values())
            accuracy = result.metrics.get(task.primary_metric, math.nan)
            # Half a doc of slack for the FP32 mean the accuracy was written as. A missing or
            # non-finite accuracy can't vouch for the rows.
            if not math.isfinite(accuracy) or abs(right - accuracy * result.num_docs) > 0.5:
                raise ValueError(
                    f"{run.source}: {task.task}'s predictions have {right} of {len(rows)} right, "
                    f"but its {task.primary_metric} is {accuracy} "
                    f"over {result.num_docs} docs"
                )
            outcomes.append(by_id)
        if outcomes[0].keys() != outcomes[1].keys():
            raise ValueError(
                f"{task.task} has the same hash in {a.source} and {b.source} but other doc ids"
            )

        cells = {(True, True): 0, (False, False): 0}
        only_a, only_b = [], []
        for doc, right_a in outcomes[0].items():
            right_b = outcomes[1][doc]
            if right_a == right_b:
                cells[right_a, right_b] += 1
            elif right_a:
                only_a.append(doc)
            else:
                only_b.append(doc)
        tasks.append(
            TaskDiff(
                task=task.task,
                task_hash=task.task_hash,
                metric=task.primary_metric,
                num_docs=len(outcomes[0]),
                both_right=cells[True, True],
                both_wrong=cells[False, False],
                only_a=only_a,
                only_b=only_b,
            )
        )
    not_compared += [(task.task, "only_b") for task in b.tasks if task.task not in a_names]

    return ItemDiff(a.provenance.model, b.provenance.model, tasks, not_compared)
