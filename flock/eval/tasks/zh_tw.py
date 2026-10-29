"""The Traditional Chinese suites, one number of each kind next to Base Easy's."""

from flock.eval.tasks.drcd import DRCD_BPB
from flock.eval.tasks.suite import Suite
from flock.eval.tasks.tmmluplus import TMMLUPLUS, TMMLUPLUS_BPB
from flock.eval.tasks.tw_mock_exams import TW_MOCK_EXAMS, TW_MOCK_EXAMS_BPB

# No reference suite exists: OLMo 3's Base Easy is English only, and TMLU and TMMLU+ each average
# only their own subjects. Each benchmark is one member through its own suite, so it counts once
# however many subjects it has, as `mmlu` does in `base_easy:qa_rc` (stage file 017).
ZH_TW = Suite("zh_tw", (TMMLUPLUS, TW_MOCK_EXAMS))
# `drcd_bpb` reports only `bpb`, so it is in the `bpb` suite alone.
ZH_TW_BPB = Suite("zh_tw:bpb", (TMMLUPLUS_BPB, DRCD_BPB, TW_MOCK_EXAMS_BPB), metric="bpb")
