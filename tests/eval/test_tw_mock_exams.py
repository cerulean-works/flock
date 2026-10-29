import dataclasses
import math

import pytest
import torch

from flock.eval.requests import Scores
from flock.eval.run import score_task
from flock.eval.tasks import SUITES, TASKS, Choice, Dataset, Fewshot
from flock.eval.tasks.tw_mock_exams import SUBJECTS, TW_MOCK_EXAMS, tw_mock_exams_doc


def row(id: str, stem: str, options: list[str], answer: str, **fields) -> dict:
    """A dataset row as the private repo holds it, scored unless `fields` says otherwise."""
    return {
        "hash": id,
        "doc_subject": "歷史",
        "rights": "school_exam",
        "type": "單選",
        "passage": "",
        "stem": stem,
        "options": [{"label": "ABCDE"[i], "text": text} for i, text in enumerate(options)],
        "answer": answer,
        "char_sensitive": False,
        "listening": False,
        "cloze": False,
        "figure_ref": False,
        "follow_up": False,
        **fields,
    }


SHOTS = [
    row("s1", "臺灣最高的山是哪一座？", ["雪山", "玉山"], "B"),
    row("s2", "一年有幾個月？", ["12", "10"], "A"),
]
TEST = [
    row("t1", " 鄭成功在哪一年來臺？ ", [" 1661 年", "1895 年"], "A", rights="publisher_copyright"),
    row("t2", "這是哪一個朝代？", ["清", "明"], "B", passage=" 康熙皇帝在位六十一年。\n"),
    row("x1", "哪些是水果？", ["蘋果", "石頭"], "A", type="多選"),
    row("x2", "下列何者是錯別字？", ["再", "在"], "A", char_sensitive=True),
    row("x3", "如圖所示，何者正確？", ["甲", "乙"], "A", figure_ref=True),
    row("x4", "承上題，何者正確？", ["甲", "乙"], "A", follow_up=True),
    row("x5", "下列哪一種農業活動？", ["", ""], "B"),
    # Repeats example s1 with its choices reordered, so it mustn't be shown s1.
    row("t3", "臺灣最高的山是哪一座？", ["玉山", "雪山"], "A"),
]


@pytest.fixture
def history():
    """`tw_mock_exams_history` with two examples, reading the rows above by split."""
    task = dataclasses.replace(TASKS["tw_mock_exams_history"], fewshot=Fewshot("dev", ("s1", "s2")))
    rows = {"dev": SHOTS, "test": TEST}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Dataset, "load", lambda dataset, split=None: rows[split or dataset.split])
        yield task


def test_only_eligible_rows_are_scored_whatever_their_rights(history):
    docs, shots = history.load()

    # Multi-answer, character-sensitive, figure, follow-up and empty-option rows are dropped.
    assert [doc.id for doc in docs] == ["t1", "t2", "t3"]
    assert [shot.id for shot in shots] == ["s1", "s2"]
    # The filter applies before the limit.
    assert [doc.id for doc in history.load(limit=3)[0]] == ["t1", "t2", "t3"]


