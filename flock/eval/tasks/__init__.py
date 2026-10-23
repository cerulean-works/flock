"""Task definitions and the registry of tasks by name."""

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
from flock.eval.tasks.registry import TASKS

__all__ = [
    "TASKS",
    "Choice",
    "Dataset",
    "Fewshot",
    "MCDoc",
    "MCRequests",
    "MultipleChoiceTask",
    "Truncation",
    "cloze",
]
