import dataclasses
import json

import pytest

from flock.eval.tasks import SUITES, TASKS, Fewshot

from . import toy
from .snapshot import (
    REGENERATE,
    TASKS_FILE,
    TOY_REQUESTS_FILE,
    definitions,
    differences,
    toy_requests,
)


def test_every_registered_task_and_suite_matches_the_snapshot():
    stored = json.loads(TASKS_FILE.read_text())
    live = definitions(TASKS, SUITES)

    changed = differences("task", stored.get("tasks", {}), live["tasks"])
    changed += differences("suite", stored.get("suites", {}), live["suites"])
    assert not changed, "\n".join([*changed, REGENERATE])


def test_every_prompt_path_renders_the_pinned_requests():
    stored = json.loads(TOY_REQUESTS_FILE.read_text())

    changed = differences("toy task", stored, toy_requests(toy.PROMPT_PATHS))
    assert not changed, "\n".join([*changed, REGENERATE])


def test_a_changed_revision_fails_naming_the_task_and_the_field():
    stored = json.loads(TASKS_FILE.read_text())["tasks"]
    arc_easy = TASKS["arc_easy"]
    moved = dataclasses.replace(
        arc_easy, dataset=dataclasses.replace(arc_easy.dataset, revision="f" * 40)
    )
    tasks = {**TASKS, "arc_easy": moved, "toy": toy.TASK}

    assert differences("task", stored, definitions(tasks, {})["tasks"]) == [
        f'task arc_easy: dataset.revision was "{arc_easy.dataset.revision}", is now "{"f" * 40}"',
        "task toy: added",
    ]


def test_a_changed_row_written_out_changes_its_digest():
    first, second = toy.ROWS["train"]
    written = dataclasses.replace(toy.TASK, fewshot=Fewshot(rows=(first, second)))
    edited = dataclasses.replace(
        toy.TASK, fewshot=Fewshot(rows=({**first, "answerKey": "A"}, second))
    )

    before = definitions({"toy": written}, {})["tasks"]
    after = definitions({"toy": edited}, {})["tasks"]
    assert before["toy"]["fewshot"]["rows"]["count"] == 2
    [changed] = differences("task", before, after)
    assert changed.startswith("task toy: fewshot.rows.sha256 was ")


def test_differences_see_new_null_and_empty_fields_and_json_types():
    stored = {"toy": {"fewshot": {"rows": []}, "scored": True, "num_shots": 1}}
    live = {"toy": {"fewshot": {}, "scored": 1, "num_shots": 1.0, "data_file": None}}

    assert differences("task", stored, live) == [
        "task toy: data_file was missing, is now null",
        "task toy: fewshot was missing, is now {}",
        "task toy: fewshot.rows was [], is now missing",
        "task toy: num_shots was 1, is now 1.0",
        "task toy: scored was true, is now 1",
    ]


def test_a_row_function_without_a_stable_name_is_refused():
    closure = dataclasses.replace(toy.TASK, to_doc=lambda row: toy.TASK.to_doc(row))

    with pytest.raises(TypeError, match="toy: to_doc must be a module-level function"):
        definitions({"toy": closure}, {})
