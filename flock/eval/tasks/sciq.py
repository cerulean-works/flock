"""SciQ (Welbl et al. 2017, 1707.06209): crowdsourced science exam questions."""

from typing import Any

from flock.eval.tasks.base import Dataset, Fewshot, MCDoc, MultipleChoiceTask, cloze


def sciq_doc(row: dict[str, Any]) -> MCDoc:
    """A SciQ row as a cloze doc: the three distractors, then the correct answer, in OLMES's order."""
    return MCDoc(
        id=str(row["index"]),
        choices=cloze(
            row["question"],
            (row["distractor1"], row["distractor2"], row["distractor3"], row["correct_answer"]),
        ),
        gold=3,
    )


SCIQ = MultipleChoiceTask(
    name="sciq",
    dataset=Dataset(
        repo="allenai/sciq",
        revision="2c94ad3e1aafab77146f384e23536f97a4849815",
        subset=None,
        split="validation",
        row_index=True,
    ),
    to_doc=sciq_doc,
    # OLMES's `sciq:rc::olmo3` scores accuracy per character.
    primary_metric="acc_per_char",
    # OLMES draws `Random(1234).sample(train, 5)` afresh for every doc, so every doc gets the same
    # 5 (`fewshot_examples`). Drawn once with OLMES's code.
    fewshot=Fewshot(split="train", ids=("7220", "1914", "122", "1485", "9539")),
)
