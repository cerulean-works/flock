import hashlib

import pytest

from flock.eval.tasks import TASKS, Choice, MCDoc, cloze
from flock.eval.tasks.gen2mc import coqa_doc, drop_doc, jeopardy_doc, naturalqs_doc, squad_doc

# Each expected doc below is what OLMES's `_process_doc` and `doc_to_text` give for the same row
# at commit 5a51f50.

CHOICES = {"text": ["Oslo", "Bergen"], "label": ["A", "B"]}


def test_coqa_scores_after_the_datasets_own_prompt():
    row = {"id": "c", "query_original": "Story.\n\nQuestion: Where?\nAnswer:", "answerKey": "B"}
    context = "Story.\n\nQuestion: Where?\nAnswer:"

    assert coqa_doc({**row, "choices": CHOICES}) == MCDoc(
        "c", (Choice(context, " Oslo"), Choice(context, " Bergen")), 1
    )


def test_drop_and_squad_put_their_passage_before_the_question():
    drop = {"id": "d", "passage_original": " P. ", "question_original": "Who?", "answerKey": "A"}
    squad = {
        "id": "s",
        "title_original": "Norway",
        "context_original": "P.\n",
        "question_original": "Who?",
        "answerKey": "A",
    }

    assert drop_doc({**drop, "choices": CHOICES}) == MCDoc(
        "d", cloze("Who?", ("Oslo", "Bergen"), question_prefix="Passage: P.\nQuestion: "), 0
    )
    assert squad_doc({**squad, "choices": CHOICES}).choices[0] == Choice(
        "Title: Norway\nPassage: P.\nQuestion: Who?\nAnswer:", " Oslo"
    )


def test_jeopardy_splits_the_category_at_the_first_colon():
    row = {"id": "j", "context_original": "CITIES:  Its 2 harbours: both cold", "answerKey": "A"}

    assert jeopardy_doc({**row, "choices": CHOICES}).choices[0] == Choice(
        "Category: CITIES\nQuestion: Its 2 harbours: both cold\nAnswer:", " Oslo"
    )


def test_naturalqs_asks_the_rewritten_question():
    row = {
        "id": "n",
        "question": "Where is it?",
        "question_original": "where is it",
        "answerKey": "B",
    }

    assert naturalqs_doc({**row, "choices": CHOICES}) == MCDoc(
        "n", cloze("Where is it?", ("Oslo", "Bergen")), 1
    )


# sha256 of each task's rendered few-shot head, equal to the head of OLMES's first request for the
# task at 5a51f50, so an edit to a written-out row fails here.
HEADS = {
    "drop": "1128c0d07330c793",
    "jeopardy": "cc1152970a7b5f94",
    "naturalqs": "c2544b9ae11415f9",
    "squad": "38ba3156ac5e1e0a",
}


def test_gen2mc_shots_render_olmes_heads():
    assert TASKS["coqa"].fewshot is None
    for name, digest in HEADS.items():
        task = TASKS[name]
        assert task.fewshot is not None
        shots = [task.to_doc(row) for row in task.fewshot.rows]
        assert hashlib.sha256(task._head(shots).encode()).hexdigest()[:16] == digest


def test_gen2mc_rejects_a_key_that_is_not_one_letter():
    with pytest.raises(ValueError):
        naturalqs_doc({"id": "n", "question": "q", "choices": CHOICES, "answerKey": ""})
