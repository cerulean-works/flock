# Adding a task

A multiple-choice task is a definition and one registry line. You don't write any scoring code.

## How `arc_easy` is defined

This is the whole task, `flock/eval/tasks/arc.py`:

<literalinclude>{"path": "../../tasks/arc.py", "language": "python"}</literalinclude>

## Steps

1. **Pin the dataset** to a commit hash. Find it on the dataset's **Files and versions** tab, or with `huggingface_hub.HfApi().dataset_info(repo).sha`.
2. **Write a function** that turns one dataset row into an [`~eval.tasks.MCDoc`].
3. **Choose the few-shot examples** by id. Use the curated OLMES examples when the task has them, so results compare with published numbers.
4. **Choose the primary metric.** Follow OLMES or OLMo 3 for the task. [Metrics](metrics) lists the options.
5. **Register the task.** In `flock/eval/tasks/registry.py`, import it and add it to the tuple that builds `TASKS`. `flock-eval run --task` accepts it from then on.
6. **Test the row function.** Add a test in `tests/eval/test_tasks.py` that maps a hand-written row to the expected [`~eval.tasks.MCDoc`], as `test_arc_doc_maps_letter_and_digit_keys` does. Tests never download: `tests/eval/conftest.py` makes every task read the toy rows in `tests/eval/toy.py`.
7. **List the task** in the table of [Tasks](tasks#available-tasks).

To check a new task, print the first doc's prompt:

```python
from flock.eval.tasks import TASKS

task = TASKS["arc_easy"]
docs, shots = task.load(limit=1)
print(task.context(docs[0], shots))
```

Then score a few docs with a small model:

```bash
uv run flock-eval run \
  --model allenai/OLMo-2-0425-1B@a1847dff35000b4271fa70afc5db10fd29fedbdf \
  --task arc_easy \
  --limit 20 \
  --out results/arc-easy-check
```

## The prompt format

Every task renders its prompts the same way:

```text
Question: <few-shot question 1>
Answer: <its gold choice>

...

Question: <question>
Answer:
```

Each choice is scored as the continuation `" <choice>"`. For pointwise mutual information (PMI, `acc_uncond`), each choice is scored a second time after the task's `unconditional_context` alone (`Answer:` by default).

`question_prefix`, `answer_prefix` and `unconditional_context` change this format. Change them only when a task needs it. A changed prompt is a different task, and its numbers don't compare with the old ones, so give it a new name.

## The task hash

Each result records the task's [hash](results#the-task-hash), so results from before and after a change to the task never share one.
