"""Taiwanese senior-high mock exams for 學測 and 指考: ten subjects of single-answer questions, one task each, read from a private dataset."""

from dataclasses import dataclass
from typing import Any

from flock.eval.tasks.base import (
    CHINESE_ANSWER_PREFIX,
    CHINESE_QUESTION_PREFIX,
    Dataset,
    Fewshot,
    MCDoc,
    MultipleChoiceTask,
    cloze,
)
from flock.eval.tasks.suite import Suite


@dataclass(frozen=True, slots=True)
class Subject:
    """
    One subject of the mock exams.

    Args:
        name (`str`):
            The dataset config, which names the task `tw_mock_exams_<name>`.
        chinese_name (`str`):
            The subject in the Chinese header. 英文 takes MMLU's English header instead.
        shots (`tuple[str, ...]`):
            The ids of the subject's 5 `dev` rows, its few-shot examples: its shortest self-contained items, 009.D4 in the stage file.
    """

    name: str
    chinese_name: str
    shots: tuple[str, ...]


def scored(row: dict[str, Any]) -> bool:
    """Whether a row is scored: single-answer, its text unaltered by the transcription and answerable from the text alone (009.D2 in the stage file)."""
    # The flags were set when the dataset was built, so the rule reads as the stage file states it.
    flagged = (
        row["char_sensitive"]
        or row["listening"]
        or row["cloze"]
        or row["figure_ref"]
        or row["follow_up"]
    )
    # One item's options were pictures, which the transcription left empty.
    return (
        row["type"] == "單選"
        and not flagged
        and all(option["text"].strip() for option in row["options"])
    )


def exam_doc(
    row: dict[str, Any], *, question_prefix: str, answer_prefix: str, separator: str
) -> MCDoc:
    """
    A row as a cloze doc, identified by its item hash, with a grouped item's passage on the line before the question as in OLMES's BoolQ (`oe_eval/tasks/oe_eval_tasks/boolq.py`).

    Raises:
        `ValueError`: naming the doc, if its answer isn't exactly one option's label.
    """
    passage = row["passage"].strip()
    if passage:
        question_prefix = f"{passage}\n{question_prefix}"
    # A transcription can repeat a label (one English item reads A, B, C, B) or miss the answer's.
    labels = [option["label"] for option in row["options"]]
    if labels.count(row["answer"]) != 1:
        raise ValueError(f"doc {row['hash']}: answer {row['answer']!r} among labels {labels}")

    return MCDoc(
        id=row["hash"],
        choices=cloze(
            row["stem"].strip(),
            [option["text"].strip() for option in row["options"]],
            question_prefix=question_prefix,
            answer_prefix=answer_prefix,
            separator=separator,
        ),
        gold=labels.index(row["answer"]),
    )


def chinese_doc(row: dict[str, Any]) -> MCDoc:
    """A row in 005.D1's Chinese format: `問題：` and `答案：`, no space before the answer."""
    return exam_doc(
        row,
        question_prefix=CHINESE_QUESTION_PREFIX,
        answer_prefix=CHINESE_ANSWER_PREFIX,
        separator="",
    )


def english_doc(row: dict[str, Any]) -> MCDoc:
    """A row in OLMES's English format: `Question: ` and `Answer:`, a space before the answer."""
    return exam_doc(row, question_prefix="Question: ", answer_prefix="Answer:", separator=" ")


def subject_task(subject: Subject) -> MultipleChoiceTask:
    """The subject's task: a header, the subject's 5 examples, then the test question. 英文 takes OLMES's MMLU header and PMI context, the others TMMLU+'s (009.D3)."""
    # The one place the format is chosen: the header, the prefixes and the PMI context together.
    if subject.name == "english":
        description = (
            "The following are multiple choice questions (with answers) about English.\n\n"
        )
        to_doc, unconditional_context = english_doc, "Answer:"
    else:
        description = f"以下是關於{subject.chinese_name}考試單選題，請選出正確的答案。\n\n"
        to_doc, unconditional_context = chinese_doc, CHINESE_ANSWER_PREFIX

    return MultipleChoiceTask(
        name=f"tw_mock_exams_{subject.name}",
        # Private: only members of the Cerulean organization can run these tasks (009.D1).
        dataset=Dataset(
            repo="cerulean-works/tw-mock-exams",
            revision="e62f9adf374da2e0fcd0095428f6339da980f020",
            subset=subject.name,
            split="test",
        ),
        to_doc=to_doc,
        # As TMMLU+ and OLMES's MMLU.
        primary_metric="acc_per_char",
        fewshot=Fewshot(split="dev", ids=subject.shots),
        description=description,
        unconditional_context=unconditional_context,
        keep=scored,
    )


