"""Compare a new results file with a banked one: refuse different instruments, list every score that moved past its tolerance."""

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from flock.eval.metrics import scale

# Another GPU type, kernel or library version reorders float32 sums, so a rerun on another
# stack moves scores slightly (013.D1). An accuracy may move by this many docs flipping, any
# other metric, such as `bpb`, by this fraction of its banked value. Both are argued, not
# measured: tighten them once a rerun on another stack shows the real drift.
FLIPPED_DOCS = 2
RELATIVE_TOLERANCE = 1e-4


class Breach(NamedTuple):
    """One value that moved past its tolerance between the banked run and the new one, such as `("arc_easy", "metrics.acc_raw", 0.71, 0.70)`."""

    name: str
    field: str
    banked: Any
    new: Any


@dataclass(frozen=True, slots=True)
class Comparison:
    """
    What [`compare`] found.

    Args:
        tasks (`list[str]`), suites (`list[str]`):
            The tasks and suites in both files, which were compared, in the banked file's order.
        breaches (`list[Breach]`):
            Every compared value that moved past its tolerance. Empty when the new run reproduces the bank.
        provenance (`dict[str, tuple[Any, Any]]`):
            The provenance fields and `schema_version` that may cause a breach and differ, each as `(banked, new)`.
        only_banked (`list[str]`), only_new (`list[str]`):
            Tasks and suites in one file only. They are listed, not compared.
    """

    tasks: list[str]
    suites: list[str]
    breaches: list[Breach]
    provenance: dict[str, tuple[Any, Any]]
    only_banked: list[str]
    only_new: list[str]


