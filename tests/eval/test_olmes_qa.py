from flock.eval.tasks import SUITES, TASKS, Choice, MCDoc, cloze
from flock.eval.tasks.hellaswag import hellaswag_doc
from flock.eval.tasks.mmlu import MMLU_TASKS, SUBJECTS, mmlu_doc
from flock.eval.tasks.piqa import piqa_doc
from flock.eval.tasks.socialiqa import socialiqa_doc
from flock.eval.tasks.winogrande import WINOGRANDE, winogrande_doc

# Each expected string below is what OLMES's `_process_doc` and `construct_requests` give for
# the same row at commit 5a51f50.


def test_hellaswag_strips_wikihow_markup_and_capitalizes_like_olmes():
    row = {
        "index": 3,
        "activity_label": "Home and Garden",
        "ctx_a": "[header] How to plant tulips [title] Pick a spot.",
        "ctx_b": "dig a HOLE",
        "endings": ["[step] Water it.  Then wait.", " Sing."],
        "label": "0",
    }
    context = "Home and Garden: How to plant tulips. Pick a spot. Dig a hole"

    # Removing `[step]` after stripping leaves a space, so that choice starts with two, as in OLMES.
    assert hellaswag_doc(row) == MCDoc(
        "3", (Choice(context, "  Water it. Then wait."), Choice(context, " Sing.")), 0
    )


def test_winogrande_gives_each_option_its_own_context():
    row = {
        "index": 0,
        "sentence": "Sarah was a much better surgeon than Maria so _ always got the easier cases.",
        "option1": "Sarah",
        "option2": "Maria",
        "answer": "2",
    }
    rest = " always got the easier cases."
    before = "Sarah was a much better surgeon than Maria so "

    assert winogrande_doc(row) == MCDoc(
        "0", (Choice(before + "Sarah", rest), Choice(before + "Maria", rest)), 1
    )


def test_winogrande_shot_is_its_gold_sentence_and_has_no_pmi():
    shot = winogrande_doc(
        {"index": 0, "sentence": "A _ b.", "option1": "x", "option2": "y", "answer": "1"}
    )
    doc = winogrande_doc(
        {"index": 1, "sentence": "C _ d.", "option1": "x", "option2": "y", "answer": "2"}
    )

    built = WINOGRANDE.requests([doc], [shot])
    assert [(r.context, r.continuation) for r in built.requests] == [
        ("A x b.\n\nC x", " d."),
        ("A x b.\n\nC y", " d."),
    ]
    assert "acc_uncond" not in WINOGRANDE.reported_metrics


def test_socialiqa_joins_context_and_question():
    row = {
        "index": 0,
        "context": "Tracy didn't go home that evening and resisted Riley's attacks.",
        "question": "What does Tracy need to do before this?",
        "answerA": "make a new plan",
        "answerB": "Go home and see Riley",
        "answerC": "Find somewhere to go",
        "label": "3",
    }
    question = (
        "Tracy didn't go home that evening and resisted Riley's attacks. "
        "What does Tracy need to do before this?"
    )

    assert socialiqa_doc(row) == MCDoc(
        "0",
        cloze(question, ("make a new plan", "Go home and see Riley", "Find somewhere to go")),
        2,
    )


def test_piqa_uses_the_goal_prefix():
    row = {
        "index": 5,
        "goal": "Open a jar.",
        "sol1": "Twist the lid.",
        "sol2": "Eat it.",
        "label": 0,
    }

    doc = piqa_doc(row)
    assert doc.id == "5"
    assert doc.gold == 0
    assert doc.choices[1] == Choice("Goal: Open a jar.\nAnswer:", " Eat it.")


def test_mmlu_subject_header_comes_before_the_shots():
    task = TASKS["mmlu_high_school_us_history"]
    shot = mmlu_doc({"index": 0, "question": "1 + 1?", "choices": ["1", "2"], "answer": 1})
    doc = mmlu_doc({"index": 7, "question": "2 + 2?", "choices": ["4", "5"], "answer": 0})

    built = task.requests([doc], [shot])
    assert doc.id == "7"
    assert built.requests[0].context == (
        "The following are multiple choice questions (with answers) about "
        "high school us history.\n\nQuestion: 1 + 1?\nAnswer: 2\n\nQuestion: 2 + 2?\nAnswer:"
    )


def test_mmlu_has_one_task_per_subject_under_both_suites():
    assert len(set(SUBJECTS)) == len(MMLU_TASKS) == 57
    assert SUITES["mmlu"].tasks == SUITES["mmlu:bpb"].tasks == MMLU_TASKS


def test_suite_scores_average_mmlu_first():
    metrics = {
        task.name: {"acc_per_char": 0.0, "acc_uncond": 0.0, "acc_raw": 0.0}
        for task in TASKS.values()
    }
    for task in MMLU_TASKS:
        metrics[task.name]["acc_per_char"] = 1.0

    # Seven tasks score 0 and MMLU, one member however many subjects, scores 1.
    assert SUITES["base_easy:qa_rc"].score(metrics) == 1 / 8
