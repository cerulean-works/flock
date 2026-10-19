# Quickstart

This page scores OLMo 2 1B on ARC-Easy, reads the results file, and adds a new task. You need a machine with a GPU for real models. Everything else, including the tests, runs on a CPU.

## Install

From the repository root, install the workspace as the [README](https://github.com/cerulean-works/flock#getting-started) describes:

```bash
uv sync --locked
```

This puts the `flock-eval` command in the workspace's environment.

## Score a model

Models are always given as `repo@revision`, so a result can't silently change when a repo gets new weights:

```bash
uv run flock-eval run \
  --model allenai/OLMo-2-0425-1B@a1847dff35000b4271fa70afc5db10fd29fedbdf \
  --task arc_easy \
  --out results/olmo2-1b
```

The command downloads the model and the dataset, scores every doc, and prints one line per metric with the task's primary metric first. Repeat `--task` to run several tasks with one model load.

| Flag | Default | Meaning |
|---|---|---|
| `--model` | required | The model as `repo@revision`. A branch or tag is resolved to its commit through the Hub, which the results record. A full commit hash needs no Hub access, so it works offline. |
| `--task` | required | A task name. Repeat it for several tasks. |
| `--out` | required | The directory for `results.json` and the per-doc predictions. It must be new or empty, so one directory holds one run. |
| `--limit` | all docs | Score only each task's first N docs, for quick checks. Results with a limit aren't comparable to full runs. |
| `--batch-tokens` | `32768` | The most padded tokens in one forward pass. Lower it if the GPU runs out of memory. In bfloat16, which requests share a batch moves each score slightly (about 0.05 nats on average for OLMo 2 1B), so compare runs made with the same value and the same `--limit`. |
| `--device` | `cuda` if available | The torch device. |
| `--wandb-project` | off | Also log the metrics to this project through the `wandb` SDK, which goes to the flock tracker once you've run `wandb login --host`. |

## Read the results

`--out` gets two kinds of files:

```text
results/olmo2-1b/
  results.json
  predictions/arc_easy.jsonl
```

`results.json` holds each task's mean metrics and the run's provenance:

```text
{
  "provenance": {
    "model": "allenai/OLMo-2-0425-1B@a1847dff35000b4271fa70afc5db10fd29fedbdf",
    "flock_commit": "<the flock commit that ran>",
    "command": "flock-eval run --model ...",
    "device": "NVIDIA H200",
    "dtype": "bfloat16",
    "max_length": 4096,
    "attention": "sdpa",
    ...
  },
  "tasks": [
    {
      "task": "arc_easy",
      "task_hash": "<sha256 of what was scored>",
      "primary_metric": "acc_per_char",
      "metrics": {"acc_raw": ..., "acc_per_token": ..., "acc_per_char": ..., "acc_per_byte": ..., "acc_uncond": ..., "bpb": ...},
      "num_docs": 2376,
      "docs_per_second": ...
    }
  ],
  "schema_version": 2
}
```

The metrics are:

| Metric | How a doc is scored |
|---|---|
| `acc_raw` | The choice with the highest summed log-probability wins. |
| `acc_per_token` | Log-probability divided by the choice's token count. |
| `acc_per_char` | Log-probability divided by the choice's character count, including its leading space. |
| `acc_per_byte` | The choice with the fewest bits per UTF-8 byte wins. |
| `acc_uncond` | PMI: log-probability minus the choice's log-probability after just `Answer:`. |
| `bpb` | Bits per UTF-8 byte of the gold choice. Lower is better. |

Each line of `predictions/<task>.jsonl` holds one doc's id, its gold index, and each choice's log-probability and token count, so you can compare two runs doc by doc.

## Score from Python

`evaluate` takes the same settings as the command line and returns the results. Its config is plain data, so you can send it to a remote worker:

```python
from pathlib import Path

from flock.eval.run import EvalConfig, evaluate

results = evaluate(
    EvalConfig(
        model="allenai/OLMo-2-0425-1B@a1847dff35000b4271fa70afc5db10fd29fedbdf",
        tasks=("arc_easy",),
        out=Path("results/olmo2-1b"),
    )
)
print(results.tasks[0].metrics["acc_per_char"])
```

To score a model you already hold in memory, such as one being trained, wrap it in a `ScoringModel` and call `score_task`:

```python
from flock.eval.model import ScoringModel
from flock.eval.run import score_task
from flock.eval.tasks import TASKS

scorer = ScoringModel(model, tokenizer, name="llm-pretrain@step-2000")
result = score_task(scorer, TASKS["arc_easy"])
```

## Add a task

A multiple-choice task is a definition and a registry line. This is how `arc_easy` is defined, in `flock/eval/tasks/arc.py`:

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

To add one:
1. Pin the dataset to a commit hash. Find it on the dataset's **Files and versions** tab, or with `huggingface_hub.HfApi().dataset_info(repo).sha`.
2. Write a function that turns one row into an `MCDoc`.
3. Choose the few-shot examples by id. Use the curated OLMES examples when the task has them, so results compare with published numbers.
4. Add the task to `TASKS` in `flock/eval/tasks/registry.py`.

Every task renders its prompts the same way:

```text
Question: <few-shot question 1>
Answer: <its gold choice>

...

Question: <question>
Answer:
```

Each choice is scored as the continuation `" <choice>"`. Change `question_prefix`, `answer_prefix` or `unconditional_context` only when a task needs it, since a changed prompt is a different task and its numbers don't compare with the old ones.
