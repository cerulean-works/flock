# Evaluate a model and read its scores

Run a benchmark with `flock eval`, understand what it measures, and keep results that you can compare across Cerulean experiments.

## What an evaluation measures

A **task** defines a benchmark: a pinned dataset, the prompt for each question, any few-shot examples shown before it, and the metrics to report. `arc_easy`, for example, is a multiple-choice science benchmark. A **suite** groups tasks so you can run a broader assessment with one model load.

Flock's scorer measures how likely a model finds a given continuation after a context. For a multiple-choice question, it scores the answer choices rather than generating a free-form answer. A few-shot example is an answered question included in the context to show the expected format. Tasks such as math or code can instead score a supplied gold solution by its likelihood; these are not tests of generated solutions passing an execution harness.

The reported metrics summarize those scores. **Accuracy** is higher-is-better; its normalization determines which choice wins. **Bits per byte (bpb)** measures how much information the model needs to encode the gold continuation, and lower is better. The byte unit lets you compare models with different tokenizers, but only when they score the same benchmark text and setup. Use the task's `primary_metric` first, then the [metric reference](metrics) when interpreting a particular normalization.

A score describes both the model and the evaluation setup. Changing prompts, data or numerical settings can move it. Flock pins model and dataset revisions and records task hashes so you can tell whether two results came from the same instrument. [Scoring behavior](scoring) covers the technical details.

## Run a benchmark

[Install Flock](../installation) first. You need enough device memory for the model, space for downloads and results, and Hugging Face access on the first run. CUDA is selected when available. Local scoring does not need a Flock account; sign-in is needed only when logging to the tracker.

1. Choose a new or empty output directory. Each directory holds one evaluation run.
2. Score OLMo 2 1B on ARC-Easy:

   ```bash
   uv run flock eval run \
     --model allenai/OLMo-2-0425-1B@a1847dff35000b4271fa70afc5db10fd29fedbdf \
     --task arc_easy \
     --out results/olmo2-1b
   ```

   The `repo@revision` form pins which weights are scored. A model name without a revision is refused.

3. The run prints each suite's score and each task's primary metric, and `--verbose` prints every metric. Check the printed scores and these output files:

   ```text
   results/olmo2-1b/
     results.json
     predictions/arc_easy.jsonl
   ```

For a smoke check, add `--limit 20` and use a different output directory. Limited scores do not represent the full benchmark. To reduce evaluation-batch memory, lower `--batch-tokens`; keep it consistent between runs you compare. CPU scoring can be selected with `--device cpu`. See [Commands](commands) for complete options.

## Score a model stored in Flock

A model saved with `save_to_hub` or `flock models push` (see [Store and load models](../hub/store-models)) is scored by its name with a `flock:` prefix:

```bash
uv run flock eval run \
  --model flock:acme/olmo2-1b@checkpoint-12000 \
  --task arc_easy \
  --out results/olmo2-1b-ckpt-12000
```

| `--model` | Scores |
|---|---|
| `flock:acme/olmo2-1b` or `flock:acme/olmo2-1b@primary` | the primary version |
| `flock:acme/olmo2-1b@12` | version 12 |
| `flock:acme/olmo2-1b@checkpoint-12000` | the version with that tag |

[Sign in](../cli/sign-in) first. The version is resolved to its number before anything loads, and the results record it as `flock:acme/olmo2-1b@12`, with the version's digest in `model_digest`. Moving the tag or the primary later doesn't change what the results say was scored. The tokenizer is the Hugging Face one that the model's config names in `hf_repo`, so a model saved without `hf_repo` can't be scored.

## Run where there is no network

A job on a machine that can't reach the Hugging Face Hub reads its datasets from copies staged on shared disk. Each task's dataset is a Hugging Face repo pinned to a commit, and your organization keeps a private copy of that pin on Flock at `eval/<owner>/<name>`. The copy holds the configurations and splits the tasks read.

