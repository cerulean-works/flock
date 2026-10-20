# Adding a task

A multiple-choice task is a definition and one registry line. You don't write any scoring code.

## How `arc_easy` is defined

This is the whole task, in `flock/eval/tasks/arc.py`:

```python
def arc_doc(row: dict[str, Any]) -> MCDoc:
    return MCDoc(
        id=row["id"],
        question=row["question"],
        choices=tuple(row["choices"]["text"]),
        gold=row["choices"]["label"].index(row["answerKey"]),
    )


ARC_EASY = MultipleChoiceTask(
    name="arc_easy",
    dataset=Dataset(
        "allenai/ai2_arc", "210d026faf9955653af8916fad021475a3f00453", "ARC-Easy", "test"
    ),
    to_doc=arc_doc,
    primary_metric="acc_per_char",
    fewshot=Fewshot("train", OLMES_ARC_EASY_SHOTS),
)
```

## Steps

1. **Pin the dataset** to a commit hash. Find it on the dataset's **Files and versions** tab, or with `huggingface_hub.HfApi().dataset_info(repo).sha`.
2. **Write a function** that turns one dataset row into an [`~eval.tasks.MCDoc`].
3. **Choose the few-shot examples** by id. Use the curated OLMES examples when the task has them, so results compare with published numbers.
4. **Choose the primary metric.** Follow OLMES or OLMo 3 for the task. [Metrics](metrics) lists the options.
5. **Register the task** in `TASKS`, in `flock/eval/tasks/registry.py`. `flock-eval run --task` accepts it from then on.

To check a new task, print the first doc's prompt, then score a few docs with `--limit 20`:

```python
from flock.eval.tasks import TASKS

task = TASKS["arc_easy"]
docs, shots = task.load(limit=1)
print(task.context(docs[0], shots))
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

Each choice is scored as the continuation `" <choice>"`. For PMI (`acc_uncond`), each choice is scored a second time after the context `Answer:` alone.

`question_prefix`, `answer_prefix` and `unconditional_context` change this format. Change them only when a task needs it. A changed prompt is a different task, and its numbers don't compare with the old ones, so give it a new name.

## What the task hash covers

Each result records a task hash: the sha256 of the task's fields (all but `to_doc`), every scored string and every gold index. Any change to the dataset revision, the few-shot examples, the prompt or what `to_doc` returns changes the hash. Two results with the same hash scored the same requests.
