"""Yes/no questions about scientific papers: Qasper (Dasigi et al. 2021, 2105.03011) and SciRIFF (Wadden et al. 2024, 2406.07835), in OLMES's yes/no subsets."""

from typing import Any

from flock.eval.tasks.base import Choice, Dataset, Fewshot, MCDoc, MultipleChoiceTask


def _yesno_doc(row: dict[str, Any], evidence: str) -> MCDoc:
    """A yes/no row as a doc: the evidence, then the question, with the whole prompt stripped, as OLMES's `QASPER_YESNO.doc_to_text` and `SCIRIFF_YESNO.doc_to_text` render it."""
    context = f"{evidence}\nQuestion: {row['question']}\nAnswer:".strip()

    return MCDoc(
        id=row["id"],
        choices=(Choice(context, " Yes"), Choice(context, " No")),
        gold=0 if row["answer"] == "Yes" else 1,
    )


def qasper_doc(row: dict[str, Any]) -> MCDoc:
    """A Qasper row, its evidence paragraphs joined by spaces."""
    return _yesno_doc(row, " ".join(row["evidence"]))


def sciriff_doc(row: dict[str, Any]) -> MCDoc:
    """A SciRIFF row, its context as the evidence."""
    return _yesno_doc(row, row["context"])


# Both are OLMES's `<task>:rc::olmes`, scored on the train split by accuracy over the raw
# log-probability. OLMES draws `Random(1234).sample(train, 5)` afresh for every doc, from the
# split it scores, and never drops the doc itself (`fewshot_examples`). So these 5 docs see their
# own answer, kept for parity (stage file 003, D17). Drawn once with OLMES's code.
QASPER_YESNO = MultipleChoiceTask(
    name="qasper_yesno",
    dataset=Dataset(
        repo="allenai/qasper-yesno",
        revision="f47a392e5444515c600ab595bf7227f8710a8c68",
        subset=None,
        split="train",
    ),
    to_doc=qasper_doc,
    primary_metric="acc_raw",
    fewshot=Fewshot(
        split="train",
        ids=("1909.00338_0", "1812.06705_2", "1907.05664_0", "1908.10449_0", "1909.05890_0"),
    ),
)

SCIRIFF_YESNO = MultipleChoiceTask(
    name="sciriff_yesno",
    dataset=Dataset(
        repo="allenai/sciriff-yesno",
        revision="c5ce62cb03b2b6052ff9fd24817444ea79e66bd4",
        subset=None,
        split="train",
    ),
    to_doc=sciriff_doc,
    primary_metric="acc_raw",
    fewshot=Fewshot(
        split="train",
        ids=(
            "bioasq_yesno_qa:train:727",
            "bioasq_yesno_qa:train:1232",
            "bioasq_yesno_qa:train:1010",
            "bioasq_yesno_qa:train:1179",
            "pubmedqa_qa:train:108",
        ),
    ),
)