SUBJECTS = (
    Subject(
        "english",
        "英文",
        (
            "289ff57fcd9d18a74e55e0f899e3d1f651fddfe0",
            "d1fd2ea9289dca1488a6335c1233fccad7b82ed8",
            "40d360663dcebef4a0a63b79076d4193ebdab431",
            "64598a1655a4aa05f32cd00dc78a1946e0fdfa76",
            "9db56e227177c6fa2165ddb8f6f664127ad7b26d",
        ),
    ),
    Subject(
        "social",
        "社會",
        (
            "2aa5699b6db62c82280661ce61c806dc3251b315",
            "694199a43cc28dd3daaf1ad68f77507933bc646f",
            "2e58cf6b63df690ce5b91ac91017d4b6b54da491",
            "2ddc69fd921ac3024907ef2f6e7188ac1cf4375a",
            "4fe007a5737b1fd9948fad3bd1020e16723f4949",
        ),
    ),
    Subject(
        "chinese",
        "國文",
        (
            "fef593d1abd9e7cfb84798cf22586796e850cb50",
            "ffa02cef4f86ae9a2a2bdb3b92a695b3d64661fb",
            "0abc7225af6e278d65ed12e94edc7644bb44181f",
            "22b37a1f07c565cd6b1b3c9224e1c858911b8189",
            "c1059e62c381b099e026828e7f32c90df3cf8a42",
        ),
    ),
    Subject(
        "science",
        "自然",
        (
            "ae1a7be761c13539c715eb5c14f7f6e3558e9ddc",
            "19ad6509c5a2925668e2350634c4abb627564bc0",
            "4dcf6784904682b1f70842cf9686628ebf3154a6",
            "fbb47345030de77596714b949f6978f3f55c708f",
            "c47082f0205cadd982b8dde1756429d80219c07e",
        ),
    ),
    Subject(
        "history",
        "歷史",
        (
            "10945c083c03de5ab7faa69b5e7bbd5079682a2d",
            "e46ae5d27fb3bca62d0585e843a2e9b37b1c0730",
            "7a38724025450e08df9a1d0f0a75f3a9f46fafa1",
            "7ad0371fd3f519cf2165b24f913d44f1d6f87625",
            "c8ca058988fbab57e2c53dd36fab2cdea43e744d",
        ),
    ),
    # 公民 is the short name of 公民與社會, and the dataset holds both under `civics`.
    Subject(
        "civics",
        "公民與社會",
        (
            "b53a987ba53facf0f2e1d3a1f50c9c50f0b48840",
            "e0185c8eb645296bf3a3164c3a348346c4553326",
            "35cb837d56e530b68a27092942a596b377c0d9ad",
            "28bd416d24a384564fc79489f7724f9843508124",
            "7ea34b551c8cc0a30e9cd4fc7b1fa3cec6e8f1c6",
        ),
    ),
    Subject(
        "biology",
        "生物",
        (
            "5aaf1f88d1d40eb9e0bbe88fa02355a6d01daf25",
            "b5814fe35fe7f2ad58970109ea67d6e6ed9aa3b5",
            "7314ee624aedf49caac11c220d1c37ed253328c0",
            "c97e945adf6deb22fa02419d6030beb4c82cbf43",
            "f2782470f27502d87e115551414f5241560f023c",
        ),
    ),
    Subject(
        "geography",
        "地理",
        (
            "3615095b6908e9222cfc256228b3db4e5f910493",
            "d4445a818e2460330340001e33376212027b43f6",
            "7e3958ff2500e8e9137598089514b9ed87793269",
            "d31979155bb52b4959226ebd23ec8aab798e4274",
            "e6fc1976a7549afa3d5d95a5b644ed66cccc525a",
        ),
    ),
    Subject(
        "physics",
        "物理",
        (
            "6af08f7da27d3680b34d1f2f113bd3193d702d0e",
            "096805c9fe57414f6a82214863724aeddb99def6",
            "5777a2b06f272390e125cfa4d4d1373f12aa7866",
            "9c85a4ba130033bfdb2749f79b34b99a0a3735a3",
            "282d8d6092d2f7fbff6a4e2aeba1cde7c101f28e",
        ),
    ),
    Subject(
        "chemistry",
        "化學",
        (
            "0cacdb6f844b413c092322fbe65101fb6c2ed652",
            "a4571f1888d156aed4e7f2169b5ed6cabce97732",
            "1ae35eb8ed59ae0539be6b9b26c1fb789cd365ef",
            "e4b4c22359daaeada5db785017bce9708ccaca7c",
            "0c4ca7574fa4749a553cfea7f78af5a6550dd653",
        ),
    ),
)

TW_MOCK_EXAMS_TASKS = tuple(map(subject_task, SUBJECTS))

# The subjects' unweighted mean, as OLMES averages MMLU's (009.D5).
TW_MOCK_EXAMS = Suite("tw_mock_exams", TW_MOCK_EXAMS_TASKS)
TW_MOCK_EXAMS_BPB = Suite("tw_mock_exams:bpb", TW_MOCK_EXAMS_TASKS, metric="bpb")
# An accuracy and a `bpb` don't average, so the group that runs both has no score.
TW_MOCK_EXAMS_ALL = Suite("tw_mock_exams:all", (TW_MOCK_EXAMS, TW_MOCK_EXAMS_BPB), scored=False)
