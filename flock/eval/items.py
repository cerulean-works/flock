"""Read one run's docs, or two runs' docs side by side, with their text and what each model picked."""

import dataclasses
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from flock.eval.diff import check_instrument, outcomes
from flock.eval.results import WrittenResults, scale

# What [`items`] keeps: of one run's docs, by its outcome; of two runs', by the diff's cells.
ONE_RUN = ("wrong", "right", "all")
CELLS = ("only_a", "only_b", "both_wrong", "both_right")


@dataclass(frozen=True, slots=True)
class Answer:
    """
    One run's answer to a doc.

    Args:
        model (`str`):
            The run's `provenance.model`.
        pick (`int`, *optional*):
            The index of the choice the task's primary metric picks, or `None` for `acc_greedy`, which ranks no choices.
        correct (`bool`):
            Whether the doc counts as right under the primary metric.
        logprob (`list[float]`):
            Each choice's summed log-probability after its context.
        prob (`list[float]`):
            Each choice's probability renormalized over the choices: the softmax of `logprob`.
        share (`list[float]`, *optional*):
            The softmax over the choices of the score the primary metric ranks them by, `None` for `acc_greedy`. It is `prob` for `acc_raw`; for a length-normalized metric it is a share of normalized scores, not a probability.
    """

    model: str
    pick: int | None
    correct: bool
    logprob: list[float]
    prob: list[float]
    share: list[float] | None


@dataclass(frozen=True, slots=True)
class Item:
    """
    A doc with its text and each run's answer. Its `to_dict` form is one line of `flock eval items --json`.

    Args:
        task (`str`):
            The task's registry name.
        metric (`str`):
            The task's primary metric, which `pick`, `correct` and `share` follow.
        id (`str`):
            The doc's id.
        question (`str`, *optional*):
            The context the doc's choices share, as scored, or `None` if each choice has its own.
        contexts (`list[str]`, *optional*):
            Each choice's own context when `question` is `None`.
        choices (`list[str]`):
            Each choice's continuation exactly as scored, leading space included.
        gold (`int`):
            The index of the correct choice.
        runs (`list[Answer]`):
            Each run's answer, in the order the runs were given.
    """

    task: str
    metric: str
    id: str
    question: str | None
    contexts: list[str] | None
    choices: list[str]
    gold: int
    runs: list[Answer]

    def to_dict(self) -> dict[str, Any]:
        """Returns the item as plain JSON-ready data."""
        return dataclasses.asdict(self)


