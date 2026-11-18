from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from flock.eval.tasks import SUITES, TASKS, Fewshot, MultipleChoiceTask
from flock.eval.tasks.tc_edu import SUBJECTS, TC_EDU, TC_EDU_JH
from flock.eval.tasks.tw_mock_exams import chinese_doc, english_doc

if TYPE_CHECKING:
    from typing import Any


def test_every_test_row_is_scored_whatever_its_check(rows_by_split: dict[str, list[dict]]) -> None:
    def row(id: str, options: list[str], **fields: Any) -> dict:
        # The mock exams' columns, which the format reads, plus tc-edu's own record of the read.
        texts = [{"label": "ABCDE"[i], "text": text} for i, text in enumerate(options)]
        return {
            "hash": id,
            "passage": "",
            "stem": f"{id}？",
            "options": texts,
            "answer": "A",
            "rights": "publisher",
            "check": "ok",
            **fields,
        }

    base = TASKS["tc_edu_jh_social"]
    assert isinstance(base, MultipleChoiceTask)
    task = dataclasses.replace(base, fewshot=Fewshot("dev", ("s1",)))
    rows_by_split.update(
        dev=[row("s1", ["甲", "乙"])],
        test=[
            row("t1", ["甲", "乙", "丙", "丁", "戊"]),
            row("t2", ["甲", "乙"], passage="父親每天早起。"),
            row("t3", ["甲", "乙"], check="unsure", rights="official_paper"),
        ],
    )

    docs, shots = task.load()

    # No `keep`: an `unsure` row, whose key the reader couldn't confirm, is scored too.
    assert [doc.id for doc in docs] == ["t1", "t2", "t3"]
    assert [shot.id for shot in shots] == ["s1"]
    assert len(docs[0].choices) == 5
    assert docs[1].choices[0].context == "父親每天早起。\n問題：t2？\n答案："
    assert task.description == "以下是關於社會考試單選題，請選出正確的答案。\n\n"


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
