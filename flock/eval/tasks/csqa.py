"""CommonsenseQA (Talmor et al. 2019, 1811.00937): questions over ConceptNet relations."""

from flock.eval.tasks.arc import arc_doc
from flock.eval.tasks.base import Dataset, Fewshot, MultipleChoiceTask

CSQA = MultipleChoiceTask(
    name="csqa",
    dataset=Dataset(
        repo="tau/commonsense_qa",
        revision="94630fe30dad47192a8546eb75f094926d47e155",
        subset=None,
        # The test split has no answers.
        split="validation",
    ),
    # CommonsenseQA rows have ARC's fields, and OLMES renders both as cloze questions
    # (`oe_eval/tasks/oe_eval_tasks/csqa.py`).
    to_doc=arc_doc,
    primary_metric="acc_uncond",
    # The first 5 of OLMES's curated `FEWSHOT_SOURCES["OLMES:commonsense_qa"]`, all train rows.
    fewshot=Fewshot(
        split="train",
        ids=(
            "61fe6e879ff18686d7552425a36344c8",
            "02e821a3e53cb320790950aab4489e85",
            "23505889b94e880c3e89cff4ba119860",
            "a76403b4921a9281b6ee2a7241a5ec9f",
            "6dc921840aa1e5dda3333b79007f630b",
        ),
    ),
)
