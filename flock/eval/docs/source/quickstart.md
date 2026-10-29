# Evaluation quickstart

This page scores OLMo 2 1B on ARC-Easy and reads the results. You need a machine with a GPU.

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

The command downloads the model and the dataset, scores every doc, and prints the task's primary metric. `--verbose` prints every metric. Repeat `--task` to run several tasks with one model load. Every flag is in [Command line](cli).

## Read the results

`--out` gets two kinds of files:

```text
results/olmo2-1b/
  results.json
  predictions/arc_easy.jsonl
```

`results.json` holds each task's mean metrics, such as `acc_per_char` and `bpb`, and the run's provenance: the model commit, the flock commit, the command and the hardware. Each line of `predictions/arc_easy.jsonl` holds one doc's per-choice scores, so you can compare two runs doc by doc. [Results files](results) describes every field, and [Metrics](metrics) what each metric measures.

## Score from Python

[`~eval.run.evaluate`] takes the same settings as the command line and returns the results:

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

[`~eval.run.EvalConfig`] is plain data, so you can send it to a remote worker that calls `evaluate`.

## Next steps

- [Adding a task](adding-a-task)
- [Scoring during training](scoring-during-training)
