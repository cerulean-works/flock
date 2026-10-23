"""Every task and suite `flock-eval` can run, by name."""

from flock.eval.tasks.arc import ARC_EASY
from flock.eval.tasks.base import MultipleChoiceTask
from flock.eval.tasks.base_easy import BASE_EASY
from flock.eval.tasks.suite import Suite

TASKS: dict[str, MultipleChoiceTask] = {task.name: task for task in (ARC_EASY,)}

SUITES: dict[str, Suite] = {suite.name: suite for suite in BASE_EASY.suites}
