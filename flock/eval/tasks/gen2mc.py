"""Gen2MC, OLMo 3's "multiple-choice version of 5 short-form generative tasks" (OLMo 3, 2512.13961, sec. 3.3.4): CoQA (Reddy et al. 2019, 1808.07042), DROP (Dua et al. 2019, 1903.00161), Jeopardy, Natural Questions (Kwiatkowski et al. 2019) and SQuAD (Rajpurkar et al. 2016, 1606.05250)."""

import re
from collections.abc import Callable
from typing import Any

from flock.eval.tasks.base import Choice, Dataset, Fewshot, MCDoc, MultipleChoiceTask, cloze
from flock.eval.tasks.shots.gen2mc import DROP_SHOTS, JEOPARDY_SHOTS, NATURALQS_SHOTS, SQUAD_SHOTS

# Every gen2mc row names its gold choice by letter. OLMES looks the key up in its own list rather
# than the row's `label` column (`["A", "B", "C", "D", "E"].index` in each gen2mc task), so a key
# outside A to E raises there and here.
_LETTERS = ("A", "B", "C", "D", "E")


def coqa_doc(row: dict[str, Any]) -> MCDoc:
    """A CoQA row as a doc: the dataset's own rendered prompt (the story, the earlier turns and the final question), then each answer."""
    return MCDoc(
        id=row["id"],
        choices=tuple(Choice(row["query_original"], f" {text}") for text in row["choices"]["text"]),
        gold=_LETTERS.index(row["answerKey"]),
    )


def drop_doc(row: dict[str, Any]) -> MCDoc:
    """A DROP row as a cloze doc after a `Passage: ` line."""
    passage = row["passage_original"].strip()

    return MCDoc(
        id=row["id"],
        choices=cloze(
            row["question_original"],
            row["choices"]["text"],
            question_prefix=f"Passage: {passage}\nQuestion: ",
        ),
        gold=_LETTERS.index(row["answerKey"]),
    )


def jeopardy_doc(row: dict[str, Any]) -> MCDoc:
    """A Jeopardy row as a cloze doc after a `Category: ` line."""
    # OLMES's own split of `CATEGORY: clue`, kept as is: the first colon ends the category, a
    # newline ends the clue, and a row without a colon raises, as there.
    category, clue = re.findall(r"(.*?):\s*(.*)", row["context_original"])[0]

    return MCDoc(
        id=row["id"],
        choices=cloze(
            clue,
            row["choices"]["text"],
            question_prefix=f"Category: {category}\nQuestion: ",
        ),
        gold=_LETTERS.index(row["answerKey"]),
    )


def naturalqs_doc(row: dict[str, Any]) -> MCDoc:
    """A Natural Questions row as a cloze doc, on the rewritten question rather than the original lowercase query."""
    return MCDoc(
        id=row["id"],
        choices=cloze(row["question"], row["choices"]["text"]),
        gold=_LETTERS.index(row["answerKey"]),
    )


def squad_doc(row: dict[str, Any]) -> MCDoc:
    """A SQuAD row as a cloze doc after `Title: ` and `Passage: ` lines."""
    title = row["title_original"]
    passage = row["context_original"].strip()

    return MCDoc(
        id=row["id"],
        choices=cloze(
            row["question_original"],
            row["choices"]["text"],
            question_prefix=f"Title: {title}\nPassage: {passage}\nQuestion: ",
        ),
        gold=_LETTERS.index(row["answerKey"]),
    )


def _gen2mc_task(
    name: str,
    repo: str,
    split: str,
    revision: str,
    to_doc: Callable[[dict[str, Any]], MCDoc],
    fewshot: Fewshot | None,
) -> MultipleChoiceTask:
    """OLMES's `<name>:rc::gen2mc:xlarge`, scored by accuracy per character on the one split its dataset has."""
    return MultipleChoiceTask(
        name=name,
        dataset=Dataset(
            repo=repo,
            revision=revision,
            subset=None,
            split=split,
        ),
        to_doc=to_doc,
        primary_metric="acc_per_char",
        fewshot=fewshot,
    )


COQA = _gen2mc_task(
    "coqa",
    "allenai/coqa-gen2mc",
    "validation",
    "5e0394a08cc63398a22b745541e5cedf9c171bf3",
    coqa_doc,
    # Zero-shot in OLMES: each prompt already holds a long story.
    None,
)
DROP = _gen2mc_task(
    "drop",
    "allenai/drop-gen2mc",
    "validation",
    "33191ccb7014f77bc6dc3f59792acd43f658eb06",
    drop_doc,
    Fewshot(rows=DROP_SHOTS),
)
JEOPARDY = _gen2mc_task(
    "jeopardy",
    "allenai/jeopardy-gen2mc",
    "test",
    "0bbd389863e56b304b22b66106caf72566144aa8",
    jeopardy_doc,
    Fewshot(rows=JEOPARDY_SHOTS),
)
NATURALQS = _gen2mc_task(
    "naturalqs",
    "allenai/nq-gen2mc",
    "validation",
    "a7e553744f28cb673bf25b734513c41e3f413840",
    naturalqs_doc,
    Fewshot(rows=NATURALQS_SHOTS),
)
SQUAD = _gen2mc_task(
    "squad",
    "allenai/squad-gen2mc",
    "validation",
    "e80f601243d8ed28732c5073cea1092aa4b8bc09",
    squad_doc,
    Fewshot(rows=SQUAD_SHOTS),
)
