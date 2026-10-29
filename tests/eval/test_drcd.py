import math

import datasets
import pytest

from flock.eval.model import ScoringModel
from flock.eval.run import score_task
from flock.eval.tasks import TASKS, Choice, Dataset, MCDoc, Truncation
from flock.eval.tasks.drcd import drcd_doc


def row(id: str, context: str, question: str, *answers: str) -> dict:
    """A row in the mirror's schema, `rajpurkar/squad`'s."""
    return {
        "id": id,
        "title": "温度",
        "context": context,
        "question": question,
        "answers": {"text": list(answers), "answer_start": [0] * len(answers)},
    }


ROWS = {
    "validation": [
        row("1172-3-1", "冰有三種。", "哪一種？", "甲", "乙"),
        row("1172-3-2", "冰是零度。", "冰幾度？", "零度", "零度"),
    ],
    "test": [
        # Outer whitespace on every field, and two answers that differ.
        row("1-1-1", " 水會結冰。 ", " 幾度？ ", " 零度 ", "0度"),
        row("1-1-2", "塔建於18世紀。", "何時？", "18世紀", "十八世紀"),
        row("1-2-1", "Celsius is used.", "哪一種？", "Celsius", "Celsius"),
    ],
}


@pytest.fixture
def drcd_rows(monkeypatch, real_load):
    """`drcd_bpb` loads `ROWS` by split through the real `Dataset.load`."""
    monkeypatch.setattr(Dataset, "load", real_load)
    monkeypatch.setattr(
        datasets,
        "load_dataset",
        lambda *args, split, **kwargs: datasets.Dataset.from_list(ROWS[split]),
    )


def test_doc_strips_every_field_and_scores_the_first_answer():
    doc = drcd_doc(ROWS["test"][0])

    assert doc == MCDoc("1-1-1", (Choice("文章：水會結冰。\n問題：幾度？\n答案：", "零度"),), 0)


def test_request_is_the_example_then_the_passage_and_question(drcd_rows):
    task = TASKS["drcd_bpb"]

    built = task.requests(*task.load())

    assert built.requests[1].context == (
        "文章：冰是零度。\n問題：冰幾度？\n答案：零度\n\n文章：塔建於18世紀。\n問題：何時？\n答案："
    )
    assert built.requests[1].continuation == "18世紀"
    # Gold-only: one request per doc and no unconditional ones.
    assert len(built.requests) == len(built.docs) == 3


def test_bpb_divides_by_the_answers_utf8_bytes(scorer, drcd_rows):
    task = TASKS["drcd_bpb"]
    built = task.requests(*task.load())
    logprob = scorer.loglikelihood(built.requests).logprob.tolist()

    result = score_task(scorer, task)

    # Every request fits the tiny model, so the score isn't of a cut context.
    assert result.truncation.requests == 0
    # "零度" is 2 characters of 3 bytes, "18世紀" 2 digits and 2 such characters, "Celsius" ASCII.
    num_bytes = [6, 8, 7]
    bpb = [-value / size / math.log(2) for value, size in zip(logprob, num_bytes, strict=True)]
    assert result.metrics == {"bpb": pytest.approx(sum(bpb) / 3, rel=1e-5)}


def test_long_passages_are_cut_from_the_left_and_counted(lm, tokenizer, drcd_rows):
    task = TASKS["drcd_bpb"]
    built = task.requests(*task.load())
    scorer = ScoringModel(lm, tokenizer, name="tiny@0")
    windows = [sum(map(len, scorer.encode(request))) for request in built.requests]
    # The third doc fits exactly: a window holds `max_length + 1` tokens. The first is shorter
    # and the second longer.
    assert windows[0] < windows[2] < windows[1]
    scorer = ScoringModel(lm, tokenizer, name="tiny@0", max_length=windows[2] - 1)

    scores = scorer.loglikelihood(built.requests)
    result = score_task(scorer, task)

    assert scores.tokens_cut.tolist() == [0, windows[1] - windows[2], 0]
    # The cut doc still scores every token of its answer.
    assert scores.num_tokens[1].item() == len(scorer.encode(built.requests[1])[1])
    assert result.truncation == Truncation(
        requests=1, docs=1, max_tokens_cut=windows[1] - windows[2]
    )


def test_scores_do_not_depend_on_batching(scorer, drcd_rows):
    task = TASKS["drcd_bpb"]
    one_batch = score_task(scorer, task)

    # One request per batch, against all three left-padded to the longest in one.
    scorer.batch_tokens = 1
    assert score_task(scorer, task).metrics == pytest.approx(one_batch.metrics, abs=1e-5)
