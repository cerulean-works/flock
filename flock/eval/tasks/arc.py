"""ARC (Clark et al. 2018, 1803.05457): grade-school science questions."""

import dataclasses
from typing import Any

from flock.eval.tasks.base import Dataset, Fewshot, MCDoc, MultipleChoiceTask, cloze


def arc_doc(row: dict[str, Any]) -> MCDoc:
    """An ARC row as a doc. Answer keys are letters or digits, matching the choice labels."""
    return MCDoc(
        id=row["id"],
        choices=cloze(row["question"], row["choices"]["text"]),
        gold=row["choices"]["label"].index(row["answerKey"]),
    )


ARC_EASY = MultipleChoiceTask(
    name="arc_easy",
    dataset=Dataset(
        repo="allenai/ai2_arc",
        revision="210d026faf9955653af8916fad021475a3f00453",
        subset="ARC-Easy",
        # The full test split, as OLMo 3's Base Easy scores it: its `olmo3:base_easy:qa_rc` suite
        # uses OLMES's `arc:rc::olmes:full`, which drops the 1000-doc sample of `arc:rc::olmes`
        # (`oe_eval/configs/task_suites.py`, `oe_eval/configs/tasks.py`).
        split="test",
    ),
    to_doc=arc_doc,
    primary_metric="acc_per_char",
    # OLMES's curated examples for ARC-Easy, all from the train split: the first 5 of
    # `FEWSHOT_SOURCES["OLMES:ARC-Easy"]`, as `num_shots: 5` takes them
    # (`oe_eval/tasks/base_task.py`).
    fewshot=Fewshot(
        split="train",
        ids=(
            "MCAS_2007_8_5189",
            "Mercury_SC_401169",
            "MCAS_2004_8_27",
            "NYSEDREGENTS_2006_8_10",
            "Mercury_7013388",
        ),
    ),
)

ARC_CHALLENGE = MultipleChoiceTask(
    name="arc_challenge",
    dataset=dataclasses.replace(ARC_EASY.dataset, subset="ARC-Challenge"),
    to_doc=arc_doc,
    # OLMES's `arc_challenge:rc::olmes` scores PMI (`oe_eval/configs/tasks.py`).
    primary_metric="acc_uncond",
    # The first 5 of OLMES's curated `FEWSHOT_SOURCES["OLMES:ARC-Challenge"]`, all train rows.
    fewshot=Fewshot(
        split="train",
        ids=(
            "Mercury_SC_415702",
            "MCAS_2009_5_6516",
            "Mercury_7233695",
            "Mercury_7041615",
            "MCAS_1998_4_3",
        ),
    ),
)
