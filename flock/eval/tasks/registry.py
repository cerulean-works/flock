"""Every task and suite `flock eval` can run, by name."""

from flock.eval.tasks.arc import ARC_EASY
from flock.eval.tasks.base_easy import BASE_EASY
from flock.eval.tasks.drcd import DRCD_BPB
from flock.eval.tasks.fineweb import FINEWEB
from flock.eval.tasks.fineweb_edu import FINEWEB_EDU
from flock.eval.tasks.paloma import PALOMA
from flock.eval.tasks.suite import Suite, Task, by_name
from flock.eval.tasks.tmmluplus import TMMLUPLUS_ALL
from flock.eval.tasks.tw_mock_exams import TW_MOCK_EXAMS_ALL
from flock.eval.tasks.tw_text import TW_TEXT
from flock.eval.tasks.zh_tw import ZH_TW, ZH_TW_BPB

SUITES: dict[str, Suite] = by_name(
    (
        *BASE_EASY.suites,
        *TMMLUPLUS_ALL.suites,
        *TW_MOCK_EXAMS_ALL.suites,
        *ZH_TW.suites,
        *ZH_TW_BPB.suites,
        *PALOMA.suites,
        *FINEWEB.suites,
        *TW_TEXT.suites,
    )
)

# Every task in a suite is registered, so `--task` runs what `--suite` does.
TASKS: dict[str, Task] = by_name(
    (
        ARC_EASY,
        *BASE_EASY.tasks,
        *TMMLUPLUS_ALL.tasks,
        *TW_MOCK_EXAMS_ALL.tasks,
        DRCD_BPB,
        *PALOMA.tasks,
        FINEWEB_EDU,
        *FINEWEB.tasks,
        *TW_TEXT.tasks,
    )
)