def test_chinese_request_is_header_then_shots_then_stripped_question(history):
    built = history.requests(*history.load())

    assert built.requests[0].context == (
        "以下是關於歷史考試單選題，請選出正確的答案。\n\n"
        "問題：臺灣最高的山是哪一座？\n答案：玉山\n\n"
        "問題：一年有幾個月？\n答案：12\n\n"
        "問題：鄭成功在哪一年來臺？\n答案："
    )
    # No space before a Chinese answer, and the option is stripped.
    assert built.requests[0].continuation == "1661 年"
    # PMI scores each choice after the answer prefix alone.
    assert built.requests[len(built.requests) // 2].context == "答案："


def test_a_grouped_item_puts_its_passage_before_the_question():
    doc = tw_mock_exams_doc(TEST[1])

    assert doc.choices[1] == Choice("康熙皇帝在位六十一年。\n問題：這是哪一個朝代？\n答案：", "明")
    assert doc.gold == 1


def test_english_takes_olmes_format_with_a_spaced_answer():
    english = row(
        "e1", "What is the main idea?", ["Trees", "Rivers"], "B",
        doc_subject="英文", passage="Rivers shape the land.",
    )  # fmt: skip
    task = TASKS["tw_mock_exams_english"]

    assert tw_mock_exams_doc(english).choices == (
        Choice("Rivers shape the land.\nQuestion: What is the main idea?\nAnswer:", " Trees"),
        Choice("Rivers shape the land.\nQuestion: What is the main idea?\nAnswer:", " Rivers"),
    )
    assert task.description == (
        "The following are multiple choice questions (with answers) about English.\n\n"
    )
    assert task.unconditional_context == "Answer:"


def test_a_doc_repeating_an_example_is_not_shown_it(history):
    docs, shots = history.load()
    context = history.requests(docs[2:], shots).requests[0].context

    assert context.count("問題：") == 2
    assert "答案：玉山\n\n" not in context


def uniform_scores(n: int) -> Scores:
    return Scores(
        torch.zeros(n), torch.ones(n, dtype=torch.int64), torch.zeros(n, dtype=torch.int64)
    )


def test_lengths_count_characters_and_utf8_bytes(history):
    built = history.requests(*history.load())
    layout = built.choice_scores(uniform_scores(len(built.requests)))

    # "1661 年": four digits, a space and one 3-byte character, with no leading space.
    assert layout.num_chars[0, 0].item() == 6
    assert layout.num_bytes[0, 0].item() == 8

    english = TASKS["tw_mock_exams_english"]
    doc = tw_mock_exams_doc(row("e1", "Pick one.", ["tea", "茶"], "A", doc_subject="英文"))
    layout = english.requests([doc], []).choice_scores(uniform_scores(4))

    # " tea" counts its leading space; " 茶" is a space and one 3-byte character.
    assert layout.num_chars[0].tolist() == [4, 2]
    assert layout.num_bytes[0].tolist() == [4, 4]


def test_metrics_follow_their_definitions_and_ignore_batching(scorer, history):
    built = history.requests(*history.load())
    logprob = scorer.loglikelihood(built.requests).logprob.tolist()

    result = score_task(scorer, history)

    per_char_hits = []
    bpb = []
    start = 0
    for doc in built.docs:
        doc_logprob = logprob[start : start + len(doc.choices)]
        texts = [choice.continuation for choice in doc.choices]
        per_char = [value / len(text) for value, text in zip(doc_logprob, texts, strict=True)]
        per_char_hits.append(per_char.index(max(per_char)) == doc.gold)
        bpb.append(-doc_logprob[doc.gold] / len(texts[doc.gold].encode()) / math.log(2))
        start += len(doc.choices)
    assert result.metrics["acc_per_char"] == pytest.approx(sum(per_char_hits) / len(built.docs))
    assert result.metrics["bpb"] == pytest.approx(sum(bpb) / len(built.docs), rel=1e-5)

    scorer.batch_tokens = 1
    assert score_task(scorer, history).metrics == pytest.approx(result.metrics, abs=1e-5)


def test_ten_subjects_with_five_examples_each():
    assert len({subject.name for subject in SUBJECTS}) == len(TW_MOCK_EXAMS.tasks) == 10
    assert all(len(set(subject.shots)) == 5 for subject in SUBJECTS)


def test_suite_is_the_plain_mean_of_the_subjects():
    metrics = {task.name: {"acc_per_char": 0.0, "bpb": 1.0} for task in TW_MOCK_EXAMS.tasks}
    metrics["tw_mock_exams_physics"] = {"acc_per_char": 1.0, "bpb": 2.0}

    assert SUITES["tw_mock_exams"].score(metrics) == pytest.approx(1 / 10)
    assert SUITES["tw_mock_exams:bpb"].score(metrics) == pytest.approx(1.1)
    assert SUITES["tw_mock_exams:all"].tasks == TW_MOCK_EXAMS.tasks