def compare(banked: Path, new: Path) -> Comparison:
    """
    Compares two `results.json` files task by task and suite by suite, requiring every score to stay within its tolerance.

    Each task in both files is compared on `num_docs` and every entry of `metrics` and `truncation`, and each suite in both on `members` and `score`. Timing is not compared. An accuracy may move by `FLIPPED_DOCS` docs out of `num_docs`, any other metric by `RELATIVE_TOLERANCE` of its banked value, and a suite's score by its members' tolerances combined as independent errors. Counts and `members` must be equal.

    Raises:
        `ValueError`: if the runs are different instruments (another model or `model_digest`, `dtype`, `batch_tokens`, `max_length` or `attention`, a common task with another `task_hash` or `limit`, or a common suite with another `metric`), if no task is in both, or if a file isn't JSON or lacks a key `compare` reads, or has the wrong type there.
    """
    files = {}
    for path in (banked, new):
        try:
            files[path] = json.loads(path.read_text())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"{path} is not a results file: {error}") from None
    bank, rerun = files[banked], files[new]

    try:
        # Settings that change scores by design: a difference is another instrument, not a
        # breach (013.D2).
        for key in ("model", "dtype", "batch_tokens", "max_length", "attention"):
            if bank["provenance"][key] != rerun["provenance"][key]:
                raise ValueError(
                    f"different instrument: provenance.{key} is {bank['provenance'][key]!r} "
                    f"in {banked}, {rerun['provenance'][key]!r} in {new}"
                )
        # Two Flock servers can each have a `flock:acme/olmo2-1b@12`, so the digest refuses
        # too. Read with `get`: Hugging Face models and files older than the field have none.
        digests = bank["provenance"].get("model_digest"), rerun["provenance"].get("model_digest")
        if digests[0] != digests[1]:
            raise ValueError(
                f"different instrument: provenance.model_digest is {digests[0]!r} in {banked}, "
                f"{digests[1]!r} in {new}"
            )
        # The usual causes of a breach, printed beside it. Read with `get`: a file without one
        # of them is still comparable.
        provenance = {}
        for key in (
            "flock_commit",
            "device",
            "torch",
            "transformers",
            "tokenizers",
            "datasets",
            "python",
        ):
            if bank["provenance"].get(key) != rerun["provenance"].get(key):
                provenance[key] = (bank["provenance"].get(key), rerun["provenance"].get(key))
        # A new layout can add a metric or a count, which shows as breaches beside it.
        if bank.get("schema_version") != rerun.get("schema_version"):
            provenance["schema_version"] = (bank.get("schema_version"), rerun.get("schema_version"))

        bank_tasks = {task["task"]: task for task in bank["tasks"]}
        new_tasks = {task["task"]: task for task in rerun["tasks"]}
        bank_suites = {suite["suite"]: suite for suite in bank["suites"]}
        new_suites = {suite["suite"]: suite for suite in rerun["suites"]}
        tasks = [name for name in bank_tasks if name in new_tasks]
        suites = [name for name in bank_suites if name in new_suites]
        if not tasks:
            raise ValueError(f"no task is in both {banked} and {new}")

        # Every refusal comes before any score is read, so a refused pair prints no breach. A
        # suite's `metric` says which metric its score averages, so it refuses like a hash.
        for name in tasks:
            for key in ("task_hash", "limit"):
                if bank_tasks[name][key] != new_tasks[name][key]:
                    raise ValueError(
                        f"different instrument: {name} has {key} {bank_tasks[name][key]!r} "
                        f"in {banked}, {new_tasks[name][key]!r} in {new}"
                    )
        for name in suites:
            if bank_suites[name]["metric"] != new_suites[name]["metric"]:
                raise ValueError(
                    f"different instrument: {name} has metric {bank_suites[name]['metric']!r} "
                    f"in {banked}, {new_suites[name]['metric']!r} in {new}"
                )

        # Every compared value as (name, field, banked, new, tolerance). A value missing on one
        # side is None, so a metric added or dropped is a breach too.
        values = []
        for name in tasks:
            a, b = bank_tasks[name], new_tasks[name]
            values.append((name, "num_docs", a["num_docs"], b["num_docs"], 0))
            for group in ("metrics", "truncation"):
                for key in sorted(a[group].keys() | b[group].keys()):
                    x, y = a[group].get(key), b[group].get(key)
                    tolerance = _tolerance(key, x, a["num_docs"]) if group == "metrics" else 0
                    values.append((name, f"{group}.{key}", x, y, tolerance))
        for name in suites:
            a, b = bank_suites[name], new_suites[name]
            values.append((name, "members", a["members"], b["members"], 0))
            tolerance = (
                _suite_tolerance(name, bank_tasks, bank_suites) if a["score"] is not None else 0
            )
            values.append((name, "score", a["score"], b["score"], tolerance))
    # A missing key, or a value of the wrong type such as a list where an object belongs.
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError(f"{banked} or {new} is not a results file: {error!r}") from None

    breaches = []
    for name, field, x, y, tolerance in values:
        if _number(x) and _number(y):
            # NaN never equals itself, but a NaN that repeats is reproduced, not a breach. Only
            # finite values move within a tolerance: an infinity must repeat exactly.
            reproduced = x == y or (math.isnan(x) and math.isnan(y))
            within = math.isfinite(x) and math.isfinite(y) and abs(x - y) <= tolerance
            moved = not (reproduced or within)
        else:
            moved = x != y
        if moved:
            breaches.append(Breach(name, field, x, y))

    only_banked = [name for name in bank_tasks if name not in new_tasks]
    only_banked += [name for name in bank_suites if name not in new_suites]
    only_new = [name for name in new_tasks if name not in bank_tasks]
    only_new += [name for name in new_suites if name not in bank_suites]
    return Comparison(tasks, suites, breaches, provenance, only_banked, only_new)


def _number(value: Any) -> bool:
    """Returns whether `value` is a number, as read from JSON: a bool or None is not."""
    return type(value) in (int, float)


def _tolerance(metric: str, banked: Any, num_docs: int) -> float:
    """Returns how far a task's `metric` may move from its `banked` value (013.D1)."""
    if scale(metric) == "accuracy":
        # An accuracy moves in steps of one doc, so half a step of slack keeps rounding in the
        # difference from turning the last allowed flip into a breach. With no docs there is
        # no accuracy to move.
        return (FLIPPED_DOCS + 0.5) / num_docs if num_docs else 0
    return RELATIVE_TOLERANCE * abs(banked) if _number(banked) else 0


def _suite_tolerance(name: str, tasks: dict[str, Any], suites: dict[str, Any]) -> float:
    """Returns a scored suite's tolerance: its score is the mean of its members' scores, so their tolerances combine as independent errors, the root of their sum of squares over their count."""
    suite = suites[name]
    tolerances = []
    for member in suite["members"]:
        if member in tasks:
            # A member task's score is the suite's metric, or the task's primary metric.
            metric = suite["metric"] or tasks[member]["primary_metric"]
            tolerances.append(
                _tolerance(metric, tasks[member]["metrics"][metric], tasks[member]["num_docs"])
            )
        else:
            tolerances.append(_suite_tolerance(member, tasks, suites))
    return math.sqrt(sum(tolerance**2 for tolerance in tolerances)) / len(tolerances)
