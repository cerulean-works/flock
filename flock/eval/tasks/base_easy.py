"""OLMo 3's Base Easy suites (OLMo 3, 2512.13961), with the tasks that exist so far."""

from flock.eval.tasks.arc import ARC_EASY
from flock.eval.tasks.suite import Suite

# OLMES's `olmo3:base_easy:qa_rc` and `olmo3:base_easy:qa_bpb` (`oe_eval/configs/task_suites.py`):
# the same tasks, read by their primary metric and by `bpb`, so a run scores each task once.
_QA_TASKS = (ARC_EASY,)

QA_RC = Suite("base_easy:qa_rc", _QA_TASKS)
QA_BPB = Suite("base_easy:qa_bpb", _QA_TASKS, metric="bpb")

# OLMES has no score over all of Base Easy: an accuracy and a `bpb` don't average.
BASE_EASY = Suite("base_easy", (QA_RC, QA_BPB), scored=False)
