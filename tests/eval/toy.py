"""Toy dataset rows and toy tasks in ARC's row format, read by every eval test instead of a download."""

import dataclasses
from typing import Any

from flock.eval.tasks import (
    CHINESE_ANSWER_PREFIX,
    CHINESE_QUESTION_PREFIX,
    Dataset,
    Fewshot,
    GoldOnlyTask,
    MCDoc,
    MultipleChoiceTask,
    cloze,
)
from flock.eval.tasks.arc import arc_doc

ROWS = {
    "train": [
        {
            "id": "shot-1",
            "question": "Which gas do plants take in?",
            "choices": {"text": ["oxygen", "carbon dioxide"], "label": ["A", "B"]},
            "answerKey": "B",
        },
        {
            "id": "shot-2",
            "question": "臺灣最高的山是哪一座？",
            "choices": {"text": ["玉山", "雪山", "合歡山"], "label": ["1", "2", "3"]},
            "answerKey": "1",
        },
        {
            "id": "shot-3",
            "question": "What do cows drink?",
            "choices": {"text": ["water", "milk"], "label": ["A", "B"]},
            "answerKey": "A",
        },
    ],
    "test": [
        {
            "id": "q-1",
            "question": "What do bees make?",
            "choices": {"text": ["honey", "milk", "silk"], "label": ["A", "B", "C"]},
            "answerKey": "A",
        },
        {
            "id": "q-2",
            "question": "哪一個是水果？",
            "choices": {"text": ["石頭", "蘋果", "椅子", "雲"], "label": ["1", "2", "3", "4"]},
            "answerKey": "2",
        },
    ],
    # Another split repeating q-1 with its choices reordered, as some TMMLU+ dev rows repeat a
    # test row.
    "dev": [
        {
            "id": "dev-1",
            "question": "What do bees make?",
            "choices": {"text": ["silk", "honey"], "label": ["A", "B"]},
            "answerKey": "B",
        },
        {
            "id": "dev-2",
            "question": "What do cows drink?",
            "choices": {"text": ["water", "milk"], "label": ["A", "B"]},
            "answerKey": "A",
        },
    ],
}


def chinese_doc(row: dict[str, Any]) -> MCDoc:
    """A toy row in the Traditional Chinese cloze format, with no space before the answer, as TMMLU+ renders it."""
    return MCDoc(
        id=row["id"],
        choices=cloze(
            row["question"],
            row["choices"]["text"],
            question_prefix=CHINESE_QUESTION_PREFIX,
            answer_prefix=CHINESE_ANSWER_PREFIX,
            separator="",
        ),
        gold=row["choices"]["label"].index(row["answerKey"]),
    )


def not_about_bees(row: dict[str, Any]) -> bool:
    """A `keep` predicate that drops q-1."""
    return "bees" not in row["question"]


def passage_doc(row: dict[str, Any]) -> MCDoc:
    """A toy row as DRCD renders it, scored on the gold answer alone after a `文章：` passage line: the choices joined stand in for the passage."""
    texts = row["choices"]["text"]
    gold = texts[row["choices"]["label"].index(row["answerKey"])]

    return MCDoc(
        id=row["id"],
        choices=cloze(
            row["question"],
            (gold,),
            question_prefix=f"文章：{'、'.join(texts)}\n{CHINESE_QUESTION_PREFIX}",
            answer_prefix=CHINESE_ANSWER_PREFIX,
            separator="",
        ),
        gold=0,
    )


TASK = MultipleChoiceTask(
    name="toy",
    dataset=Dataset("cerulean/toy", "0" * 40, None, "test"),
    to_doc=arc_doc,
    primary_metric="acc_per_char",
    fewshot=Fewshot("train", ("shot-2", "shot-1")),
)


# One toy task per prompt path the core renders. `snapshot/toy_requests.json` pins what each
# renders, so a new prompt path adds its toy task here.
PROMPT_PATHS = (
    TASK,
    # Scores the train split, so `shot-1` and `shot-2` each skip themselves and `shot-3`, which
    # is no example, is cut to `num_shots`.
    dataclasses.replace(
        TASK,
        name="toy:num_shots",
        dataset=dataclasses.replace(TASK.dataset, split="train"),
        fewshot=Fewshot("train", ("shot-2", "shot-1"), num_shots=1),
        description="Science and geography.\n\n",
    ),
    dataclasses.replace(TASK, name="toy:rows", fewshot=Fewshot(rows=tuple(ROWS["train"]))),
    dataclasses.replace(TASK, name="toy:zero_shot", fewshot=None, unconditional_context=None),
    # Examples from another split: q-1 isn't shown `dev-1`, which shows its own answer.
    dataclasses.replace(TASK, name="toy:repeat", fewshot=Fewshot("dev", ("dev-1", "dev-2"))),
    # A row filter: only q-2 is scored, and the examples aren't filtered.
    dataclasses.replace(TASK, name="toy:keep", keep=not_about_bees),
    # The Chinese prefixes, with PMI scored after `答案：`.
    dataclasses.replace(
        TASK,
        name="toy:chinese",
        to_doc=chinese_doc,
        description="以下是關於常識考試單選題，請選出正確的答案。\n\n",
        unconditional_context=CHINESE_ANSWER_PREFIX,
    ),
    # Scored on the gold text alone, with no PMI, after a passage in the Chinese format.
    GoldOnlyTask(
        name="toy:gold_only",
        dataset=TASK.dataset,
        to_doc=passage_doc,
        fewshot=Fewshot("train", ("shot-2",)),
    ),
)


def load(dataset: Dataset, split: str | None = None) -> list[dict[str, Any]]:
    """Stands in for `Dataset.load`, returning the toy rows of `split` whatever the dataset."""
    return ROWS[split or dataset.split]
