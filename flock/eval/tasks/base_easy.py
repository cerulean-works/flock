"""OLMo 3's Base Easy suites (OLMo 3, 2512.13961), with the tasks that exist so far."""

from flock.eval.tasks.arc import ARC_EASY
from flock.eval.tasks.basic_skills import BASIC_SKILLS, BASIC_SKILLS_BPB
from flock.eval.tasks.gen2mc import COQA, DROP, JEOPARDY, NATURALQS, SQUAD
from flock.eval.tasks.lab_bench import LAB_BENCH_DBQA, LAB_BENCH_PROTOCOLQA
from flock.eval.tasks.lambada import LAMBADA
from flock.eval.tasks.medical import MEDMCQA, MEDQA_EN
from flock.eval.tasks.sciq import SCIQ
from flock.eval.tasks.suite import Suite
from flock.eval.tasks.yesno import QASPER_YESNO, SCIRIFF_YESNO

# OLMES's `olmo3:base_easy:qa_rc` and `olmo3:base_easy:qa_bpb` (`oe_eval/configs/task_suites.py`):
# the same tasks, read by their primary metric and by `bpb`, so a run scores each task once.
# Basic skills is one member, through a suite of each kind.
_QA_TASKS = (
    ARC_EASY,
    COQA,
    DROP,
    JEOPARDY,
    NATURALQS,
    SQUAD,
    SCIQ,
    QASPER_YESNO,
    SCIRIFF_YESNO,
    LAB_BENCH_DBQA,
    LAB_BENCH_PROTOCOLQA,
    MEDMCQA,
    MEDQA_EN,
)

# LAMBADA's `qa_rc` member is greedy match, which isn't scored yet (stage file 003, D20), so it
# is only in `qa_bpb` for now.
QA_RC = Suite("base_easy:qa_rc", (*_QA_TASKS, BASIC_SKILLS))
QA_BPB = Suite("base_easy:qa_bpb", (*_QA_TASKS, BASIC_SKILLS_BPB, LAMBADA), metric="bpb")

# OLMES has no score over all of Base Easy: an accuracy and a `bpb` don't average.
BASE_EASY = Suite("base_easy", (QA_RC, QA_BPB), scored=False)
