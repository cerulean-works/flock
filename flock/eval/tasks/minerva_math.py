"""Minerva MATH (Hendrycks et al. 2021, 2103.03874; Lewkowycz et al. 2022, 2206.14858): competition math problems in 7 subjects, scored on the gold solution."""

from typing import Any

from flock.eval.tasks.base import Choice, Dataset, Fewshot, MCDoc, MultipleChoiceTask

SUBJECTS = (
    "algebra",
    "counting_and_probability",
    "geometry",
    "intermediate_algebra",
    "number_theory",
    "prealgebra",
    "precalculus",
)

# OLMES's `FEWSHOT_SOURCES["Minerva:MATH:fixed"]` (`oe_eval/tasks/fewshot_sources.py`), copied
# exactly, quirks included: they aren't rows of the dataset. The same 4 serve every subject.
_SHOTS: tuple[dict[str, Any], ...] = (
    {
        "problem": "Find the domain of the expression  $\\frac{\\sqrt{x-2}}{\\sqrt{5-x}}$.}",
        "solution": (
            "The expressions inside each square root must be non-negative. Therefore, $x-2 \\ge "
            "0$, so $x\\ge2$, and $5 - x \\ge 0$, so $x \\le 5$. Also, the denominator cannot be "
            "equal to zero, so $5-x>0$, which gives $x<5$. Therefore, the domain of the "
            "expression is $\\boxed{[2,5)}$.\nFinal Answer: The final answer is $[2,5)$. I hope "
            "it is correct."
        ),
        "index": "fewshot_0",
    },
    {
        "problem": (
            "If $\\det \\mathbf{A} = 2$ and $\\det \\mathbf{B} = 12,$ then find $\\det "
            "(\\mathbf{A} \\mathbf{B}).$"
        ),
        "solution": (
            "We have that $\\det (\\mathbf{A} \\mathbf{B}) = (\\det \\mathbf{A})(\\det "
            "\\mathbf{B}) = (2)(12) = \\boxed{24}.$\nFinal Answer: The final answer is $24$. I "
            "hope it is correct."
        ),
        "index": "fewshot_1",
    },
    {
        "problem": (
            "Terrell usually lifts two 20-pound weights 12 times. If he uses two 15-pound weights "
            "instead, how many times must Terrell lift them in order to lift the same total "
            "weight?"
        ),
        "solution": (
            "If Terrell lifts two 20-pound weights 12 times, he lifts a total of $2\\cdot "
            "12\\cdot20=480$ pounds of weight.  If he lifts two 15-pound weights instead for $n$ "
            "times, he will lift a total of $2\\cdot15\\cdot n=30n$ pounds of weight.  Equating "
            "this to 480 pounds, we can solve for "
            "$n$:\n\\begin{align*}\n30n&=480\\\n\\Rightarrow\\qquad "
            "n&=480/30=\\boxed{16}\n\\end{align*}\nFinal Answer: The final answer is $16$. I hope "
            "it is correct."
        ),
        "index": "fewshot_2",
    },
    {
        "problem": (
            "If the system of equations\n\\begin{align*}\n6x-4y&=a,\\\n6y-9x "
            "&=b.\n\\end{align*}\nhas a solution $(x, y)$ where $x$ and $y$ are both nonzero, "
            "find $\\frac{a}{b},$ assuming $b$ is nonzero."
        ),
        "solution": (
            "If we multiply the first equation by $-\\frac{3}{2}$, we obtain "
            "$$6y-9x=-\\frac{3}{2}a.$$Since we also know that $6y-9x=b$, we have "
            "$$-\\frac{3}{2}a=b\\Rightarrow\\frac{a}{b}=\\boxed{-\\frac{2}{3}}.$$\nFinal Answer: "
            "The final answer is $-\\frac{2}{3}$. I hope it is correct."
        ),
        "index": "fewshot_3",
    },
)


def minerva_doc(row: dict[str, Any]) -> MCDoc:
    """A MATH row as a gold-only doc in OLMES's Minerva format: `Problem:\\n<problem>\\n\\nSolution:`, then the solution (`oe_eval/tasks/oe_eval_tasks/minerva_math.py`)."""
    context = f"Problem:\n{row['problem']}\n\nSolution:"
    solution = row["solution"]
    # OLMES scores the solution with a leading space unless it starts with one or a newline
    # (`construct_basic_generation_requests` in `oe_eval/tasks/base_task.py`). Its examples always
    # get one (`doc_to_target`), which is the same text: the 4 below start with a letter.
    if not solution.startswith((" ", "\n")):
        solution = " " + solution

    return MCDoc(id=str(row["index"]), choices=(Choice(context, solution),), gold=0)


# One task per subject, as OLMES's `minerva_math_<subject>:bpb::olmes`, whose primary metric is
# the gold solution's `bits_per_byte_corr` (`oe_eval/configs/tasks.py`).
MINERVA_MATH_TASKS = tuple(
    MultipleChoiceTask(
        name=f"minerva_math_{subject}",
        dataset=Dataset(
            repo="EleutherAI/hendrycks_math",
            revision="21a5633873b6a120296cce3e2df9d5550074f4a3",
            subset=subject,
            split="test",
            row_index=True,
        ),
        to_doc=minerva_doc,
        primary_metric="bpb",
        fewshot=Fewshot(rows=_SHOTS),
        # One choice, so an accuracy is always 1 and pointwise mutual information (PMI) has nothing
        # to compare.
        unconditional_context=None,
        metrics=("bpb",),
    )
    for subject in SUBJECTS
)
