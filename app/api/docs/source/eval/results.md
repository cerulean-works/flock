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
      "docs_per_second": ...
    }
  ],
  "schema_version": 2
}
```

| Field | Meaning |
|---|---|
| `task` | The task's name. |
| `task_hash` | The sha256 of the task's definition and every scored string. Equal hashes mean the same requests were scored. |
| `primary_metric` | The metric to read first. |
| `metrics` | Each metric's mean over docs. See [Metrics](metrics). |
| `num_docs` | How many docs were scored. |
| `limit` | The `--limit` of the run, or `null` for the whole split. |
| `seconds`, `docs_per_second` | Scoring time and throughput, without data and model loading. |
| `schema_version` | The file format's version. It changes when fields change. |

## Provenance

| Field | Meaning |
|---|---|
| `model` | The model as `repo@commit`. |
| `flock_commit` | The flock commit that ran, from the `FLOCK_COMMIT` environment variable or git. A `-dirty` suffix means the checkout had uncommitted changes. |
| `command` | The command line, or `python` when [`~eval.run.evaluate`] was called directly. |
| `device` | The GPU's name, or `cpu`. |
| `dtype` | The weights' dtype, such as `bfloat16`. |
| `batch_tokens` | The forward-pass budget in padded tokens. |
| `max_length` | The longest input the model saw. Longer inputs lost tokens from the left. |
| `attention` | The attention implementation, such as `sdpa`. It changes bfloat16 scores. |
| `torch`, `transformers`, `python` | Library and interpreter versions. |
| `created_at` | When the run finished, in ISO 8601 UTC. |

## Predictions

Each line of `predictions/<task>.jsonl` is one doc:

| Field | Meaning |
|---|---|
| `id` | The doc's id in the dataset. |
| `gold` | The index of the correct choice. |
| `logprob` | Each choice's summed log-probability. |
| `num_tokens` | Each choice's token count. |
| `logprob_uncond` | Each choice's log-probability after the unconditional context, for tasks that score PMI. |

## Classes

[[autodoc]] eval.results.Results

[[autodoc]] eval.results.TaskResult

[[autodoc]] eval.results.Provenance
