"""Pins every registered task's definition and the requests the toy tasks render, as JSON files beside this module."""

import dataclasses
import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import FunctionType
from typing import Any
from unittest import mock

from flock.eval.tasks import Dataset, MultipleChoiceTask, Suite, Task

from .. import toy

TASKS_FILE = Path(__file__).parent / "tasks.json"
TOY_REQUESTS_FILE = Path(__file__).parent / "toy_requests.json"
REGENERATE = "If the change is intended, run `uv run python -m tests.eval.snapshot` and commit it."


def definitions(tasks: Mapping[str, Task], suites: Mapping[str, Suite]) -> dict[str, Any]:
    """Returns every task's definition fields and every suite's members, by name."""
    pinned_tasks = {}
    for name, task in tasks.items():
        # Every field but the name, read off the dataclasses so a new field is pinned too.
        definition = dataclasses.asdict(task)
        del definition["name"]
        definition["type"] = f"{type(task).__module__}.{type(task).__qualname__}"
        # A perplexity task is plain data. A multiple-choice task also has a row function and may
        # write out its examples.
        if isinstance(task, MultipleChoiceTask):
            # A module-level function is pinned by where it is defined. A lambda, a closure or a
            # `partial` has no name that changes with what it does, so it fails here until this
            # code says how to pin it.
            to_doc = task.to_doc
            if not isinstance(to_doc, FunctionType) or "<" in to_doc.__qualname__:
                raise TypeError(f"{name}: to_doc must be a module-level function, got {to_doc!r}")
            definition["to_doc"] = f"{to_doc.__module__}.{to_doc.__qualname__}"
            # Rows written out in a task's module are long and reviewed where they are written.
            if task.fewshot is not None and task.fewshot.rows:
                rows = json.dumps(task.fewshot.rows, sort_keys=True).encode()
                definition["fewshot"]["rows"] = {
                    "count": len(task.fewshot.rows),
                    "sha256": hashlib.sha256(rows).hexdigest(),
                }
        # As the file reads back: JSON turns tuples into lists. A field JSON can't hold fails here,
        # naming the task, until this code says how to pin it.
        try:
            pinned_tasks[name] = json.loads(json.dumps(definition))
        except TypeError as error:
            raise TypeError(f"{name}: {error}") from None

    pinned_suites = {}
    for name, suite in suites.items():
        # Every field but the name, members by name: each member task is pinned on its own.
        definition = {}
        for field in dataclasses.fields(suite):
            definition[field.name] = getattr(suite, field.name)
        del definition["name"]
        definition["members"] = [member.name for member in suite.members]
        pinned_suites[name] = json.loads(json.dumps(definition))

    return {"tasks": pinned_tasks, "suites": pinned_suites}


def toy_requests(tasks: Iterable[Task]) -> dict[str, Any]:
    """Returns every request each task renders from the toy rows, and its hash, by task name. A perplexity task renders no requests, so it gives the texts it scores whole: their windows depend on the tokenizer, and the test against lm-evaluation-harness's windows pins those."""
    rendered = {}
    with mock.patch.object(Dataset, "load", toy.load):
        for task in tasks:
            if not isinstance(task, MultipleChoiceTask):
                texts = task.load()
                docs = [{"doc": doc.id, "text": doc.text} for doc in texts]
                rendered[task.name] = {"hash": task.task_hash(texts), "docs": docs}
                continue

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
    """Lists what differs between two snapshots of entries by name, one line per field, such as `task arc_easy: dataset.revision was "...", is now "..."`. A field only one side has reads `missing` on the other."""
    lines = []
    for name in sorted(stored.keys() | live.keys()):
        if name not in live:
            lines.append(f"{kind} {name}: removed")
        elif name not in stored:
            lines.append(f"{kind} {name}: added")
        else:
            count = len(lines)
            before, after = dict(_leaves(stored[name])), dict(_leaves(live[name]))
            for field in sorted(before.keys() | after.keys()):
                # Compared as JSON text, not with `==`: `True == 1` and `1 == 1.0` in Python, but
                # the file and the task hash tell them apart.
                old = (
                    json.dumps(before[field], ensure_ascii=False) if field in before else "missing"
                )
                new = json.dumps(after[field], ensure_ascii=False) if field in after else "missing"
                if old != new:
                    lines.append(f"{kind} {name}: {field} was {old}, is now {new}")
            # Dotted paths can coincide, as a list `[x]` and a dict `{"0": x}` do.
            old_text = json.dumps(stored[name], sort_keys=True)
            if len(lines) == count and old_text != json.dumps(live[name], sort_keys=True):
                lines.append(f"{kind} {name}: changed shape")

    return lines


def _leaves(value: Any, path: str = "") -> Iterable[tuple[str, Any]]:
    """Yields every leaf of nested dicts and lists with its dotted path, such as `dataset.revision`. An empty dict or list is a leaf, so a new field holding one is still seen."""
    if isinstance(value, dict) and value:
        children = value.items()
    elif isinstance(value, list) and value:
        children = enumerate(value)
    else:
        yield path, value
        return
    for key, child in children:
        yield from _leaves(child, f"{path}.{key}" if path else str(key))
