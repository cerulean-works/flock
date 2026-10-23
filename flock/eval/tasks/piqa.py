"""PIQA (Bisk et al. 2020, 1911.11641): picking the right way to reach a physical goal."""

from typing import Any

from flock.eval.tasks.base import Dataset, Fewshot, MCDoc, MultipleChoiceTask, cloze


def piqa_doc(row: dict[str, Any]) -> MCDoc:
    """A PIQA row as a doc, with OLMES's `Goal: ` prefix (`oe_eval/tasks/oe_eval_tasks/piqa.py`)."""
    return MCDoc(
        id=str(row["index"]),
        choices=cloze(row["goal"], (row["sol1"], row["sol2"]), question_prefix="Goal: "),
        gold=row["label"],
    )


PIQA = MultipleChoiceTask(
    name="piqa",
    dataset=Dataset(
        repo="ybisk/piqa",
        # The Hub's parquet conversion of the loading script, which `datasets` no longer runs.
        revision="142c51238b3ca2bc61e9a075913871b8b600e8e1",
        subset=None,
        # The test split has no labels.
        split="validation",
        row_index=True,
    ),
    to_doc=piqa_doc,
    primary_metric="acc_per_char",
    # The first 5 of OLMES's curated `FEWSHOT_SOURCES["OLMES:piqa"]`, found among the train rows
    # by content.
    fewshot=Fewshot(split="train", ids=("9", "3", "4", "5", "6")),
)
