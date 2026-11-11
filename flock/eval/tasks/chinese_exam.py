"""The Chinese exam format TMMLU+, C-Eval and CMMLU share: four-choice rows scored as Chinese cloze docs, one task per subject, after a header and the subject's dev rows (stage files 005 and 033)."""

from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from flock.eval.tasks.base import (
    CHINESE_ANSWER_PREFIX,
    Dataset,
    Fewshot,
    MCDoc,
    MultipleChoiceTask,
    cloze,
)
from flock.eval.tasks.suite import Suite


def exam_doc(row: dict[str, Any], question_prefix: str) -> MCDoc:
    """
    A row with a `question`, choices `A` to `D` and an `answer` letter as a Chinese cloze doc, identified by its row index as MMLU is in OLMES.

    The question and choices are stripped, since some carry outer whitespace in both the test and the dev rows. lm-evaluation-harness's `tmmluplus` strips the question too (`doc_to_text`, v0.4.3), and leaves its choices alone because it scores letters.

    Args:
        row (`dict[str, Any]`):
            The row, with the `index` `Dataset.load` numbers it by.
        question_prefix (`str`):
            `CHINESE_QUESTION_PREFIX` or `SIMPLIFIED_QUESTION_PREFIX`, in the benchmark's script.
    """
    letters = ("A", "B", "C", "D")

    return MCDoc(
        id=str(row["index"]),
        choices=cloze(
            row["question"].strip(),
            [row[letter].strip() for letter in letters],
            question_prefix=question_prefix,
            answer_prefix=CHINESE_ANSWER_PREFIX,
            separator="",
        ),
        # A tuple, not "ABCD": `str.index` would also find an empty or two-letter answer.
        gold=letters.index(row["answer"]),
    )


@dataclass(frozen=True, slots=True)
class Subject:
    """
    One subject of a Chinese exam benchmark.

    Args:
        name (`str`):
            The dataset config, which names the task `<benchmark>_<name>` as lm-evaluation-harness does.
        chinese_name (`str`):
            The subject in the task's header, from lm-evaluation-harness's subject table or per-subject config (v0.4.3).
        dev_rows (`int`, *optional*, defaults to `5`):
            The subject's dev rows, all of which every doc is shown as few-shot examples, except a dev row that repeats the doc's question and answer. Twelve TMMLU+ subjects have fewer than 5 after v1.1's corrections, and they show what they have. No reference handles a short dev split (lm-evaluation-harness's `first_n` sampler asserts at 5-shot on them), so this is our choice, 005.D4 in its stage file.
        china_specific (`bool`, *optional*, defaults to `False`):
            Whether CMMLU's `src/categories.py` tags the subject China specific.
        keep (`Callable[[dict], bool]`, *optional*):
            The task's `keep`, for a subject with a test row no model can score (033.D8). Every row if not set.
    """

    name: str
    chinese_name: str
    dev_rows: int = 5
    china_specific: bool = False
    keep: Callable[[dict[str, Any]], bool] | None = None


@dataclass(frozen=True, slots=True)
class Category:
    """
    The subjects of one of a benchmark's own categories.

    Args:
        name (`str`):
            The category, which names its suite `<benchmark>:<name>`.
        subjects (`tuple[Subject, ...]`):
            Its subjects, in the order its suite holds their tasks.
    """

    name: str
    subjects: tuple[Subject, ...]


