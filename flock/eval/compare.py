"""Compare a new results file with a banked one: refuse different instruments, list the tasks and suites scored differently, and list every other score that moved past its tolerance."""

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
            The tasks and suites in both files that were compared, in the banked file's order.
        changed (`list[tuple[str, str]]`):
            The tasks and suites in both files that were scored differently, so they were listed and not compared, each with the first field that differs: a task's `task_hash` or `limit`, a suite's `metric` or `suite_hash`, or `members` for a suite holding a changed task or suite at any depth. Tasks first, then suites, in the banked file's order.
        breaches (`list[Breach]`):
            Every compared value that moved past its tolerance. Empty when the new run reproduces the bank.
        provenance (`dict[str, tuple[Any, Any]]`):
            The provenance fields and `schema_version` that may cause a breach and differ, each as `(banked, new)`.
        only_banked (`list[str]`), only_new (`list[str]`):
            Tasks and suites in one file only. They are listed, not compared.
    """

    tasks: list[str]
    suites: list[str]
    changed: list[tuple[str, str]]
    breaches: list[Breach]
    provenance: dict[str, tuple[Any, Any]]
    only_banked: list[str]
    only_new: list[str]


def compare(banked: Path, new: Path) -> Comparison:
    """
    Compares two `results.json` files task by task and suite by suite, requiring every score to stay within its tolerance.

    Each task in both files is compared on `num_docs` and every entry of `metrics` and `truncation`, and each suite in both on `members` and `score`. Timing is not compared. An accuracy may move by `FLIPPED_DOCS` docs out of `num_docs`, any other metric by `RELATIVE_TOLERANCE` of its banked value, and a suite's score by its members' tolerances combined as independent errors. Counts and `members` must be equal. A task or suite scored differently in the two runs is listed in `changed` and not compared.

    Raises:
        `ValueError`: if the runs are different instruments (another model or `model_digest`, `dtype`, `batch_tokens`, `max_length` or `attention`), if no task is in both, or if a file isn't JSON or lacks a key `compare` reads, or has the wrong type there.
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
        # The pinned SDPA kernel changes BF16 scores (023.D3). A file older than the field has an
        # unknown kernel, not none, so only two files that both record it can refuse: the FP32
        # bank, where the pin changes no score, stays comparable.
        if "sdpa_backend" in bank["provenance"] and "sdpa_backend" in rerun["provenance"]:
            backends = bank["provenance"]["sdpa_backend"], rerun["provenance"]["sdpa_backend"]
            if backends[0] != backends[1]:
                raise ValueError(
                    f"different instrument: provenance.sdpa_backend is {backends[0]!r} in "
                    f"{banked}, {backends[1]!r} in {new}"
                )
        # The usual causes of a breach, printed beside it. Read with `get`: a file without one
        # of them is still comparable.
        provenance = {}
        for key in (
            "flock_commit",
            "device",
            "sdpa_backend",
            "torch",
            "transformers",
            "tokenizers",
            "datasets",
            "python",
        ):
            if bank["provenance"].get(key) != rerun["provenance"].get(key):
                provenance[key] = (bank["provenance"].get(key), rerun["provenance"].get(key))
        # A new layout can add a metric or a count, which shows as breaches beside it.
        if bank["schema_version"] != rerun["schema_version"]:
            provenance["schema_version"] = (bank["schema_version"], rerun["schema_version"])

        bank_tasks = {task["task"]: task for task in bank["tasks"]}
        new_tasks = {task["task"]: task for task in rerun["tasks"]}
        bank_suites = {suite["suite"]: suite for suite in bank["suites"]}
        new_suites = {suite["suite"]: suite for suite in rerun["suites"]}
        if not any(name in new_tasks for name in bank_tasks):
            raise ValueError(f"no task is in both {banked} and {new}")

        # A task or suite scored differently is listed with the first field that differs and
        # skipped, so every other score still compares (021.D1). A suite's `metric` says which
        # metric its score averages, and its hash covers its members' hashes. A file older than
        # schema 5 has no suite hash, so its suites change through a changed member, as a hash
        # would.
        hashed = bank["schema_version"] >= 5 and rerun["schema_version"] >= 5
        changed_tasks, changed_suites = {}, {}
        for name, task in bank_tasks.items():
            if name in new_tasks:
                for key in ("task_hash", "limit"):
                    if task[key] != new_tasks[name][key]:
                        changed_tasks.setdefault(name, key)
        for name, suite in bank_suites.items():
            if name in new_suites:
                for key in ("metric", "suite_hash") if hashed else ("metric",):
                    if suite[key] != new_suites[name][key]:
                        changed_suites.setdefault(name, key)
        # A suite holding a changed task or suite changes too, at any depth, and is listed with
        # `members` rather than `suite_hash`, since its hash covers its members'. A member of
        # either file counts, and it is a task if the bank has a task of that name, as
        # `_suite_tolerance` reads it. A suite may hold, in the new file only, a suite after it in
        # the bank, so this repeats until no suite is added.
        added = True
        while added:
            added = False
            for name, suite in bank_suites.items():
                if name not in new_suites or changed_suites.get(name) in ("metric", "members"):
                    continue
                for member in suite["members"] + new_suites[name]["members"]:
                    if member in (changed_tasks if member in bank_tasks else changed_suites):
                        changed_suites[name] = "members"
                        added = True
                        break
        tasks = [name for name in bank_tasks if name in new_tasks and name not in changed_tasks]
        suites = [name for name in bank_suites if name in new_suites and name not in changed_suites]

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
    # Suites in the bank's order, though the loop above may add one in a later pass.
    changed = list(changed_tasks.items())
    changed += [(name, changed_suites[name]) for name in bank_suites if name in changed_suites]
    return Comparison(tasks, suites, changed, breaches, provenance, only_banked, only_new)


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
