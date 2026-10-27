"""Pins every registered task's definition and the requests the toy tasks render, as JSON files beside this module."""

import dataclasses
import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import FunctionType
from typing import Any
from unittest import mock

from flock.eval.tasks import Dataset, MultipleChoiceTask, Suite

from .. import toy

TASKS_FILE = Path(__file__).parent / "tasks.json"
TOY_REQUESTS_FILE = Path(__file__).parent / "toy_requests.json"
REGENERATE = "If the change is intended, run `uv run python -m tests.eval.snapshot` and commit it."


def definitions(
    tasks: Mapping[str, MultipleChoiceTask], suites: Mapping[str, Suite]
) -> dict[str, Any]:
    """Returns every task's definition fields and every suite's members, by name."""
    pinned_tasks = {}
    for name, task in tasks.items():
        # Every field but the name, read off the dataclasses so a new field is pinned too.
        definition = dataclasses.asdict(task)
        del definition["name"]
        definition["type"] = type(task).__name__
        # A function is pinned by where it is defined. Anything else, such as a `partial`, has no
        # stable name and fails here until this code says how to pin it.
        to_doc = task.to_doc
        if not isinstance(to_doc, FunctionType):
            raise TypeError(f"{name}: to_doc must be a function to be pinned, got {to_doc!r}")
        definition["to_doc"] = f"{to_doc.__module__}.{to_doc.__qualname__}"
        # Rows written out in a task's module are long and reviewed where they are written.
        if task.fewshot is not None and task.fewshot.rows:
            rows = json.dumps(task.fewshot.rows, sort_keys=True).encode()
            definition["fewshot"]["rows"] = {
                "count": len(task.fewshot.rows),
                "sha256": hashlib.sha256(rows).hexdigest(),
            }
        pinned_tasks[name] = definition

    pinned_suites = {}
    for name, suite in suites.items():
        pinned_suites[name] = {
            "members": [member.name for member in suite.members],
            "metric": suite.metric,
            "scored": suite.scored,
        }

    # As the file reads back: JSON turns tuples into lists.
    return json.loads(json.dumps({"tasks": pinned_tasks, "suites": pinned_suites}))


def toy_requests(tasks: Iterable[MultipleChoiceTask]) -> dict[str, Any]:
    """Returns every request each task renders from the toy rows, and its hash, by task name."""
    rendered = {}
    with mock.patch.object(Dataset, "load", toy.load):
        for task in tasks:
            docs, shots = task.load()
            scored = task.requests(docs, shots)

            # Each doc's choices in order, then the same again for the unconditional requests.
            owners = []
            for doc in docs:
                for index in range(len(doc.choices)):
                    owners.append((doc.id, index == doc.gold))
            if scored.has_uncond:
                owners *= 2

            requests = []
            for (doc_id, gold), request in zip(owners, scored.requests, strict=True):
                requests.append(
                    {
                        "doc": doc_id,
                        "context": request.context,
                        "continuation": request.continuation,
                        "gold": gold,
                    }
                )
            rendered[task.name] = {"hash": scored.hash, "requests": requests}

    return rendered


def differences(kind: str, stored: Mapping[str, Any], live: Mapping[str, Any]) -> list[str]:
    """Lists what differs between two snapshots of entries by name, one line per field, such as `task arc_easy: dataset.revision was '...', is now '...'`."""
    lines = []
    for name in sorted(stored.keys() | live.keys()):
        if name not in live:
            lines.append(f"{kind} {name}: removed")
        elif name not in stored:
            lines.append(f"{kind} {name}: added")
        else:
            before, after = dict(_leaves(stored[name])), dict(_leaves(live[name]))
            for field in sorted(before.keys() | after.keys()):
                if before.get(field) != after.get(field):
                    lines.append(
                        f"{kind} {name}: {field} was {before.get(field)!r}, "
                        f"is now {after.get(field)!r}"
                    )

    return lines


def _leaves(value: Any, path: str = "") -> Iterable[tuple[str, Any]]:
    """Yields every leaf of nested dicts and lists with its dotted path, such as `dataset.revision`."""
    if isinstance(value, dict):
        children = value.items()
    elif isinstance(value, list):
        children = enumerate(value)
    else:
        yield path, value
        return
    for key, child in children:
        yield from _leaves(child, f"{path}.{key}" if path else str(key))
