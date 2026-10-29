"""LAMBADA (Paperno et al. 2016, 1606.06031): predicting the last word of a passage."""

from typing import Any

from flock.eval.tasks.base import Choice, Dataset, GoldOnlyTask, MCDoc


def lambada_doc(row: dict[str, Any]) -> MCDoc:
    """A LAMBADA row as a gold-only doc: the passage's words but the last, joined by single spaces, then the last word."""
    words = row["text"].split()

    return MCDoc(
        id=str(row["index"]), choices=(Choice(" ".join(words[:-1]), f" {words[-1]}"),), gold=0
    )


LAMBADA = GoldOnlyTask(
    name="lambada",
    dataset=Dataset(
        repo="EleutherAI/lambada_openai",
        revision="900124bf3b8235c6daf21033af9948b3f07346c4",
        subset=None,
        split="test",
        row_index=True,
    ),
    to_doc=lambada_doc,
    # OLMES's `lambada` scores greedy match of the last word and `lambada:bpb` its `bpb`, on the
    # same requests (`oe_eval/configs/tasks.py`), so one task reports both.
    primary_metric="acc_greedy",
    metrics=("acc_greedy", "bpb"),
)
