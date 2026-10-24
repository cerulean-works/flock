"""LAMBADA (Paperno et al. 2016, 1606.06031): predicting the last word of a passage."""

from typing import Any

from flock.eval.tasks.base import Choice, Dataset, MCDoc, MultipleChoiceTask


def lambada_doc(row: dict[str, Any]) -> MCDoc:
    """A LAMBADA row as a gold-only doc: the passage's words but the last, joined by single spaces, then the last word (`oe_eval/tasks/oe_eval_tasks/lambada.py`)."""
    words = row["text"].split()

    return MCDoc(
        id=str(row["index"]), choices=(Choice(" ".join(words[:-1]), f" {words[-1]}"),), gold=0
    )


LAMBADA = MultipleChoiceTask(
    name="lambada",
    dataset=Dataset(
        repo="EleutherAI/lambada_openai",
        revision="900124bf3b8235c6daf21033af9948b3f07346c4",
        subset=None,
        split="test",
        row_index=True,
    ),
    to_doc=lambada_doc,
    # OLMES's `lambada:bpb` (`oe_eval/configs/tasks.py`). Its `lambada` scores greedy match of
    # the last word, which needs another scorer output and comes in its own change (stage file
    # 003, D20), so only `bpb` is reported until then.
    primary_metric="bpb",
    metrics=("bpb",),
    # One choice, so PMI has nothing to compare, and OLMES's LAMBADA has none.
    unconditional_context=None,
)
