"""HumanEval (Chen et al. 2021, 2107.03374): Python functions from their signature and docstring, scored on the canonical solution."""

from typing import Any

from flock.eval.tasks.base import Choice, Dataset, Fewshot, MCDoc, MultipleChoiceTask


def humaneval_doc(row: dict[str, Any]) -> MCDoc:
    """A HumanEval row as a gold-only doc: the prompt, then the canonical solution (`oe_eval/tasks/oe_eval_tasks/codex_humaneval.py`)."""
    solution = row["canonical_solution"]
    # OLMES adds a leading space unless the text starts with one or a newline
    # (`construct_basic_generation_requests` in `oe_eval/tasks/base_task.py`). Every solution
    # is indented, so none gets one.
    if not solution.startswith((" ", "\n")):
        solution = " " + solution

    return MCDoc(
        id=row["task_id"],
        choices=(Choice(row["prompt"], solution),),
        gold=0,
        # As an example, OLMES's `doc_to_target` puts a space before the already indented code.
        example=row["prompt"] + " " + row["canonical_solution"],
    )


# OLMES's `codex_humaneval:3shot:bpb::none`, whose primary metric is the gold solution's
# `bits_per_byte_corr` (`oe_eval/configs/tasks.py`).
HUMANEVAL = MultipleChoiceTask(
    name="humaneval",
    dataset=Dataset(
        repo="openai/openai_humaneval",
        revision="7dce6050a7d6d172f3cc5c32aa97f52fa1a2e544",
        subset=None,
        split="test",
    ),
    to_doc=humaneval_doc,
    primary_metric="bpb",
    # HumanEval has only a test split, so OLMES draws `Random(1234).sample(test, 4)` for every
    # doc and shows the first 3 that aren't the doc (`fewshot_examples` in
    # `oe_eval/tasks/base_task.py`).
    fewshot=Fewshot(
        split="test",
        ids=("HumanEval/112", "HumanEval/29", "HumanEval/1", "HumanEval/23"),
        num_shots=3,
    ),
    # One choice, so an accuracy is always 1 and pointwise mutual information (PMI) has nothing
    # to compare.
    unconditional_context=None,
    metrics=("bpb",),
)
