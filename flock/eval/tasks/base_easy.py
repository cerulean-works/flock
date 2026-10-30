"""OLMo 3's Base Easy suites (OLMo 3, 2512.13961), with the tasks that exist so far."""

from flock.eval.tasks.arc import ARC_CHALLENGE, ARC_EASY
from flock.eval.tasks.basic_skills import BASIC_SKILLS, BASIC_SKILLS_BPB
from flock.eval.tasks.csqa import CSQA
from flock.eval.tasks.gen2mc import COQA, DROP, JEOPARDY, NATURALQS, SQUAD
from flock.eval.tasks.hellaswag import HELLASWAG
from flock.eval.tasks.humaneval import HUMANEVAL
from flock.eval.tasks.lab_bench import LAB_BENCH_DBQA, LAB_BENCH_PROTOCOLQA
from flock.eval.tasks.lambada import LAMBADA
from flock.eval.tasks.mbpp import MBPP, MT_MBPP
from flock.eval.tasks.medical import MEDMCQA, MEDQA_EN
from flock.eval.tasks.minerva_math import MINERVA_MATH_TASKS
from flock.eval.tasks.mmlu import MMLU, MMLU_BPB
from flock.eval.tasks.piqa import PIQA
from flock.eval.tasks.sciq import SCIQ
from flock.eval.tasks.socialiqa import SOCIALIQA
from flock.eval.tasks.suite import Suite
from flock.eval.tasks.winogrande import WINOGRANDE
from flock.eval.tasks.yesno import QASPER_YESNO, SCIRIFF_YESNO

# OLMES's `olmo3:base_easy:qa_rc` and `olmo3:base_easy:qa_bpb` (`oe_eval/configs/task_suites.py`):
# the same tasks, read by their primary metric and by `bpb`, so a run scores each task once.
# MMLU and basic skills are one member each, through a suite of each kind. OLMES lists ARC as
# one suite with no metric of its own, which by our reading leaves its suite unscored, so we
# count ARC-Easy and ARC-Challenge as two members (stage file 003, D18).
_QA_TASKS = (
    ARC_EASY,
    ARC_CHALLENGE,
    CSQA,
    HELLASWAG,
    WINOGRANDE,
    SOCIALIQA,
    PIQA,
    SCIQ,
    QASPER_YESNO,
    SCIRIFF_YESNO,
    LAB_BENCH_DBQA,
    LAB_BENCH_PROTOCOLQA,
    MEDMCQA,
    MEDQA_EN,
    COQA,
    DROP,
    JEOPARDY,
    NATURALQS,
    SQUAD,
)

# LAMBADA is read by `acc_greedy` in `qa_rc` and by `bpb` in `qa_bpb`, as OLMES's `lambada` and
# `lambada:bpb`.
QA_RC = Suite("base_easy:qa_rc", (*_QA_TASKS, MMLU, BASIC_SKILLS, LAMBADA))
QA_BPB = Suite("base_easy:qa_bpb", (*_QA_TASKS, MMLU_BPB, BASIC_SKILLS_BPB, LAMBADA), metric="bpb")

# OLMES's `olmo3:base_easy:math_bpb` and `olmo3:base_easy:code_bpb`. Every task in them reports
# only `bpb`, and `mt_mbpp`, a suite of 17 languages, is one member of `code_bpb`.
MATH_BPB = Suite("base_easy:math_bpb", MINERVA_MATH_TASKS, metric="bpb")
CODE_BPB = Suite("base_easy:code_bpb", (HUMANEVAL, MBPP, MT_MBPP), metric="bpb")

# OLMES has no score over all of Base Easy: an accuracy and a `bpb` don't average.
BASE_EASY = Suite("base_easy", (QA_RC, QA_BPB, MATH_BPB, CODE_BPB), scored=False)