def items(
    runs: Sequence[WrittenResults],
    task: str,
    keep: str,
    limit: int | None = None,
    seed: int | None = None,
) -> list[Item]:
    """
    Returns a task's docs from one run or two, with their text and each run's answer, in doc order.

    Args:
        runs (`Sequence[WrittenResults]`):
            One run, or two. Two runs must be one instrument and have scored the task under one `task_hash`, as [`diff`] requires.
        task (`str`):
            The task's name. Its primary metric must be an accuracy.
        keep (`str`):
            For one run, its `wrong`, `right` or `all` docs. For two, a cell of their diff: `only_a`, `only_b`, `both_wrong` or `both_right`.
        limit (`int`, *optional*):
            Return at most this many: the first ones, or a sample with `seed`.
        seed (`int`, *optional*):
            Sample `limit` docs with this seed instead of taking the first ones.

    Raises:
        `ValueError`: if `keep` doesn't fit the number of runs, the runs are different instruments, a run hasn't scored the task, the task isn't an accuracy, its hashes differ, a run's file is older than schema 11 (it holds no text: score it again), or its predictions don't match its results or each other.
    """
    if len(runs) not in (1, 2):
        raise ValueError(f"items reads one run or two, not {len(runs)}")
    allowed = ONE_RUN if len(runs) == 1 else CELLS
    if keep not in allowed:
        raise ValueError(f"{len(runs)} run(s) keep one of {', '.join(allowed)}, not {keep!r}")
    if len(runs) == 2:
        check_instrument(*runs)

    results, rows_of, outcomes_of = [], [], []
    for run in runs:
        result = next((t for t in run.tasks if t.task == task), None)
        if result is None:
            raise ValueError(f"{run.source} has no task {task}")
        if scale(result.primary_metric) != "accuracy":
            raise ValueError(f"{task}'s primary metric {result.primary_metric} has no outcome")
        if run.schema_version < 11:
            raise ValueError(
                f"{run.source} is schema {run.schema_version}, whose predictions hold no text: "
                "score the model again to read its items"
            )
        rows = run.predictions(task)
        if rows is None:
            raise ValueError(f"{run.source} has no predictions for {task}")
        results.append(result)
        rows_of.append(rows)
        outcomes_of.append(outcomes(run, result, rows))
    # As `diff`: equal hashes mean the same requests, and rows follow them in order.
    if len(runs) == 2:
        if results[0].task_hash != results[1].task_hash:
            raise ValueError(
                f"{task} was scored differently in {runs[0].source} and {runs[1].source}"
            )
        if list(outcomes_of[0]) != list(outcomes_of[1]):
            raise ValueError(
                f"{task} has the same hash in {runs[0].source} and {runs[1].source} but other "
                "doc ids, or another order"
            )

    # Each doc's outcome per run picks it: one run by its own, two by the diff's cell.
    kept = []
    for i, right in enumerate(zip(*(found.values() for found in outcomes_of), strict=True)):
        if len(runs) == 1:
            cell = "all" if keep == "all" else "right" if right[0] else "wrong"
        elif right[0] == right[1]:
            cell = "both_right" if right[0] else "both_wrong"
        else:
            cell = "only_a" if right[0] else "only_b"
        if cell == keep:
            kept.append(i)
    if seed is not None:
        kept = sorted(random.Random(seed).sample(kept, min(limit or len(kept), len(kept))))
    elif limit is not None:
        kept = kept[:limit]

    metric = results[0].primary_metric
    found = []
    for i in kept:
        first = rows_of[0][i]
        # Equal hashes mean equal text, so text that differs means a mixed-up file.
        texts = [
            [rows[i].get(key) for key in ("question", "contexts", "choices")] for rows in rows_of
        ]
        choices = first.get("choices")
        if texts[0] != texts[-1] or not isinstance(choices, list):
            raise ValueError(f"{task}: doc {first['id']} has damaged or differing text")
        answers = []
        for run, rows in zip(runs, rows_of, strict=True):
            row = rows[i]
            try:
                logprob = row["logprob"]
                # The score the metric ranks choices by, higher is better, as the scorer's
                # `ranking_scores` has it; `acc_greedy` ranks none.
                ranked = None
                if metric == "acc_raw":
                    ranked = logprob
                elif metric == "acc_per_token":
                    ranked = [
                        lp / max(n, 1) for lp, n in zip(logprob, row["num_tokens"], strict=True)
                    ]
                elif metric == "acc_per_char":
                    ranked = [
                        lp / max(n, 1) for lp, n in zip(logprob, row["num_chars"], strict=True)
                    ]
                elif metric == "acc_per_byte":  # negated bits per byte
                    ranked = [
                        lp / max(n, 1) / math.log(2)
                        for lp, n in zip(logprob, row["num_bytes"], strict=True)
                    ]
                elif metric == "acc_uncond":
                    ranked = [lp - u for lp, u in zip(logprob, row["logprob_uncond"], strict=True)]
                # Softmax over the choices, shifted by the top score so no exp overflows.
                top = max(logprob)
                weights = [math.exp(score - top) for score in logprob]
                total = math.fsum(weights)
                prob = [weight / total for weight in weights]
                share = None
                if ranked is not None:
                    top = max(ranked)
                    weights = [math.exp(score - top) for score in ranked]
                    total = math.fsum(weights)
                    share = [weight / total for weight in weights]
                pick = row.get("pick")
                # The scorer wrote both, so a pick that doesn't explain `correct` is damage.
                consistent = pick is None or (pick == row["gold"]) == row["correct"]
                if len(logprob) != len(choices) or not consistent:
                    raise ValueError("its scores don't fit its choices")
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(
                    f"{run.source}: {task}: doc {row.get('id')} is damaged: {error!r}"
                ) from None
            answers.append(
                Answer(
                    model=run.provenance.model,
                    pick=pick,
                    correct=row["correct"],
                    logprob=logprob,
                    prob=prob,
                    share=share,
                )
            )
        found.append(
            Item(
                task=task,
                metric=metric,
                id=first["id"],
                question=first.get("question"),
                contexts=first.get("contexts"),
                choices=choices,
                gold=first["gold"],
                runs=answers,
            )
        )

    return found
