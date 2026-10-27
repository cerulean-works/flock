import math

import datasets
import pytest
import torch

from flock.eval.requests import Scores
from flock.eval.run import score_task
from flock.eval.tasks import SUITES, TASKS, Choice, Dataset, Fewshot, MCDoc, cloze
from flock.eval.tasks.tmmluplus import CATEGORIES, TAIWAN_TASKS, TMMLUPLUS, tmmluplus_doc

ROWS = {
    "train": [
        {
            "question": "臺灣最高的山是哪一座？",
            "A": "雪山",
            "B": "玉山",
            "C": "合歡山",
            "D": "阿里山",
            "answer": "B",
        },
        {
            "question": "一年有幾個月？",
            "A": "10",
            "B": "11",
            "C": "12",
            "D": "13",
            "answer": "C",
        },
        {
            "question": "哪一個是水果？",
            "A": "石頭",
            "B": "椅子",
            "C": "雲",
            "D": "蘋果",
            "answer": "D",
        },
    ],
    "test": [
        {
            "question": " 水在攝氏幾度結冰？ ",
            "A": " 0 度",
            "B": "50 度",
            "C": "100 度",
            "D": "200 度",
            "answer": "A",
        },
        {
            "question": "哪一種動物會飛？",
            "A": "魚",
            "B": "老鷹",
            "C": "牛",
            "D": "蛇",
            "answer": "B",
        },
    ],
}


@pytest.fixture
def tmmluplus_rows(monkeypatch, real_load):
    """Every task loads `ROWS` by split through the real `Dataset.load`, which numbers the rows."""
    monkeypatch.setattr(Dataset, "load", real_load)
    monkeypatch.setattr(
        datasets,
        "load_dataset",
        lambda *args, split, **kwargs: datasets.Dataset.from_list(ROWS[split]),
    )


def test_doc_strips_text_and_scores_answers_with_no_leading_space():
    doc = tmmluplus_doc({**ROWS["test"][0], "index": 0})

    assert doc == MCDoc(
        "0",
        (
            Choice("問題：水在攝氏幾度結冰？\n答案：", "0 度"),
            Choice("問題：水在攝氏幾度結冰？\n答案：", "50 度"),
            Choice("問題：水在攝氏幾度結冰？\n答案：", "100 度"),
            Choice("問題：水在攝氏幾度結冰？\n答案：", "200 度"),
        ),
        0,
    )


def test_request_is_header_then_shots_then_question(tmmluplus_rows):
    task = TASKS["tmmluplus_organic_chemistry"]

    built = task.requests(*task.load())
    assert built.requests[1].context == (
        "以下是關於有機化學考試單選題，請選出正確的答案。\n\n"
        "問題：臺灣最高的山是哪一座？\n答案：玉山\n\n"
        "問題：一年有幾個月？\n答案：12\n\n"
        "問題：哪一個是水果？\n答案：蘋果\n\n"
        "問題：水在攝氏幾度結冰？\n答案："
    )
    assert built.requests[1].continuation == "50 度"
    # The unconditional context for PMI is the answer prefix alone.
    assert built.requests[8].context == "答案："
    assert built.requests[8].continuation == "0 度"


def test_short_dev_split_shows_every_dev_row(tmmluplus_rows):
    # `organic_chemistry` has 3 dev rows in v1.1, and most subjects have 5.
    assert TASKS["tmmluplus_organic_chemistry"].fewshot == Fewshot("train", ("0", "1", "2"))
    assert TASKS["tmmluplus_accounting"].fewshot == Fewshot("train", ("0", "1", "2", "3", "4"))

    _, shots = TASKS["tmmluplus_organic_chemistry"].load()
    assert [shot.id for shot in shots] == ["0", "1", "2"]


