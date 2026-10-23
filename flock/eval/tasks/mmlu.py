"""MMLU (Hendrycks et al. 2021, 2009.03300): 57 subjects of exam questions, one task each."""

from typing import Any

from flock.eval.tasks.base import Dataset, Fewshot, MCDoc, MultipleChoiceTask, cloze
from flock.eval.tasks.suite import Suite

SUBJECTS = (
    "abstract_algebra",
    "anatomy",
    "astronomy",
    "business_ethics",
    "clinical_knowledge",
    "college_biology",
    "college_chemistry",
    "college_computer_science",
    "college_mathematics",
    "college_medicine",
    "college_physics",
    "computer_security",
    "conceptual_physics",
    "econometrics",
    "electrical_engineering",
    "elementary_mathematics",
    "formal_logic",
    "global_facts",
    "high_school_biology",
    "high_school_chemistry",
    "high_school_computer_science",
    "high_school_european_history",
    "high_school_geography",
    "high_school_government_and_politics",
    "high_school_macroeconomics",
    "high_school_mathematics",
    "high_school_microeconomics",
    "high_school_physics",
    "high_school_psychology",
    "high_school_statistics",
    "high_school_us_history",
    "high_school_world_history",
    "human_aging",
    "human_sexuality",
    "international_law",
    "jurisprudence",
    "logical_fallacies",
    "machine_learning",
    "management",
    "marketing",
    "medical_genetics",
    "miscellaneous",
    "moral_disputes",
    "moral_scenarios",
    "nutrition",
    "philosophy",
    "prehistory",
    "professional_accounting",
    "professional_law",
    "professional_medicine",
    "professional_psychology",
    "public_relations",
    "security_studies",
    "sociology",
    "us_foreign_policy",
    "virology",
    "world_religions",
)


def mmlu_doc(row: dict[str, Any]) -> MCDoc:
    """An MMLU row as a cloze doc, identified by its row index as in OLMES (`oe_eval/tasks/oe_eval_tasks/mmlu.py`)."""
    return MCDoc(
        id=str(row["index"]), choices=cloze(row["question"], row["choices"]), gold=row["answer"]
    )


# One task per subject, as OLMES's `mmlu_<subject>:rc::olmes`: the subject's header, then its
# first 5 `dev` rows in order (`GenericMMLU.fewshot_examples`), then the test question.
MMLU_TASKS = tuple(
    MultipleChoiceTask(
        name=f"mmlu_{subject}",
        dataset=Dataset(
            repo="cais/mmlu",
            revision="c30699e8356da336a370243923dbaf21066bb9fe",
            subset=subject,
            split="test",
            row_index=True,
        ),
        to_doc=mmlu_doc,
        primary_metric="acc_per_char",
        fewshot=Fewshot(split="dev", ids=("0", "1", "2", "3", "4")),
        description=(
            "The following are multiple choice questions (with answers) about "
            f"{subject.replace('_', ' ')}.\n\n"
        ),
    )
    for subject in SUBJECTS
)

# OLMES's `mmlu:rc::olmes` and `mmlu:rc:bpb::olmes` (`oe_eval/configs/task_suites.py`): MMLU
# counts as one member of the Base Easy suites, its subjects averaged first.
MMLU = Suite("mmlu", MMLU_TASKS)
MMLU_BPB = Suite("mmlu:bpb", MMLU_TASKS, metric="bpb")
