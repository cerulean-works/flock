"""HellaSwag (Zellers et al. 2019, 1905.07830): picking the ending of a video caption or how-to step."""

import re
from typing import Any

from flock.eval.tasks.base import Choice, Dataset, Fewshot, MCDoc, MultipleChoiceTask


def preprocess(text: str) -> str:
    """Removes WikiHow's bracketed markup, as OLMES's `HellaSwag.preprocess` does."""
    text = text.strip()
    text = re.sub("\\.? \\[title\\]", ". ", text)
    text = re.sub("\\[.*?\\]", "", text)

    return text.replace("  ", " ")


def hellaswag_doc(row: dict[str, Any]) -> MCDoc:
    """A HellaSwag row as a doc: the activity and context, then each ending, with no prefixes (`oe_eval/tasks/oe_eval_tasks/hellaswag.py`)."""
    # `capitalize` also lowercases the rest of `ctx_b`, as in OLMES.
    context = preprocess(f"{row['activity_label']}: {row['ctx_a']} {row['ctx_b'].capitalize()}")

    return MCDoc(
        id=str(row["index"]),
        choices=tuple(Choice(context, f" {preprocess(ending)}") for ending in row["endings"]),
        gold=int(row["label"]),
    )


HELLASWAG = MultipleChoiceTask(
    name="hellaswag",
    dataset=Dataset(
        repo="allenai/hellaswag",
        revision="55aa56627db098098f50549e650a00be2684f4bf",
        subset=None,
        # The test split has no labels.
        split="validation",
        # `ind` repeats within a split (9609 values over 10042 validation rows).
        row_index=True,
    ),
    to_doc=hellaswag_doc,
    primary_metric="acc_per_char",
    # The first 5 of OLMES's curated `FEWSHOT_SOURCES["OLMES:hellaswag"]`, found among the train
    # rows by content.
    fewshot=Fewshot(split="train", ids=("14745", "14755", "2", "19", "14754")),
)
