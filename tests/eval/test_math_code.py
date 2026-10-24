from flock.eval.tasks import SUITES, TASKS, Choice, MCDoc
from flock.eval.tasks.humaneval import HUMANEVAL, humaneval_doc
from flock.eval.tasks.mbpp import LANGUAGES, MT_MBPP_TASKS, mbpp_doc, mt_mbpp_doc
from flock.eval.tasks.minerva_math import MINERVA_MATH_TASKS, SUBJECTS, minerva_doc

# Each expected string below is what OLMES's `fewshot_context` and `construct_requests` give
# for the same row at commit 5a51f50.


def test_minerva_scores_the_solution_after_one_space():
    doc = minerva_doc({"index": 4, "problem": "What is $1+1$?", "solution": "It is $\\boxed{2}$."})
    assert doc == MCDoc(
        "4", (Choice("Problem:\nWhat is $1+1$?\n\nSolution:", " It is $\\boxed{2}$."),), 0
    )

    # A solution that already starts with a space or a newline gets none added.
    assert (
        minerva_doc({"index": 5, "problem": "p", "solution": "\n2"}).choices[0].continuation
        == "\n2"
    )


def test_minerva_shots_are_olmes_literal_problems():
    task = TASKS["minerva_math_algebra"]
    doc = minerva_doc({"index": 0, "problem": "Find $x$.", "solution": "$x=1$."})
    assert task.fewshot is not None
    shots = [minerva_doc(row) for row in task.fewshot.rows]

    built = task.requests([doc], shots)
    assert len(shots) == 4
    assert built.requests[0].context.startswith(
        "Problem:\nFind the domain of the expression  $\\frac{\\sqrt{x-2}}{\\sqrt{5-x}}$.}"
        "\n\nSolution: The expressions inside each square root must be non-negative."
    )
    assert built.requests[0].context.endswith(
        "I hope it is correct.\n\nProblem:\nFind $x$.\n\nSolution:"
    )
    assert built.requests[0].continuation == " $x=1$."


def test_humaneval_shows_examples_with_olmes_extra_space():
    def row(number: int, body: str) -> dict:
        return {
            "task_id": f"HumanEval/{number}",
            "prompt": f"def f{number}():\n",
            "canonical_solution": body,
        }

    # The first example is the doc itself, so it is skipped and the next 3 are shown.
    shots = [humaneval_doc(row(n, f"    return {n}\n")) for n in (112, 29, 1, 23)]
    doc = shots[0]

    request = HUMANEVAL.requests([doc], shots).requests[0]
    assert request.context == (
        "def f29():\n     return 29\n\n\n"
        "def f1():\n     return 1\n\n\n"
        "def f23():\n     return 23\n\n\n"
        "def f112():\n"
    )
    # The scored solution is already indented, so it gets no space.
    assert request.continuation == "    return 112\n"


def test_mbpp_closes_the_fence_without_a_leading_space():
    row = {"task_id": 11, "text": " Add one. ", "code": "def f(x):\r\n  return x + 1 \r\n\n"}

    assert mbpp_doc(row) == MCDoc(
        "11", (Choice("Add one.\n```python\n", "def f(x):\n  return x + 1\n```"),), 0
    )


def test_mt_mbpp_uses_the_row_language_and_unix_line_ends():
    row = {
        "task_id": 11,
        "text": "Add one.\r\n",
        "code": "\nf() {\r\n  echo $(( $1 + 1 ))\r\n}\r\n",
        "language": "bash",
    }

    assert mt_mbpp_doc(row) == MCDoc(
        "11", (Choice("Add one.\n```bash\n", "f() {\n  echo $(( $1 + 1 ))\n}\n```"),), 0
    )


def test_math_and_code_suites_hold_every_subject_and_language():
    assert len(SUBJECTS) == len(MINERVA_MATH_TASKS) == 7
    assert len(LANGUAGES) == len(MT_MBPP_TASKS) == 17
    assert SUITES["base_easy:math_bpb"].members == MINERVA_MATH_TASKS
    assert [m.name for m in SUITES["base_easy:code_bpb"].members] == [
        "humaneval",
        "mbpp",
        "mt_mbpp",
    ]
    for task in (*MINERVA_MATH_TASKS, *SUITES["base_easy:code_bpb"].tasks):
        assert task.reported_metrics == ("bpb",)
        assert task.unconditional_context is None


def test_code_bpb_averages_the_languages_first():
    metrics = {task.name: {"bpb": 0.0} for task in SUITES["base_easy:code_bpb"].tasks}
    for task in MT_MBPP_TASKS:
        metrics[task.name]["bpb"] = 3.0

    # HumanEval and MBPP score 0, and the 17 languages, one member, score 3.
    assert SUITES["base_easy:code_bpb"].score(metrics) == 1.0
