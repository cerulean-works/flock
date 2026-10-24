"""WinoGrande (Sakaguchi et al. 2020, 1907.10641): resolving a pronoun between two names."""

from typing import Any

from flock.eval.tasks.base import Choice, Dataset, Fewshot, MCDoc, MultipleChoiceTask


def winogrande_doc(row: dict[str, Any]) -> MCDoc:
    """A WinoGrande row as a doc: each option fills the blank in its own context, and both score the rest of the sentence (`oe_eval/tasks/oe_eval_tasks/winogrande.py`)."""
    blank = row["sentence"].index("_")
    before = row["sentence"][:blank]
    # Skip the blank itself: the continuation is the text after it, behind one space.
    after = " " + row["sentence"][blank + 1 :].strip()

    return MCDoc(
        id=str(row["index"]),
        choices=(Choice(before + row["option1"], after), Choice(before + row["option2"], after)),
        gold=int(row["answer"]) - 1,
    )


WINOGRANDE = MultipleChoiceTask(
    name="winogrande",
    dataset=Dataset(
        repo="allenai/winogrande",
        revision="01e74176c63542e6b0bcb004dcdea22d94fb67b5",
        subset="winogrande_xl",
        # The test split has no answers.
        split="validation",
        row_index=True,
    ),
    to_doc=winogrande_doc,
    # OLMES's `winogrande:rc::olmes` scores raw accuracy (`oe_eval/configs/tasks.py`).
    primary_metric="acc_raw",
    # The first 5 of OLMES's curated `FEWSHOT_SOURCES["OLMES:winogrande"]`, found among the train
    # rows by content.
    fewshot=Fewshot(split="train", ids=("19875", "26035", "2302", "13568", "7412")),
    # Every choice scores the same continuation, so pointwise mutual information (PMI) can't tell
    # them apart, and OLMES skips it.
    unconditional_context=None,
)
