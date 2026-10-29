"""Every task and suite `flock-eval` can run, by name."""

from flock.eval.tasks.arc import ARC_EASY
from flock.eval.tasks.base import MultipleChoiceTask
from flock.eval.tasks.base_easy import BASE_EASY
from flock.eval.tasks.drcd import DRCD_BPB
from flock.eval.tasks.suite import Suite, by_name
from flock.eval.tasks.tmmluplus import TMMLUPLUS_ALL

SUITES: dict[str, Suite] = by_name((*BASE_EASY.suites, *TMMLUPLUS_ALL.suites))

# Every task in a suite is registered, so `--task` runs what `--suite` does.
TASKS: dict[str, MultipleChoiceTask] = by_name(
    (ARC_EASY, *BASE_EASY.tasks, *TMMLUPLUS_ALL.tasks, DRCD_BPB)
)
