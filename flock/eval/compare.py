"""Compare a new results file with a banked one: refuse different instruments, list every score that moved."""

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple


class Breach(NamedTuple):
    """One value that differs between the banked run and the new one, such as `("arc_easy", "metrics.acc_raw", 0.71, 0.70)`."""

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
            Every compared value that differs. Empty when the new run reproduces the bank.
        provenance (`dict[str, tuple[Any, Any]]`):
            The provenance fields that may cause a breach and differ, each as `(banked, new)`.
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
    Compares two `results.json` files task by task and suite by suite, requiring every score to be equal.

    Each task in both files is compared on `num_docs` and every entry of `metrics` and `truncation`, and each suite in both on `members` and `score`. Timing is not compared.

    Raises:
        `ValueError`: if the runs are different instruments (another model, `dtype`, `batch_tokens`, `max_length` or `attention`, a common task with another `task_hash` or `limit`, or a common suite with another `metric`), if no task is in both, or if a file isn't JSON or lacks a key `compare` reads.
    """
    files = {}
    for path in (banked, new):
        try:
            files[path] = json.loads(path.read_text())
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
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
        # The usual causes of a breach, printed beside it.
        provenance = {}
        for key in ("flock_commit", "device", "torch", "transformers", "python"):
            if bank["provenance"][key] != rerun["provenance"][key]:
                provenance[key] = (bank["provenance"][key], rerun["provenance"][key])

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

        # Every compared value as (name, field, banked, new). A value missing on one side is
        # None, so a metric added or dropped is a breach too.
        values = []
        for name in tasks:
            a, b = bank_tasks[name], new_tasks[name]
            values.append((name, "num_docs", a["num_docs"], b["num_docs"]))
            for group in ("metrics", "truncation"):
                for key in sorted(a[group].keys() | b[group].keys()):
                    values.append((name, f"{group}.{key}", a[group].get(key), b[group].get(key)))
        for name in suites:
            for key in ("members", "score"):
                values.append((name, key, bank_suites[name][key], new_suites[name][key]))
    except KeyError as error:
        raise ValueError(f"{banked} or {new} has no key {error}") from None

    breaches = []
    for name, field, x, y in values:
        # NaN never equals itself, but a NaN that repeats is reproduced, not a breach.
        both_nan = isinstance(x, float) and isinstance(y, float) and math.isnan(x) and math.isnan(y)
        if x != y and not both_nan:
            breaches.append(Breach(name, field, x, y))

    only_banked = [name for name in bank_tasks if name not in new_tasks]
    only_banked += [name for name in bank_suites if name not in new_suites]
    only_new = [name for name in new_tasks if name not in bank_tasks]
    only_new += [name for name in new_suites if name not in bank_suites]
    return Comparison(tasks, suites, breaches, provenance, only_banked, only_new)
