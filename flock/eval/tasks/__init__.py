"""Task definitions and the registry of tasks by name."""

from flock.eval.tasks.base import Dataset, Fewshot, MCDoc, MCRequests, MultipleChoiceTask
from flock.eval.tasks.registry import TASKS

__all__ = ["TASKS", "Dataset", "Fewshot", "MCDoc", "MCRequests", "MultipleChoiceTask"]
