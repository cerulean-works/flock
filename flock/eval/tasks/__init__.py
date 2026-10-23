"""Task and suite definitions, and the registries of both by name."""

from flock.eval.tasks.base import (
    Choice,
    Dataset,
    Fewshot,
    MCDoc,
    MCRequests,
    MultipleChoiceTask,
    Truncation,
    cloze,
)
from flock.eval.tasks.registry import SUITES, TASKS
from flock.eval.tasks.suite import Suite

__all__ = [
    "SUITES",
    "TASKS",
    "Choice",
    "Dataset",
    "Fewshot",
    "MCDoc",
    "MCRequests",
    "MultipleChoiceTask",
    "Suite",
    "Truncation",
    "cloze",
]
