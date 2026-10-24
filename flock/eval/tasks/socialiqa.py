"""SocialIQA (Sap et al. 2019, 1904.09728): questions about people's motives and reactions."""

from typing import Any

from flock.eval.tasks.base import Dataset, Fewshot, MCDoc, MultipleChoiceTask, cloze


def socialiqa_doc(row: dict[str, Any]) -> MCDoc:
    """A SocialIQA row as a doc: the context and question as one cloze question (`oe_eval/tasks/oe_eval_tasks/siqa.py`)."""
    return MCDoc(
        id=str(row["index"]),
        choices=cloze(
            f"{row['context']} {row['question']}",
            (row["answerA"], row["answerB"], row["answerC"]),
        ),
        gold=int(row["label"]) - 1,
    )


SOCIALIQA = MultipleChoiceTask(
    name="socialiqa",
    dataset=Dataset(
        repo="allenai/social_i_qa",
        # The Hub's parquet conversion of the loading script, which `datasets` no longer runs.
        revision="537a2ec8ec565adc0b70b70752893e59e024df26",
        subset=None,
        # The test split has no labels.
        split="validation",
        row_index=True,
    ),
    to_doc=socialiqa_doc,
    # OLMES's `socialiqa:rc::olmes` scores accuracy per character (`oe_eval/configs/tasks.py`).
    primary_metric="acc_per_char",
    # The first 5 of OLMES's curated `FEWSHOT_SOURCES["OLMES:social_i_qa"]`, found among the train
    # rows by content.
    fewshot=Fewshot(split="train", ids=("0", "8", "1", "13", "2")),
)
