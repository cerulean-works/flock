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
      "primary_metric": "acc_per_char",
      "metrics": {"acc_raw": ..., "acc_per_token": ..., "acc_per_char": ..., "acc_per_byte": ..., "acc_uncond": ..., "bpb": ...},
      "num_docs": 2376,
      "limit": null,
      "seconds": ...,
      "truncation": {"requests": 0, "docs": 0, "max_tokens_cut": 0},
      "docs_per_second": ...
    }
  ],
  "schema_version": 3
}
```

The file is a [`~eval.results.Results`]. Each entry of `tasks` is a [`~eval.results.TaskResult`] plus its `docs_per_second`, and `provenance` is described below. The class docstrings at the end of this page define every field.

### The task hash

`task_hash` is the sha256 of the task's definition (every field but `to_doc`, so its name and primary metric too), every scored context and continuation, and every gold index. Changing the dataset revision, the few-shot examples, the prompt, the task's name or primary metric, or the question, choices or gold index that `to_doc` returns changes it. Doc ids aren't hashed. Two results with the same hash scored the same requests under the same task definition.

### Truncation

A context too long for the model loses tokens from the left, and the continuation is kept whole (see [How scoring works](how-scoring-works)). `truncation` says how often that happened in the task: how many requests were cut, how many docs had at least one cut request, and the most tokens cut from one request. Metrics on cut contexts can differ from metrics on whole ones, so check `docs` before comparing a task across models with different `max_length`. All three are 0 when nothing was cut.

## Provenance

`provenance` records what produced the run, so a number can be traced to the exact model, code and settings: [`~eval.provenance.Provenance`] at the end of this page defines each field. `created_at` is taken after scoring, when `results.json` is assembled.

## Predictions

Each line of `predictions/<task>.jsonl` is one doc:

| Field | Meaning |
|---|---|
| `id` | The doc's id in the dataset. |
| `gold` | The index of the correct choice. |
| `logprob` | Each choice's summed log-probability. |
| `num_tokens` | Each choice's token count. |
| `logprob_uncond` | Each choice's log-probability after the unconditional context, for tasks that score pointwise mutual information (PMI). |

## Classes

[[autodoc]] eval.results.Results

[[autodoc]] eval.results.TaskResult

[[autodoc]] eval.tasks.Truncation

[[autodoc]] eval.provenance.Provenance
