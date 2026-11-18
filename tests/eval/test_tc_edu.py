from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

import pytest

from flock.eval.tasks import SUITES, TASKS, Choice, Dataset, Fewshot, MultipleChoiceTask
from flock.eval.tasks.tc_edu import SUBJECTS, TC_EDU, TC_EDU_JH
from flock.eval.tasks.tw_mock_exams import chinese_doc, english_doc

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import Any


def row(id: str, stem: str, options: list[str], answer: str, **fields: Any) -> dict:
    """A dataset row as the private repo holds it: the mock exams' columns plus tc-edu's own."""
    return {
        "hash": id,
        "uid": id,
        "passage": "",
        "stem": stem,
        "options": [{"label": "ABCDE"[i], "text": text} for i, text in enumerate(options)],
        "answer": answer,
        "explanation": "",
        "rights": "publisher",
        "check": "ok",
        **fields,
    }


SHOTS = [
    row("s1", "臺灣最高的山是哪一座？", ["雪山", "玉山"], "B"),
    row("s2", "一年有幾個月？", ["12", "10"], "A"),
]
TEST = [
    row("t1", "下列何者是哺乳類？", ["鯨魚", "鯊魚", "鮭魚", "鰻魚", "海馬"], "A"),
    row("t2", "文中的「他」指誰？", ["作者", "父親"], "B", passage="父親每天早起。\n"),
    # The check is a record, not a filter: an unsure row is scored like any other.
    row("t3", "何者屬於板塊運動？", ["地震", "潮汐"], "A", check="unsure", rights="official_paper"),
]


@pytest.fixture
def social() -> Iterator[MultipleChoiceTask]:
    """`tc_edu_jh_social` with two examples, reading the rows above by split."""
    base = TASKS["tc_edu_jh_social"]
    assert isinstance(base, MultipleChoiceTask)
    task = dataclasses.replace(base, fewshot=Fewshot("dev", ("s1", "s2")))
    rows = {"dev": SHOTS, "test": TEST}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Dataset, "load", lambda dataset, split=None: rows[split or dataset.split])
        yield task


def test_every_test_row_is_scored_in_the_mock_exams_format(social: MultipleChoiceTask) -> None:
    docs, shots = social.load()

    assert [doc.id for doc in docs] == ["t1", "t2", "t3"]
    assert [shot.id for shot in shots] == ["s1", "s2"]
    # Five options score five choices; the passage sits on the line before the question.
    assert len(docs[0].choices) == 5
    assert docs[1].choices[1] == Choice("父親每天早起。\n問題：文中的「他」指誰？\n答案：", "父親")

    built = social.requests(docs, shots)
    assert built.requests[0].context == (
        "以下是關於社會考試單選題，請選出正確的答案。\n\n"
        "問題：臺灣最高的山是哪一座？\n答案：玉山\n\n"
        "問題：一年有幾個月？\n答案：12\n\n"
        "問題：下列何者是哺乳類？\n答案："
    )


def test_nine_subjects_in_two_suites_read_one_pinned_repo() -> None:
    tasks = [TASKS[f"tc_edu_{subject.name}"] for subject in SUBJECTS]

    assert SUITES["tc_edu"] is TC_EDU and SUITES["tc_edu:jh"] is TC_EDU_JH
    assert [task.name for task in TC_EDU.tasks] == [task.name for task in tasks]
    # Senior-high Chinese is the one subject `tc_edu:jh` leaves out.
    assert [task.name for task in TC_EDU_JH.tasks] == [task.name for task in tasks[1:]]
    assert tasks[0].name == "tc_edu_hs_chinese"
    for subject, task in zip(SUBJECTS, tasks, strict=True):
        assert isinstance(task, MultipleChoiceTask)
        assert len(set(subject.shots)) == 5
        assert task.dataset.subset == subject.name
        assert task.dataset.repo == "cerulean-works/tc-edu-exams"
        assert task.keep is None


def test_english_alone_takes_olmes_english_format() -> None:
    for task in TC_EDU.tasks:
        assert isinstance(task, MultipleChoiceTask)
        english = task.name == "tc_edu_jh_english"
        assert task.to_doc is (english_doc if english else chinese_doc)
        assert task.unconditional_context == ("Answer:" if english else "答案：")
