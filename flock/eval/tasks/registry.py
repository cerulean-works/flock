"""Every task `flock-eval` can run, by name."""

from flock.eval.tasks.arc import ARC_EASY
from flock.eval.tasks.base import MultipleChoiceTask

TASKS: dict[str, MultipleChoiceTask] = {task.name: task for task in (ARC_EASY,)}
