"""Compare a new results file with a banked one: refuse different instruments, list every score that moved."""

import json
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
        `ValueError`: if the runs are different instruments (another model, `dtype`, `batch_tokens`, `max_length` or `attention`, or a common task with another `task_hash` or `limit`), if no task is in both, or if a key `compare` reads is missing.
    """
    old, now = json.loads(banked.read_text()), json.loads(new.read_text())

    try:
        # Settings that change scores by design: a difference is another instrument, not a
        # breach (013.D2).
        for key in ("model", "dtype", "batch_tokens", "max_length", "attention"):
            if old["provenance"][key] != now["provenance"][key]:
                raise ValueError(
                    f"different instrument: provenance.{key} is {old['provenance'][key]!r} "
                    f"in {banked}, {now['provenance'][key]!r} in {new}"
                )
        # The usual causes of a breach, printed beside it.
        provenance = {}
        for key in ("flock_commit", "device", "torch", "transformers", "python"):
            if old["provenance"][key] != now["provenance"][key]:
                provenance[key] = (old["provenance"][key], now["provenance"][key])

        old_tasks = {task["task"]: task for task in old["tasks"]}
        new_tasks = {task["task"]: task for task in now["tasks"]}
        old_suites = {suite["suite"]: suite for suite in old["suites"]}
        new_suites = {suite["suite"]: suite for suite in now["suites"]}
        tasks = [name for name in old_tasks if name in new_tasks]
        suites = [name for name in old_suites if name in new_suites]
        if not tasks:
            raise ValueError(f"no task is in both {banked} and {new}")

        # Every refusal comes before any score is read, so a refused pair prints no breach.
        for name in tasks:
            for key in ("task_hash", "limit"):
                if old_tasks[name][key] != new_tasks[name][key]:
                    raise ValueError(
                        f"different instrument: {name} has {key} {old_tasks[name][key]!r} "
                        f"in {banked}, {new_tasks[name][key]!r} in {new}"
                    )

        # A value missing on one side is None, so a metric added or dropped is a breach too.
        breaches = []
        for name in tasks:
            a, b = old_tasks[name], new_tasks[name]
            values = [("num_docs", a["num_docs"], b["num_docs"])]
            for group in ("metrics", "truncation"):
                for key in sorted(a[group].keys() | b[group].keys()):
                    values.append((f"{group}.{key}", a[group].get(key), b[group].get(key)))
            breaches += [Breach(name, field, x, y) for field, x, y in values if x != y]
        for name in suites:
            for key in ("members", "score"):
                if old_suites[name][key] != new_suites[name][key]:
                    breaches.append(Breach(name, key, old_suites[name][key], new_suites[name][key]))
    except KeyError as error:
        raise ValueError(f"{banked} or {new} has no key {error}") from None

    compared = {*tasks, *suites}
    only_banked = [name for name in [*old_tasks, *old_suites] if name not in compared]
    only_new = [name for name in [*new_tasks, *new_suites] if name not in compared]
    return Comparison(tasks, suites, breaches, provenance, only_banked, only_new)
