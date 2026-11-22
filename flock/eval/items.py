"""Read one run's docs, or two runs' docs side by side, with their text and what each model picked."""

import dataclasses
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from flock.eval.diff import check_instrument, outcomes
from flock.eval.results import WrittenResults

# What [`items`] keeps: of one run's docs, by its outcome; of two runs', by the diff's cells.
ONE_RUN = ("wrong", "right", "all")
CELLS = ("only_a", "only_b", "both_wrong", "both_right")
# The accuracies that rank choices, each by its score in `metrics.ranking_scores`.
RANKED = ("acc_raw", "acc_per_token", "acc_per_char", "acc_per_byte", "acc_uncond")
# The fields a row has from schema 12 on that say where its doc comes from.
SOURCE_FIELDS = (
    "split",
    "source_index",
    "native_id",
    "subject",
    "meta",
    "doc_hash",
    "prompt_hash",
)


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
            The softmax over the choices of the score the primary metric ranks them by, `None` for `acc_greedy`. It is `prob` for `acc_raw`; for a length-normalized metric it is a share of normalized scores, not a probability. It is computed in double precision from the rows, so where the scorer's FP32 scores tie or nearly tie, its largest value can sit on another choice than `pick`.
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
        source (`dict[str, Any]`, *optional*):
            Where the doc comes from, as the first run's row has it from schema 12 on: `split`, `source_index`, `doc_hash` and `prompt_hash`, and `native_id`, `subject` and `meta` where the task gives them. Empty for an older run.
    """

    task: str
    metric: str
    id: str
    question: str | None
    contexts: list[str] | None
    choices: list[str]
    gold: int
    runs: list[Answer]
    source: dict[str, Any] = dataclasses.field(default_factory=dict)

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
        `ValueError`: if `keep` doesn't fit the number of runs, `limit` is below 1, the runs are different instruments, a run hasn't scored the task, the task isn't an accuracy, its hashes or primary metrics differ, a run's file is older than schema 11 (it holds no text: score it again), or its predictions don't match its results or each other (another text or gold, a pick that doesn't explain `correct`, scores that aren't finite).
    """
    if len(runs) not in (1, 2):
        raise ValueError(f"items reads one run or two, not {len(runs)}")
    allowed = ONE_RUN if len(runs) == 1 else CELLS
    if keep not in allowed:
        raise ValueError(f"{len(runs)} run(s) keep one of {', '.join(allowed)}, not {keep!r}")
    if limit is not None and limit < 1:
        raise ValueError(f"limit must be at least 1, not {limit}")
    if len(runs) == 2:
        check_instrument(*runs)

    results, rows_of, outcomes_of = [], [], []
    for run in runs:
        result = next((t for t in run.tasks if t.task == task), None)
        if result is None:
            raise ValueError(f"{run.source} has no task {task}")
        # The accuracies this reader knows how each ranks choices, `acc_greedy` ranking none.
        if result.primary_metric not in RANKED + ("acc_greedy",):
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
        # A perplexity task's hash leaves the metric out, and a damaged file could disagree.
        if results[0].primary_metric != results[1].primary_metric:
            raise ValueError(
                f"{task} is read by {results[0].primary_metric} in {runs[0].source} but by "
                f"{results[1].primary_metric} in {runs[1].source}"
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
        size = len(kept) if limit is None else min(limit, len(kept))
        kept = sorted(random.Random(seed).sample(kept, size))
    elif limit is not None:
        kept = kept[:limit]

    # Every doc is checked, whichever are kept, so a mixed-up file is refused in any view; only
    # the kept ones are returned.
    metric = results[0].primary_metric
    kept_set = set(kept)
    found = []
    for i in range(len(rows_of[0])):
        # Equal hashes mean equal text, gold and examples shown, so a difference means a
        # mixed-up file.
        shared = [
            [rows[i].get(key) for key in ("question", "contexts", "choices", "gold", "shots")]
            for rows in rows_of
        ]
        question, contexts, choices, gold, _ = shared[0]
        doc = f"{task}: doc {rows_of[0][i]['id']}"
        if shared[0] != shared[-1]:
            raise ValueError(
                f"{doc} has other text or gold in {runs[0].source} and {runs[-1].source}"
            )
        damaged = ValueError(f"{doc} has damaged text in {runs[0].source}")
        if not (isinstance(choices, list) and choices and all(isinstance(c, str) for c in choices)):
            raise damaged
        # A question the choices share, or one context per choice.
        per_choice = (
            question is None
            and isinstance(contexts, list)
            and len(contexts) == len(choices)
            and all(isinstance(context, str) for context in contexts)
        )
        if not (isinstance(question, str) and contexts is None) and not per_choice:
            raise damaged
        if type(gold) is not int or not 0 <= gold < len(choices):
            raise damaged

        answers = []
        for run, rows in zip(runs, rows_of, strict=True):
            row = rows[i]
            try:
                logprob = row["logprob"]
                # One number per choice, as the scorer writes them: a bool is an int to Python
                # but no score, and a length is a whole count.
                scores = [logprob] + ([row["logprob_uncond"]] if metric == "acc_uncond" else [])
                lengths = [row[key] for key in ("num_tokens", "num_chars", "num_bytes")]
                for values in scores + lengths:
                    if not isinstance(values, list) or len(values) != len(choices):
                        raise ValueError("a per-choice field doesn't have one value per choice")
                if not (
                    all(type(value) in (int, float) for values in scores for value in values)
                    and all(type(n) is int and n >= 0 for counts in lengths for n in counts)
                ):
                    raise ValueError("a score or length isn't a number")
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

                # A metric that ranks choices has the scorer's pick, which explains `correct`;
                # `acc_greedy` has none.
                pick = row.get("pick")
                if ranked is None:
                    fits = pick is None
                else:
                    fits = type(pick) is int and 0 <= pick < len(choices)
                    fits = fits and (pick == gold) == row["correct"]
                # Raw log-probabilities need no arithmetic, so their pick is checked exactly.
                if metric == "acc_raw":
                    fits = fits and pick == logprob.index(max(logprob))
                # JSON has no NaN or infinity, and a softmax of them means nothing.
                values = logprob + (ranked or []) + prob + (share or [])
                finite = all(math.isfinite(value) for value in values)
                if len(logprob) != len(choices) or not fits or not finite:
                    raise ValueError("its scores or pick don't fit its choices")
            except (KeyError, TypeError, ValueError, OverflowError) as error:
                raise ValueError(f"{run.source}: {doc} is damaged: {error!r}") from None
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
        if i in kept_set:
            row = rows_of[0][i]
            found.append(
                Item(
                    task=task,
                    metric=metric,
                    id=row["id"],
                    question=question,
                    contexts=contexts,
                    choices=choices,
                    gold=gold,
                    runs=answers,
                    source={key: row[key] for key in SOURCE_FIELDS if key in row},
                )
            )

    return found
