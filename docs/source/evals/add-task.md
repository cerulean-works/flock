# Add an evaluation task

A multiple-choice task is a definition and one registry line. You don't write any scoring code.

## Before you start

Work in an installed repository checkout. Choose a pinned dataset and a stable task name. You need a row fixture for a network-free test.

## Use an existing definition

This is the whole task, `flock/eval/tasks/arc.py`:

<literalinclude>{"path": "../../../flock/eval/tasks/arc.py", "language": "python"}</literalinclude>

## Steps

1. **Pin the dataset** to a commit hash. Find it on the dataset's **Files and versions** tab, or with `huggingface_hub.HfApi().dataset_info(repo).sha`. A dataset whose repo has only a loading script, which `datasets` no longer runs, is mirrored as parquet under `cerulean-works`, as basic skills is.
2. **Write a function** that turns one dataset row into an [`~eval.tasks.MCDoc`]. It renders each choice as the model sees it: [`~eval.tasks.cloze`] gives OLMES's `Question: ... Answer:` format, as `arc_doc` uses. [Task definition reference](task-definitions#the-prompt-format) covers other formats. A dataset with no unique id column sets `Dataset(row_index=True)`, and the function reads the id as `str(row["index"])`, the row's position in its split, as MMLU does.
3. **Choose the few-shot examples** by id. Use the curated OLMES examples when the task has them, so results compare with published numbers. Examples that aren't rows of the dataset go in `Fewshot.rows`, in the dataset's row format.
4. **Choose the primary metric.** Follow OLMES or OLMo 3 for the task. [Metrics](metrics) lists the options.
5. **Register the task.** Add it to its suite, such as `_QA_TASKS` in `flock/eval/tasks/base_easy.py` for a Base Easy QA task (see [Suites](tasks#suites)): every task in a suite is registered. A task in no suite goes in the tuple that builds `TASKS` in `flock/eval/tasks/registry.py`, and a new top-level suite in the expression that builds `SUITES` there. `flock eval run --task` accepts it from then on.
6. **Test the row function.** Add a test in `tests/eval/` that maps a hand-written row to the expected [`~eval.tasks.MCDoc`], as `test_arc_doc_maps_letter_and_digit_keys` does. Tests never download: `tests/eval/conftest.py` makes every task read the toy rows in `tests/eval/toy.py`.
7. **Update the snapshot.** CI pins every registered task's definition (dataset, revision, few-shot examples, metrics, row function) in `tests/eval/snapshot/tasks.json`, and fails when the code no longer matches it. Run `uv run python -m tests.eval.snapshot` and commit the files it rewrites. The JSON diff shows your reviewer exactly what changed, so regenerate it only for a change you mean to make. A new prompt format also adds a toy task to `PROMPT_PATHS` in `tests/eval/toy.py`, which pins the requests it renders in `toy_requests.json`.
8. **List the task** in the table of [Tasks](tasks#available-tasks).

To check a new task, print the first doc's prompt:

```python
from flock.eval.tasks import TASKS

task = TASKS["arc_easy"]
docs, shots = task.load(limit=1)
print(task.requests(docs, shots).requests[0])
```

Then score a few docs with a small model:

```bash
uv run flock eval run \
  --model allenai/OLMo-2-0425-1B@a1847dff35000b4271fa70afc5db10fd29fedbdf \
  --task arc_easy \
  --limit 20 \
  --out results/arc-easy-check
```


## Check that it works

Run your row-mapping test and the snapshot check before the GPU smoke run. Review the generated snapshot diff and confirm that only the intended task definitions changed. A smoke run should write one predictions file and a task entry in `results.json`.

Prompt formats, few-shot filtering and task identity are specified in [Task definitions](task-definitions).