def test_chinese_choices_count_characters_and_utf8_bytes(tmmluplus_rows):
    task = TASKS["tmmluplus_organic_chemistry"]
    built = task.requests(*task.load())
    n = len(built.requests)
    scores = Scores(
        torch.zeros(n), torch.ones(n, dtype=torch.int64), torch.zeros(n, dtype=torch.int64)
    )
    layout = built.choice_scores(scores)

    # "老鷹": two characters of 3 bytes each, and no leading space.
    assert layout.num_chars[1, 1].item() == 2
    assert layout.num_bytes[1, 1].item() == 6
    # "0 度": a digit, a space and one 3-byte character.
    assert layout.num_chars[0, 0].item() == 3
    assert layout.num_bytes[0, 0].item() == 5


def test_metrics_on_chinese_text_follow_their_definitions(scorer, tmmluplus_rows):
    task = TASKS["tmmluplus_organic_chemistry"]
    built = task.requests(*task.load())
    logprob = scorer.loglikelihood(built.requests).logprob.tolist()

    result = score_task(scorer, task)

    # By hand: `acc_per_char` ranks log-probability over characters, `bpb` reads the gold
    # choice's negated log-probability per UTF-8 byte in bits.
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


def test_chinese_scores_do_not_depend_on_batching(scorer, tmmluplus_rows):
    task = TASKS["tmmluplus_organic_chemistry"]
    one_batch = score_task(scorer, task)

    scorer.batch_tokens = 1
    assert score_task(scorer, task).metrics == pytest.approx(one_batch.metrics, abs=1e-5)


def test_cloze_takes_other_prefixes_and_separator():
    assert cloze("為什麼？", ("因為",), "問題：", "答案：", separator="") == (
        Choice("問題：為什麼？\n答案：", "因為"),
    )


def test_subjects_and_categories_match_the_card():
    names = [subject.name for subjects in CATEGORIES.values() for subject in subjects]
    sizes = {category: len(subjects) for category, subjects in CATEGORIES.items()}

    assert len(set(names)) == len(TMMLUPLUS.tasks) == 66
    assert sizes == {"stem": 14, "social_sciences": 18, "humanities": 7, "other": 27}
    assert [task.name for task in TAIWAN_TASKS] == [
        "tmmluplus_geography_of_taiwan",
        "tmmluplus_ttqav2",
        "tmmluplus_taiwanese_hokkien",
    ]


def test_total_is_the_mean_of_the_category_means():
    metrics = {task.name: {"acc_per_char": 0.0, "bpb": 1.0} for task in TMMLUPLUS.tasks}
    # One STEM subject scores 1 and the other 13 score 0, so STEM scores 1 / 14.
    metrics["tmmluplus_physics"] = {"acc_per_char": 1.0, "bpb": 3.0}

    assert SUITES["tmmluplus:stem"].score(metrics) == pytest.approx(1 / 14)
    # Each category counts once: (1 / 14 + 0 + 0 + 0) / 4, not 1 / 66.
    assert SUITES["tmmluplus"].score(metrics) == pytest.approx(1 / 14 / 4)
    assert SUITES["tmmluplus:bpb"].score(metrics) == pytest.approx((1 + 2 / 14) / 4 + 3 / 4)


def test_tmmluplus_all_runs_every_subject_once():
    assert SUITES["tmmluplus:all"].tasks == TMMLUPLUS.tasks
    assert SUITES["tmmluplus:taiwan:bpb"].tasks == TAIWAN_TASKS


def test_a_test_row_repeating_a_dev_row_skips_that_shot(tmmluplus_rows):
    # As `official_document_management`'s test row 129 repeats its dev row 0 in v1.1, with the
    # choices in another order.
    task = TASKS["tmmluplus_organic_chemistry"]
    _, shots = task.load()
    row = {**ROWS["train"][2], "A": "蘋果", "D": "石頭", "answer": "A", "index": 129}
    repeat = tmmluplus_doc(row)

    context = task.requests([repeat], shots).requests[0].context
    assert context.count("問題：") == 3
    assert "答案：蘋果" not in context
