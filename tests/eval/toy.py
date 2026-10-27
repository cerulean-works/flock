"""Toy dataset rows and toy tasks in ARC's row format, read by every eval test instead of a download."""

import dataclasses
from typing import Any

from flock.eval.tasks import (
    CHINESE_ANSWER_PREFIX,
    CHINESE_QUESTION_PREFIX,
    Dataset,
    Fewshot,
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
    # The Chinese prefixes, with PMI scored after `答案：`.
    dataclasses.replace(
        TASK,
        name="toy:chinese",
        to_doc=chinese_doc,
        description="以下是關於常識考試單選題，請選出正確的答案。\n\n",
        unconditional_context=CHINESE_ANSWER_PREFIX,
    ),
)


def load(dataset: Dataset, split: str | None = None) -> list[dict[str, Any]]:
    """Stands in for `Dataset.load`, returning the toy rows of `split` whatever the dataset."""
    return ROWS[split or dataset.split]
