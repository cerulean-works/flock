"""LAB-Bench (Laurent et al. 2024, 2407.10362): biology research questions, two of its subsets."""

from typing import Any

from flock.eval.tasks.base import Dataset, Fewshot, MCDoc, MultipleChoiceTask, cloze


def lab_bench_doc(row: dict[str, Any]) -> MCDoc:
    """A LAB-Bench row as a cloze doc: the distractors, then the ideal answer, in OLMES's order (`GenericLabBenchRC`)."""
    return MCDoc(
        id=row["id"],
        choices=cloze(row["question"], (*row["distractors"], row["ideal"])),
        gold=len(row["distractors"]),
    )


def _lab_bench_task(subset: str, shot_ids: tuple[str, ...]) -> MultipleChoiceTask:
    """OLMES's `lab_bench_<subset>`, scored on the only split, train."""
    return MultipleChoiceTask(
        name=f"lab_bench_{subset.lower()}",
        dataset=Dataset(
            repo="futurehouse/lab-bench",
            revision="5c77cec648430f30611808808861eb86f81d5eaa",
            subset=subset,
            split="train",
        ),
        to_doc=lab_bench_doc,
        # OLMES's `lab_bench_<subset>` scores accuracy per character.
        primary_metric="acc_per_char",
        # OLMES draws `Random(1234).sample(train, 3)` afresh for every doc, from the split it
        # scores, and never drops the doc itself (`fewshot_examples`). So these 3 docs see their
        # own answer, kept for parity (stage file 003, D17). Drawn once with OLMES's code.
        fewshot=Fewshot(split="train", ids=shot_ids),
    )


LAB_BENCH_DBQA = _lab_bench_task(
    "DbQA",
    (
        "b7d66443-572b-4ffd-b72d-8839db7afc5c",
        "fec11ed9-ea38-489c-ae0f-a57dacaf47f6",
        "901552e2-b5a0-4462-a68b-2796823dedee",
    ),
)
LAB_BENCH_PROTOCOLQA = _lab_bench_task(
    "ProtocolQA",
    (
        "eebdad6c-9209-40bd-88a2-23ceb6e9db66",
        "be697753-fe7c-4ed5-ad28-4853e435d21e",
        "7ad6f06c-1e25-452e-a5d1-58258ff2ed18",
    ),
)
