"""Minerva MATH (Hendrycks et al. 2021, 2103.03874; Lewkowycz et al. 2022, 2206.14858): competition math problems in 7 subjects, scored on the gold solution."""

from typing import Any

from flock.eval.tasks.base import Choice, Dataset, Fewshot, MCDoc, MultipleChoiceTask
from flock.eval.tasks.shots.minerva_math import MINERVA_MATH_SHOTS

SUBJECTS = (
    "algebra",
    "counting_and_probability",
    "geometry",
    "intermediate_algebra",
    "number_theory",
    "prealgebra",
    "precalculus",
)


def minerva_doc(row: dict[str, Any]) -> MCDoc:
    """A MATH row as a gold-only doc in OLMES's Minerva format: `Problem:\\n<problem>\\n\\nSolution:`, then the solution."""
    context = f"Problem:\n{row['problem']}\n\nSolution:"
    solution = row["solution"]
    # OLMES scores the solution with a leading space unless it starts with one or a newline
    # (`construct_basic_generation_requests`). Its examples always get one (`doc_to_target`),
    # which is the same text: the 4 examples start with a letter.
    if not solution.startswith((" ", "\n")):
        solution = " " + solution

    return MCDoc(id=str(row["index"]), choices=(Choice(context, solution),), gold=0)


# One task per subject, as OLMES's `minerva_math_<subject>:bpb::olmes`, whose primary metric is
# the gold solution's `bits_per_byte_corr`.
MINERVA_MATH_TASKS = tuple(
    MultipleChoiceTask.gold_only(
        name=f"minerva_math_{subject}",
        dataset=Dataset(
            repo="EleutherAI/hendrycks_math",
            revision="21a5633873b6a120296cce3e2df9d5550074f4a3",
            subset=subject,
            split="test",
            row_index=True,
        ),
        to_doc=minerva_doc,
        fewshot=Fewshot(rows=MINERVA_MATH_SHOTS),
    )
    for subject in SUBJECTS
)
