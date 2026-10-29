# Results files

A run with an output directory writes `results.json` and one predictions file per task:

```text
<out>/
  results.json
  predictions/<task>.jsonl
```

## `results.json`

```text
{
  "provenance": { ... },
  "tasks": [
    {
      "task": "arc_easy",
      "task_hash": "<sha256>",
      "dataset": {"repo": "allenai/ai2_arc", "revision": "210d026f...", "subset": "ARC-Easy", "split": "test", "row_index": false},
      "primary_metric": "acc_per_char",
      "metrics": {"acc_raw": ..., "acc_per_token": ..., "acc_per_char": ..., "acc_per_byte": ..., "acc_uncond": ..., "bpb": ...},
      "num_docs": 2376,
      "limit": null,
      "seconds": ...,
      "truncation": {"requests": 0, "docs": 0, "max_tokens_cut": 0},
      "docs_per_second": ...
    },
    ...
  ],
  "suites": [
    {"suite": "mmlu", "suite_hash": "<sha256>", "members": ["mmlu_abstract_algebra", ...], "metric": null, "score": ...},
    {"suite": "base_easy:qa_rc", "suite_hash": "<sha256>", "members": ["arc_easy", ..., "basic_skills", "lambada"], "metric": null, "score": ...},
    {"suite": "mmlu:bpb", "suite_hash": "<sha256>", "members": ["mmlu_abstract_algebra", ...], "metric": "bpb", "score": ...},
    {"suite": "base_easy:qa_bpb", "suite_hash": "<sha256>", "members": ["arc_easy", ..., "basic_skills:bpb", "lambada"], "metric": "bpb", "score": ...},
    ...,
    {
      "suite": "base_easy",
      "suite_hash": "<sha256>",
      "members": ["base_easy:qa_rc", "base_easy:qa_bpb", "base_easy:math_bpb", "base_easy:code_bpb"],
      "metric": null,
      "score": null
    }
  ],
  "schema_version": 6
}
```

The file is a [`~eval.results.Results`]. Each entry of `tasks` is a [`~eval.results.TaskResult`] plus its `docs_per_second`, which is `null` if the clock measured no time, each entry of `suites` is a [`~eval.results.SuiteResult`], and `provenance` is described below. The class docstrings at the end of this page define every field.

### The task hash

`task_hash` is the sha256 of the task's definition (every field but `to_doc`, so its name and primary metric too), every scored context and continuation, and every gold index. Changing the dataset revision, the few-shot examples, the task's name, description, metrics or primary metric, or a choice's context or continuation or the gold index that `to_doc` returns changes it. Doc ids aren't hashed. Two results with the same hash scored the same requests under the same task definition. `dataset` repeats the pinned dataset next to the hash, so you can see which rows were scored without the task's code.

### Suites

`suites` holds the suites the run was given and every suite they hold, each after its members, and is empty for a run of tasks only. Each entry lists its members by name, and every member has its own entry in `tasks` or `suites`, so you can recompute the score: the mean of each member task's `metric` (its `primary_metric` when `metric` is `null`) and each member suite's `score`. A suite that only groups others has a `null` score. A suite's members can grow between versions of `flock-eval`, so compare two suite scores only when their `suite_hash`es are equal. `suite_hash` is the sha256 of the JSON object `{"suite": <name>, "metric": <metric>, "scored": <bool>, "members": [[<name>, <hash>], ...]}`, written with Python's `json.dumps(..., sort_keys=True)`, where each member gives its `task_hash` or `suite_hash`. Equal suite hashes mean the same members, all the way down, scored the same requests, so a `limit` shows up too. This is a check of our own: OLMES and lm-evaluation-harness record no hash for a group of tasks.

### Truncation

A context too long for the model loses tokens from the left, and the continuation is kept whole (see [How scoring works](how-scoring-works)). `truncation` says how often that happened in the task: how many requests were cut, how many docs had at least one cut request, and the most tokens cut from one request. Metrics on cut contexts can differ from metrics on whole ones, so check `docs` before comparing a task across models: a different `max_length` or tokenizer cuts different requests. `max_tokens_cut` counts the model's own tokens. All three are 0 when nothing was cut.

## Provenance

`provenance` records what produced the run, so a number can be traced to the exact model, code and settings: [`~eval.provenance.Provenance`] at the end of this page defines each field. It is collected once the model has loaded and before any task is scored, so `created_at` is when scoring started, as lm-evaluation-harness records its start date. `command` is the command line for a run from `flock-eval`, and `null` for a run from the Python API that passes none. `flock_commit` is `"unknown"` when the installed package isn't a flock checkout, and ends in `-dirty` when tracked files changed or new files appeared under `flock/`.

## Predictions

Each line of `predictions/<task>.jsonl` is one doc, with enough to recompute every metric of the task and its truncation counts:

| Field | Meaning |
|---|---|
| `id` | The doc's id in the dataset. |
| `gold` | The index of the correct choice. |
| `logprob` | Each choice's summed log-probability. |
| `num_tokens` | Each choice's token count. |
| `num_chars` | Each choice's character count, leading space included. |
| `num_bytes` | Each choice's UTF-8 byte count, leading space included. |
| `is_greedy` | Whether each choice's continuation is the model's greedy output: every token is its position's most likely next token. |
| `tokens_cut` | The tokens cut from the left of each choice's context, 0 if it fit. |
| `logprob_uncond` | Each choice's log-probability after the unconditional context, for tasks that score pointwise mutual information (PMI). |
| `tokens_cut_uncond` | `tokens_cut` for the unconditional requests, with `logprob_uncond`. |

[Metrics](metrics) gives each metric's formula over these fields.

## Classes

[[autodoc]] eval.results.Results

[[autodoc]] eval.results.TaskResult

[[autodoc]] eval.results.SuiteResult

[[autodoc]] eval.tasks.Truncation

[[autodoc]] eval.provenance.Provenance