# Not slotted, so `tasks` and the suites can be cached on the instance.
@dataclass(frozen=True)
class Benchmark:
    """
    A Chinese exam benchmark with one config per subject. Each subject is scored in 005's Chinese cloze format, after the benchmark's header and the subject's dev rows in file order, so the benchmarks differ only in data.

    Args:
        name (`str`):
            The prefix of its task names and the name of its suite.
        repo (`str`):
            The dataset repo, with one config per subject.
        revision (`str`):
            The repo's commit.
        fewshot_split (`str`):
            The split holding each subject's dev rows. The test rows are always the `test` split.
        header (`str`):
            The paper's header, with `{}` for the subject's Chinese name.
        to_doc (`Callable[[dict], MCDoc]`):
            Turns one row into a doc. A module-level function, since the task snapshot (012) pins it by name.
        categories (`tuple[Category, ...]`):
            The subjects by the benchmark's own categories.
        averages_categories (`bool`, *optional*, defaults to `False`):
            Whether the benchmark's score is the mean of its category means, as TMMLU+'s card computes its total (005), rather than the mean of its subjects, as C-Eval and CMMLU report (C-Eval Table 3 caption, CMMLU Table 1 caption; 033.D3).
    """

    name: str
    repo: str
    revision: str
    fewshot_split: str
    header: str
    to_doc: Callable[[dict[str, Any]], MCDoc]
    categories: tuple[Category, ...]
    averages_categories: bool = False

    @cached_property
    def tasks(self) -> dict[str, MultipleChoiceTask]:
        """Every subject's task by subject name: the header, the subject's dev rows in file order as lm-evaluation-harness's `tmmluplus`, `ceval` and `cmmlu` show them (`first_n`), then the test question. Built once, so every suite and the registry hold the same objects."""
        tasks = {}
        for category in self.categories:
            for subject in category.subjects:
                tasks[subject.name] = MultipleChoiceTask(
                    name=f"{self.name}_{subject.name}",
                    dataset=Dataset(
                        repo=self.repo,
                        revision=self.revision,
                        subset=subject.name,
                        split="test",
                        row_index=True,
                    ),
                    to_doc=self.to_doc,
                    # As OLMES scores MMLU (`mmlu_<subject>:rc::olmes` in
                    # `oe_eval/configs/tasks.py`).
                    primary_metric="acc_per_char",
                    fewshot=Fewshot(
                        split=self.fewshot_split,
                        ids=tuple(str(row) for row in range(subject.dev_rows)),
                    ),
                    description=self.header.format(subject.chinese_name) + "\n\n",
                    unconditional_context=CHINESE_ANSWER_PREFIX,
                    keep=subject.keep,
                )

        return tasks

    @cached_property
    def suite(self) -> Suite:
        """The benchmark's suite `<name>`. Built once, so every group holds the same object."""
        return self._scored_suite(None)

    @cached_property
    def bpb_suite(self) -> Suite:
        """The `bpb` twin of `suite`, `<name>:bpb`."""
        return self._scored_suite("bpb")

    def _scored_suite(self, metric: str | None) -> Suite:
        """The mean of the subjects under `metric`, or of the category suites if the benchmark `averages_categories`."""
        suffix = f":{metric}" if metric else ""
        if not self.averages_categories:
            return Suite(f"{self.name}{suffix}", tuple(self.tasks.values()), metric=metric)

        members = []
        for category in self.categories:
            tasks = tuple(self.tasks[subject.name] for subject in category.subjects)
            members.append(Suite(f"{self.name}:{category.name}{suffix}", tasks, metric=metric))

        return Suite(f"{self.name}{suffix}", tuple(members), metric=metric)

    def all_suite(self, *more: Suite) -> Suite:
        """The unscored `<name>:all` group: the benchmark's suite, each category's unless the benchmark's suite holds them, and the China specific subjects' if any, then the `bpb` twin of each, then `more`."""
        groups: dict[str, tuple[MultipleChoiceTask, ...]] = {}
        china_specific = []
        for category in self.categories:
            tasks = tuple(self.tasks[subject.name] for subject in category.subjects)
            if not self.averages_categories:
                groups[category.name] = tasks
            china_specific.extend(self.tasks[s.name] for s in category.subjects if s.china_specific)
        if china_specific:
            groups["china_specific"] = tuple(china_specific)

        accuracy = [self.suite]
        bpb = [self.bpb_suite]
        for group, tasks in groups.items():
            accuracy.append(Suite(f"{self.name}:{group}", tasks))
            bpb.append(Suite(f"{self.name}:{group}:bpb", tasks, metric="bpb"))

        return Suite(f"{self.name}:all", (*accuracy, *bpb, *more), scored=False)
