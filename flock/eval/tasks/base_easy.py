"""OLMo 3's Base Easy suites (OLMo 3, 2512.13961), with the tasks that exist so far."""

from flock.eval.tasks.arc import ARC_CHALLENGE, ARC_EASY
from flock.eval.tasks.csqa import CSQA
from flock.eval.tasks.hellaswag import HELLASWAG
from flock.eval.tasks.mmlu import MMLU, MMLU_BPB
from flock.eval.tasks.piqa import PIQA
from flock.eval.tasks.socialiqa import SOCIALIQA
from flock.eval.tasks.suite import Suite
from flock.eval.tasks.winogrande import WINOGRANDE

# OLMES's `olmo3:base_easy:qa_rc` and `olmo3:base_easy:qa_bpb` (`oe_eval/configs/task_suites.py`):
# the same tasks, read by their primary metric and by `bpb`, so a run scores each task once.
# MMLU is one member, through a suite of each kind. OLMES lists ARC as one suite with no metric
# of its own; we count ARC-Easy and ARC-Challenge as two members.
_QA_TASKS = (ARC_EASY, ARC_CHALLENGE, CSQA, HELLASWAG, WINOGRANDE, SOCIALIQA, PIQA)

QA_RC = Suite("base_easy:qa_rc", (*_QA_TASKS, MMLU))
QA_BPB = Suite("base_easy:qa_bpb", (*_QA_TASKS, MMLU_BPB), metric="bpb")

# OLMES has no score over all of Base Easy: an accuracy and a `bpb` don't average.
BASE_EASY = Suite("base_easy", (QA_RC, QA_BPB), scored=False)
