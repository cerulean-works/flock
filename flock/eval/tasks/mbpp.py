"""MBPP (Austin et al. 2021, 2108.07732): short Python programs from a description, and its translation into 17 languages (`allenai/multilingual_mbpp`), scored on the gold code."""

from typing import Any

from flock.eval.tasks.base import Choice, Dataset, Fewshot, MCDoc, MultipleChoiceTask
from flock.eval.tasks.suite import Suite

LANGUAGES = (
    "bash",
    "c",
    "cpp",
    "csharp",
    "go",
    "haskell",
    "java",
    "javascript",
    "matlab",
    "php",
    "python",
    "r",
    "ruby",
    "rust",
    "scala",
    "swift",
    "typescript",
)

# Both datasets have a 10-row `prompt` split for examples. OLMES shuffles it with
# `Random(1234)` for every doc and shows the first 3 (`fewshot_context` in
# `oe_eval/tasks/oe_eval_tasks/codex_mbpp.py` and `multilingual_mbpp.py`). The two splits hold
# the same task ids in the same order, so the draw is the same.
_SHOTS = Fewshot(split="prompt", ids=("3", "9", "4"))


def mbpp_doc(row: dict[str, Any]) -> MCDoc:
    """An MBPP row as a gold-only doc in OLMES's `inloop_bpb` format: the description, an open code fence, then the code and the closing fence, with no leading space (`codex_mbpp.py`)."""
    code = row["code"].rstrip("\n").rstrip().replace("\r", "")

    return MCDoc(
        id=str(row["task_id"]),
        choices=(Choice(row["text"].strip() + "\n```python\n", code + "\n```"),),
        gold=0,
    )


def mt_mbpp_doc(row: dict[str, Any]) -> MCDoc:
    """A multilingual MBPP row as a gold-only doc, as `mbpp_doc` with the row's language on the fence and Windows line ends made `\\n` (`BaseMultilingualMbppFixed` in `multilingual_mbpp.py`)."""
    text = row["text"].replace("\r\n", "\n").strip()
    code = row["code"].replace("\r\n", "\n").strip()

    return MCDoc(
        id=str(row["task_id"]),
        choices=(Choice(f"{text}\n```{row['language']}\n", code + "\n```"),),
        gold=0,
    )


# OLMES's `mbpp:3shot:bpb::none`, whose primary metric is the gold code's `bits_per_byte_corr`
# (`oe_eval/configs/tasks.py`). Its `limit: 500` keeps the whole test split.
MBPP = MultipleChoiceTask(
    name="mbpp",
    dataset=Dataset(
        repo="google-research-datasets/mbpp",
        revision="4bb6404fdc6cacfda99d4ac4205087b89d32030c",
        subset="full",
        split="test",
    ),
    to_doc=mbpp_doc,
    primary_metric="bpb",
    fewshot=_SHOTS,
    # One choice, so an accuracy is always 1 and PMI has nothing to compare.
    unconditional_context=None,
    metrics=("bpb",),
)

# One task per language, as OLMES's `mt_mbpp_v2fix:<language>` (`oe_eval/configs/tasks.py`).
MT_MBPP_TASKS = tuple(
    MultipleChoiceTask(
        name=f"mt_mbpp_{language}",
        dataset=Dataset(
            repo="allenai/multilingual_mbpp",
            revision="c86b037e7d20e705f3595b1cbb9db993681ca181",
            subset=language,
            split="test",
        ),
        to_doc=mt_mbpp_doc,
        primary_metric="bpb",
        fewshot=_SHOTS,
        unconditional_context=None,
        metrics=("bpb",),
    )
    for language in LANGUAGES
)

# OLMES's `mt_mbpp_v2fix` (`oe_eval/configs/task_suites.py`): one member of `code_bpb`, its
# languages averaged first.
MT_MBPP = Suite("mt_mbpp", MT_MBPP_TASKS)
