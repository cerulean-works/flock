"""Medical licensing exam questions: MedMCQA (Pal et al. 2022, 2203.14371) and MedQA (Jin et al. 2021, 2009.13081)."""

from typing import Any

from flock.eval.tasks.base import Dataset, Fewshot, MCDoc, MultipleChoiceTask, cloze


def medmcqa_doc(row: dict[str, Any]) -> MCDoc:
    """A MedMCQA row as a cloze doc over its four options."""
    return MCDoc(
        id=row["id"],
        choices=cloze(row["question"], (row["opa"], row["opb"], row["opc"], row["opd"])),
        gold=int(row["cop"]),
    )


def medqa_doc(row: dict[str, Any]) -> MCDoc:
    """A MedQA row as a cloze doc, identified by its row index as in OLMES."""
    return MCDoc(
        id=str(row["index"]),
        choices=cloze(row["question"], row["choices"]),
        gold=int(row["answer_idx"]),
    )


# OLMES draws `Random(1234).sample(train, 5)` afresh for every doc of both tasks, so every doc
# gets the same 5 (`fewshot_examples`). Drawn once with OLMES's code. Both primary metrics are
# OLMES's `medmcqa:rc::none` and `medqa_en:rc::none`.
MEDMCQA = MultipleChoiceTask(
    name="medmcqa",
    dataset=Dataset(
        repo="openlifescienceai/medmcqa",
        revision="91c6572c454088bf71b679ad90aa8dffcd0d5868",
        subset=None,
        # The test split has no answers.
        split="validation",
    ),
    to_doc=medmcqa_doc,
    primary_metric="acc_per_char",
    fewshot=Fewshot(
        split="train",
        ids=(
            "ad9ceec9-93f9-40ef-b436-6bb4f23551fd",
            "248808d1-2a95-44f2-9df2-0447b002555c",
            "83b524ec-a747-489a-8021-3a2d96f64b2b",
            "77abb3bf-2058-4dc2-8e6c-efe247008d88",
            "771ffafb-2b91-4beb-8839-1f0d49290cc3",
        ),
    ),
)

MEDQA_EN = MultipleChoiceTask(
    name="medqa_en",
    dataset=Dataset(
        repo="davidheineman/medqa-en",
        revision="a9cc2ff94f0444e1970fd785c986f4b1571239c0",
        subset=None,
        split="test",
        row_index=True,
    ),
    to_doc=medqa_doc,
    primary_metric="acc_per_char",
    # The same ids as SciQ's, correctly: both train splits (10178 and 11679 rows) draw from
    # `Random(1234)` with the same number of bits, so the two samples coincide (checked with OLMES).
    fewshot=Fewshot(split="train", ids=("7220", "1914", "122", "1485", "9539")),
    # OLMES's `MedQAEn.unconditioned_prompt` is `None`, so it reports no PMI.
    unconditional_context=None,
)