1. On a machine that reaches Flock, [sign in](../cli/sign-in) and stage each pinned dataset the run reads. ARC-Easy reads `allenai/ai2_arc`:

   ```bash
   uv run flock eval hub pull acme/allenai/ai2_arc@210d026faf9955653af8916fad021475a3f00453 --to /shared/eval-hub
   ```

2. In the job, set `FLOCK_EVAL_HUB` to the same folder and run as usual:

   ```bash
   export FLOCK_EVAL_HUB=/shared/eval-hub
   uv run flock eval run \
     --model allenai/OLMo-2-0425-1B@a1847dff35000b4271fa70afc5db10fd29fedbdf \
     --task arc_easy \
     --out results/olmo2-1b
   ```

With `FLOCK_EVAL_HUB` set, every dataset must be staged. The run checks them all before the model loads and lists the `pull` command for each missing one, with the organization left for you to fill in. The staged copy gives the same rows as the Hugging Face Hub, so the task hashes and scores don't depend on where the rows came from. A pin's commit is in its task's definition, and `pull` refuses a copy whose recorded commit differs.

> [!IMPORTANT]
> A staged copy is readable by whoever can read its staging folder, whatever your umask, and nobody who can only read it can change it. Some copies (exam sets, crawled text) are private on Flock, so choose a staging folder that only the people allowed to read them can open. A staging folder that doesn't exist yet is made readable by you only. A folder others can write to, such as a group's, needs the sticky bit (`chmod +t`), so writing to the folder isn't enough to replace someone else's copy; `pull` refuses one without it. Everyone who can write to the folder is trusted: the first copy staged for a dataset is the one every later `pull` keeps, and only its owner and the owners of the folders above it, up to the staging folder, can replace it.

## Read the results

`results.json` holds the task metrics and **provenance**: the model commit, Flock commit, numerical settings and machine that produced them. Prediction files hold one row per question, including its gold choice and the per-choice scores.

Open the summary:

```python
import json
from pathlib import Path

output = Path("results/olmo2-1b")
results = json.loads((output / "results.json").read_text())
print(results["provenance"])
for task in results["tasks"]:
    primary = task["primary_metric"]
    print(task["task"], primary, task["metrics"][primary])
```

Inspect a question's scores when investigating a result:

```python
with (output / "predictions" / "arc_easy.jsonl").open() as stream:
    prediction = json.loads(next(stream))
print(prediction)
```

Match the doc id across runs to compare the same question. Before comparing aggregates, check the task hash, full versus limited coverage, dtype and batching settings. [Results files](results) defines the schema and provenance; [compare with the regression bank](compare-regression) for a controlled regression check.

## Run more tasks or a suite

Repeat `--task` to evaluate several benchmarks with one model load. For example, add `--task piqa` alongside `--task arc_easy` in the command above and choose a fresh output directory.

To run the implemented Base Easy suite:

```bash
uv run flock eval run \
  --model allenai/OLMo-2-0425-1B@a1847dff35000b4271fa70afc5db10fd29fedbdf \
  --suite base_easy \
  --out results/olmo2-base-easy
```

You can combine tasks and suites; overlapping tasks run once. A suite can report a mean score or only group other suites, so not every suite has one aggregate number. Check the [catalog](tasks#suites) for membership and aggregation, then inspect `tasks` and `suites` in `results.json` to verify coverage.

## Log to Flock

[Sign in](../cli/sign-in) and accept the wandb SDK setup. Add `--wandb-project model-evals` to the evaluation command. The SDK uses your default organization, just as a training script does.

Open `model-evals` in [Cerulean's dashboard](https://flock.cerulean.works) and find the run with job type `eval`. Its summary contains task metrics and suite scores, while config records the evaluation provenance. The local `results.json` is still written. [Tracker fields](commands#tracker-runs) lists the details.

To plot evaluation curves on an existing training run instead, use [Evaluate a checkpoint in memory](evaluate-checkpoint) and log its returned metrics with that run.
