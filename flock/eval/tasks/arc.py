"""ARC (Clark et al. 2018, 1803.05457): grade-school science questions."""

from typing import Any

from flock.eval.tasks.base import Dataset, Fewshot, MCDoc, MultipleChoiceTask

# OLMES's curated 5-shot examples for ARC-Easy ("OLMES:ARC-Easy"), all from the train split.
OLMES_ARC_EASY_SHOTS = (
    "MCAS_2007_8_5189",
    "Mercury_SC_401169",
    "MCAS_2004_8_27",
    "NYSEDREGENTS_2006_8_10",
    "Mercury_7013388",
)


def arc_doc(row: dict[str, Any]) -> MCDoc:
    """An ARC row as a doc. Answer keys are letters or digits, matching the choice labels."""
    return MCDoc(
        id=row["id"],
        question=row["question"],
        choices=tuple(row["choices"]["text"]),
        gold=row["choices"]["label"].index(row["answerKey"]),
    )


# The full test split, as OLMo 3's Base Easy scores it (OLMES itself samples 1000 docs).
ARC_EASY = MultipleChoiceTask(
    name="arc_easy",
    dataset=Dataset(
        "allenai/ai2_arc", "210d026faf9955653af8916fad021475a3f00453", "ARC-Easy", "test"
    ),
    to_doc=arc_doc,
    primary_metric="acc_per_char",
    fewshot=Fewshot("train", OLMES_ARC_EASY_SHOTS),
)
