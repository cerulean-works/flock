# Command line

`flock-eval` is installed with the workspace. Run it with `uv run flock-eval` from the repository root.

## `flock-eval run`

Scores one model on one or more tasks, writes the results, and prints each task's metrics with the primary metric first.

```bash
uv run flock-eval run --model <repo>@<revision> --task <task> [--task <task> ...] --out <dir>
```

| Flag | Default | Meaning |
|---|---|---|
| `--model` | required | The model as `repo@revision`. A branch or tag is resolved to its commit through the Hub, and the results record the commit. A full commit hash needs no Hub access, so it works offline. |
| `--task` | required | A task name from [Tasks](tasks#available-tasks). Repeat it for several tasks with one model load. |
| `--out` | required | The directory for `results.json` and `predictions/`. It must be new or empty, so one directory holds one run. |
| `--limit` | all docs | Score only each task's first N docs, for quick checks. Results with a limit aren't comparable to full runs. |
| `--batch-tokens` | `32768` | The most padded tokens in one forward pass. Lower it if the GPU runs out of memory. Compare only runs with the same value. See [Batching and precision](how-scoring-works#batching-and-precision). |
| `--device` | `cuda` if available, else `cpu` | The torch device. |
| `--wandb-project` | off | Also log the run to this project through the `wandb` SDK. Run `wandb login --host <flock url>` first, so it goes to Flock. |

The command runs [`~eval.run.evaluate`], and every flag maps to a field of [`~eval.run.EvalConfig`].

## Tracker runs

With `--wandb-project`, each `flock-eval run` logs one run with job type `eval`:

| Run field | Holds |
|---|---|
| config | The [provenance](results#provenance), plus `task_hashes`: each task's hash by name. |
| summary | `<task>/<metric>` for each metric, and `<task>/docs_per_second`. |

`results.json` is written either way.
