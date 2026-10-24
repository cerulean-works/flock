"""Task and suite definitions, and the registries of both by name."""

from flock.eval.tasks.base import (
    CHINESE_ANSWER_PREFIX,
    CHINESE_QUESTION_PREFIX,
    Choice,
    Dataset,
    Fewshot,
    GoldOnlyTask,
    MCDoc,
    MCRequests,
    MultipleChoiceTask,
    Truncation,
    cloze,
)
from flock.eval.tasks.registry import SUITES, TASKS
from flock.eval.tasks.suite import Suite

__all__ = [
    "CHINESE_ANSWER_PREFIX",
    "CHINESE_QUESTION_PREFIX",
    "SUITES",
    "TASKS",
    "Choice",
    "Dataset",
    "Fewshot",
    "GoldOnlyTask",
    "MCDoc",
    "MCRequests",
    "MultipleChoiceTask",
    "Suite",
    "Truncation",
    "cloze",
]
