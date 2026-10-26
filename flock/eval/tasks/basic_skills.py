"""OLMo 3's basic skills (OLMo 3, 2512.13961): six short tests of arithmetic, code, facts, logic, strings and patterns."""

import random
from typing import Any

from flock.eval.tasks.base import Choice, Dataset, Fewshot, MCDoc, MultipleChoiceTask
from flock.eval.tasks.suite import Suite


def basic_skills_doc(row: dict[str, Any]) -> MCDoc:
    """A basic skills row as a doc: the raw question, then each answer, with no prefixes."""
    # OLMES's `shuffle_and_insert`: shuffle the wrong answers, then insert the right one at a
    # random place, both with a generator seeded by the doc's id. Seeding with a string gives the
    # same numbers on every Python version since 3.2, so each doc has one fixed order.
    rng = random.Random(row["id"])
    answers = list(row["wrong_answers"])
    rng.shuffle(answers)
    gold = rng.randint(0, len(answers))
    answers.insert(gold, row["answer"])

    return MCDoc(
        id=row["id"],
        choices=tuple(Choice(row["question"], f" {answer}") for answer in answers),
        gold=gold,
    )


# Each skill's validation split holds 6 examples drawn by OLMES's `Random(1234).sample(docs, 6)`,
# the same for every doc, from the split it scores. A doc shows the first 5 that aren't itself
# (OLMES's `fewshot_examples` and `fewshot_context`). Drawn once with OLMES's code.
SHOT_IDS = {
    "arithmetic": ("903", "240", "15", "186", "71", "172"),
    "coding": ("989", "796", "451", "119", "7", "92"),
    "common_knowledge": ("902", "239", "15", "185", "71", "171"),
    "logical_reasoning": ("902", "239", "15", "185", "71", "171"),
    "string_operations": ("902", "239", "15", "185", "1192", "71"),
    "pattern": ("451", "119", "7", "92", "35", "85"),
}

# One task per skill, as OLMES's `basic_skills_<skill>:rc::olmes`.
BASIC_SKILLS_TASKS = tuple(
    MultipleChoiceTask(
        name=f"basic_skills_{skill}",
        dataset=Dataset(
            repo="allenai/basic-skills",
            revision="faf63e9719e124d7741519a024719c8992622630",
            subset=None,
            split="validation",
            # The repo's loading script reads this one file per skill.
            data_file=f"{skill}/validation.json",
        ),
        to_doc=basic_skills_doc,
        # OLMES's primary metric. Its own `acc_per_token` equals its `acc_raw`, since it counts
        # one token per continuation, so parity compares our `acc_raw` (stage file 003, D22).
        primary_metric="acc_per_token",
        fewshot=Fewshot(split="validation", ids=ids, num_shots=5),
    )
    for skill, ids in SHOT_IDS.items()
)

# OLMES's `basic_skills:rc::olmes` and `basic_skills:rc:bpb::olmes`: one member of the Base Easy
# suites, its skills averaged first.
BASIC_SKILLS = Suite("basic_skills", BASIC_SKILLS_TASKS)
BASIC_SKILLS_BPB = Suite("basic_skills:bpb", BASIC_SKILLS_TASKS, metric="bpb")
