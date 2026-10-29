"""DRCD (Shao et al. 2018, 1806.00920): Traditional Chinese reading comprehension, scored on the gold answer."""

from typing import Any

from flock.eval.tasks.base import (
    CHINESE_ANSWER_PREFIX,
    CHINESE_QUESTION_PREFIX,
    Dataset,
    Fewshot,
    GoldOnlyTask,
    MCDoc,
    cloze,
)


def drcd_doc(row: dict[str, Any]) -> MCDoc:
    """A DRCD row as a gold-only doc: the passage after `文章：`, then the question in the Chinese cloze format, scored on the first gold answer. The title is left out, since many titles are Wikipedia page names in Simplified script (stage file 010, 010.D2)."""
    passage = row["context"].strip()

    return MCDoc(
        id=row["id"],
        # The first of the two annotated answers, as SQuAD's gold-answer BPB scores one (010.D5).
        choices=cloze(
            row["question"].strip(),
            (row["answers"]["text"][0].strip(),),
            question_prefix=f"文章：{passage}\n{CHINESE_QUESTION_PREFIX}",
            answer_prefix=CHINESE_ANSWER_PREFIX,
            separator="",
        ),
        gold=0,
    )


# OLMo 3 scores SQuAD and DROP by the gold answer's bits per byte with 5 examples (2512.13961,
# Table 45). DRCD shows 1, so every request fits a 2,048-token context under OLMo 2's and Qwen
# 2.5's tokenizers (010.D3).
DRCD_BPB = GoldOnlyTask(
    name="drcd_bpb",
    dataset=Dataset(
        repo="cerulean-works/drcd",
        # The mirror of DRCD's GitHub commit b944790d, in SQuAD's schema (010.D1).
        revision="e409db6dd4090dcc390a05e660b1344b42b79e98",
        subset=None,
        split="test",
    ),
    to_doc=drcd_doc,
    # The first dev question whose two answers agree, in an article test doesn't share (010.D4).
    fewshot=Fewshot(split="validation", ids=("1172-3-2",)),
)
