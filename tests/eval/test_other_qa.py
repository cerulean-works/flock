import dataclasses

import datasets
import huggingface_hub
import pytest

from flock.eval.tasks import SUITES, TASKS, Choice, Dataset, Fewshot, MCDoc, cloze
from flock.eval.tasks.basic_skills import SHOT_IDS, basic_skills_doc
from flock.eval.tasks.lab_bench import lab_bench_doc
from flock.eval.tasks.lambada import LAMBADA, lambada_doc
from flock.eval.tasks.medical import MEDQA_EN, medmcqa_doc, medqa_doc
from flock.eval.tasks.sciq import sciq_doc
from flock.eval.tasks.yesno import qasper_doc, sciriff_doc

# Each expected doc below is what OLMES's `_process_doc` and `doc_to_text` give for the same row
# at commit 5a51f50.

# The real `Dataset.load`, read before the `offline_rows` fixture replaces it in each test.
LOAD = Dataset.load


def test_sciq_puts_the_correct_answer_last():
    row = {
        "index": 4,
        "question": "What do plants take in?",
        "distractor1": "oxygen",
        "distractor2": "helium",
        "distractor3": "neon",
        "correct_answer": "carbon dioxide",
    }

    assert sciq_doc(row) == MCDoc(
        "4", cloze("What do plants take in?", ("oxygen", "helium", "neon", "carbon dioxide")), 3
    )


def test_medical_docs_read_their_answer_index():
    medmcqa = {"id": "u-1", "question": "q?", "opa": "a", "opb": "b", "opc": "c", "opd": "d"}
    medqa = {"index": 2, "question": "q?", "choices": ["a", "b"], "answer_idx": 1}

    assert medmcqa_doc({**medmcqa, "cop": "2"}) == MCDoc(
        "u-1", cloze("q?", ("a", "b", "c", "d")), 2
    )
    assert medqa_doc(medqa) == MCDoc("2", cloze("q?", ("a", "b")), 1)
    assert "acc_uncond" not in MEDQA_EN.reported_metrics


def test_lab_bench_puts_the_ideal_answer_last():
    row = {"id": "x", "question": "Which gene?", "distractors": ["A1", "B2"], "ideal": "C3"}

    assert lab_bench_doc(row) == MCDoc("x", cloze("Which gene?", ("A1", "B2", "C3")), 2)


def test_yesno_joins_evidence_and_strips_the_prompt():
    qasper = {"id": "p_0", "evidence": ["One.", "Two."], "question": "Is it?", "answer": "No"}
    context = "One. Two.\nQuestion: Is it?\nAnswer:"

    assert qasper_doc(qasper) == MCDoc("p_0", (Choice(context, " Yes"), Choice(context, " No")), 1)
    # With no evidence, stripping the whole prompt drops its leading newline, as in OLMES.
    sciriff = {"id": "s:0", "context": "", "question": "Is it?", "answer": "Yes"}
    assert sciriff_doc(sciriff).choices[0].context == "Question: Is it?\nAnswer:"
    assert sciriff_doc(sciriff).gold == 0


def test_basic_skills_orders_choices_as_olmes_does():
    row = {
        "id": "0",
        "question": "4 * 8 =",
        "answer": "32",
        "wrong_answers": ["28", "30", "34", "36"],
    }

    doc = basic_skills_doc(row)
    # OLMES's `shuffle_and_insert(wrong_answers, answer, random.Random("0"))` for this row.
    assert [choice.continuation for choice in doc.choices] == [" 36", " 30", " 32", " 28", " 34"]
    assert doc.gold == 2
    assert doc.choices[2] == Choice("4 * 8 =", " 32")


def test_basic_skills_shots_skip_the_scored_doc():
    ids = SHOT_IDS["arithmetic"]
    row = {"question": "q", "answer": "a", "wrong_answers": ["b"]}
    shots = [basic_skills_doc({**row, "id": shot_id}) for shot_id in ids]
    scored = basic_skills_doc({**row, "id": ids[0]})

    # The scored doc is the first of the 6 examples, so it sees the other 5.
    built = TASKS["basic_skills_arithmetic"].requests([scored], shots)
    assert built.requests[0].context == "q a\n\n" * 5 + "q"


def test_lambada_scores_the_last_word_after_single_spaces():
    doc = lambada_doc({"index": 9, "text": "He said  hello\nto  Sacha"})

    assert doc == MCDoc("9", (Choice("He said hello to", " Sacha"),), 0)
    assert LAMBADA.reported_metrics == ("bpb",)


def test_data_file_reads_one_cached_json_file_at_the_revision(monkeypatch):
    calls = []

    def download(*args, **kwargs):
        calls.append((args, kwargs))
        return "/cache/skill/validation.json"

    def load_dataset(*args, **kwargs):
        calls.append((args, kwargs))
        return datasets.Dataset.from_list([{"id": "0"}])

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", download)
    monkeypatch.setattr(datasets, "load_dataset", load_dataset)
    dataset = Dataset("org/repo", "abc", None, "validation", data_file="skill/validation.json")

    assert LOAD(dataset) == [{"id": "0"}]
    assert calls == [
        (("org/repo", "skill/validation.json"), {"repo_type": "dataset", "revision": "abc"}),
        (
            ("json",),
            {"data_files": {"validation": "/cache/skill/validation.json"}, "split": "validation"},
        ),
    ]


def test_data_file_task_rejects_shots_from_another_split():
    task = TASKS["basic_skills_arithmetic"]
    with pytest.raises(ValueError, match="holds only the 'validation' split"):
        dataclasses.replace(task, fewshot=Fewshot("train", ("1", "2")))


def test_data_file_takes_no_subset():
    with pytest.raises(ValueError, match="data_file takes no subset"):
        Dataset("org/repo", "abc", "skill", "validation", data_file="skill/validation.json")


def test_registry_suites_list_exactly_their_members():
    qa = [
        "arc_easy",
        "sciq",
        "qasper_yesno",
        "sciriff_yesno",
        "lab_bench_dbqa",
        "lab_bench_protocolqa",
        "medmcqa",
        "medqa_en",
    ]

    assert [m.name for m in SUITES["base_easy:qa_rc"].members] == [*qa, "basic_skills"]
    # LAMBADA joins `qa_rc` with greedy match (stage file 003, D20).
    assert [m.name for m in SUITES["base_easy:qa_bpb"].members] == [
        *qa,
        "basic_skills:bpb",
        "lambada",
    ]
    assert [task.name for task in SUITES["basic_skills"].members] == [
        f"basic_skills_{skill}"
        for skill in (
            "arithmetic",
            "coding",
            "common_knowledge",
            "logical_reasoning",
            "string_operations",
            "pattern",
        )
    ]
    assert SUITES["basic_skills:bpb"].members == SUITES["basic_skills"].members
