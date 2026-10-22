# 007: record truncated requests in results.json

| | |
| --- | --- |
| Status | in review |
| Date | 2026-10-22 |
| Builds on | [001](001-eval-core.md) |
| Keeps | D2 (left truncation, whole continuation), D3 (no host syncs of our own), D8 (one results file) |
| Result | 85 tests in `tests/eval/` pass on CPU; scores and task hashes unchanged; see [section 4](#4-how-it-was-actually-built) |

## 1. Goal

`ScoringModel` cuts tokens from the left of a context longer than `max_length` and logs a
warning, but `results.json` doesn't show it. A reader can't tell whether a number was scored
on cut contexts. Few-shot tasks with long prompts will truncate on small-context models, so:

1. **Count truncation per task** and write it into `results.json` next to the metrics.
2. **Two follow-ups from the 001 review:** `bits_per_byte` warns when a byte count is below 1,
   and a test shows that scoring works with a tokenizer that has no BOS token (Qwen).

**Out of scope:**
- changing how truncation works, or sliding-window scoring;
- per-doc truncation in `predictions/<task>.jsonl`;
- the tracker summary (see D14).

---

## 2. Design decisions

### D11. Count three numbers per task

Every task result gets a `truncation` object:

| Field | Meaning |
| --- | --- |
| `requests` | Requests whose context lost tokens, conditional and unconditional alike. |
| `docs` | Docs with at least one such request: the docs whose metrics may have moved. |
| `max_tokens_cut` | The most tokens cut from any one request. |

All three are 0 when nothing was cut, and the object is always present, so a missing field
never has to be read as "none". `docs` is the number that says how much of a metric is
affected. `requests` matches the warning's count. `max_tokens_cut` tells a one-token cut on a
long prompt from a context that lost most of its few-shot examples.

lm-evaluation-harness and OLMES cut the same way, `(context_enc + continuation_enc)[-(self.max_length + 1) :]`
in `_loglikelihood_tokens`, and neither records how often. Recording it is our addition: a
setting that changes what is scored must show in the results file.

### D12. Counted on the host from token lengths, carried with the scores

A request's cut is `max(0, len(context) + len(continuation) - (max_length + 1))`, known from the
token ids before any forward pass, so the per-request count needs no GPU work.

- `Scores` gains `tokens_cut`, int64 `[len(requests)]` on the model's device like `num_tokens`:
  the tokens cut from each request's context, 0 if it fit. The warning reads the same list.
- `MCRequests.truncation(scores) -> Truncation` maps requests to docs through the same
  `_choice_index` layout `choice_scores` uses (each doc's choices, then the same choices again
  for PMI) and reduces on the device: `requests` and `max_tokens_cut` directly, `docs` through
  an `index_add_` of truncated requests per doc. It copies the three
  numbers to the host once, which is one sync per task, after the metric means have already
  waited for the GPU.
- `Truncation` is a frozen dataclass of three ints in `tasks/base.py`, and `TaskResult` gains a
  `truncation: Truncation` field, which `dataclasses.asdict` writes as a nested object.

`results.json` becomes schema version 3:

```text
"tasks": [{"task": "arc_easy", ..., "truncation": {"requests": 0, "docs": 0, "max_tokens_cut": 0}, ...}]
```

### D13. `bits_per_byte` warns on a byte count below 1

`bits_per_byte` clamps byte counts to at least 1, as OLMES does. Every current caller passes the
UTF-8 length of a non-empty continuation, so the clamp never fires. A later caller might not
check, so the function logs one warning per call with how many counts were below 1, then clamps
as before. Counting copies one number to the host, one sync per call. `multiple_choice` calls it
once per task, so this stays within D3.

### D14. The tracker summary is unchanged

`results.json` is the record. The tracker keeps logging metrics and throughput only. Adding
`<task>/truncated_docs` to the summary is a one-line change if the dashboard should show it.

### D15. Tokenizers without a BOS token

`_prefix_token` already falls back to EOS (D2). The test tokenizer has no BOS, as Qwen3's
tokenizer has none (its `bos_token` is null; OLMo 2's is `<|endoftext|>`), and a new test scores an empty-context request end to end against an
unbatched forward pass that starts from EOS. No code changes.

---

## 3. Plan

1. `model.py`: per-request `tokens_cut`, returned in `Scores`; the warning reads it.
2. `tasks/base.py`: `Truncation`, `MCRequests.truncation`.
3. `results.py`, `run.py`: `TaskResult.truncation`, schema version 3.
4. `metrics.py`: the byte-count warning.
5. Tests: a request that fits records 0; one over `max_length` records its exact cut; a PMI
   task where only some docs truncate gives the right `docs`; the byte-count warning; scoring
   with no BOS token. Scores and task hashes are unchanged.
6. Docs: `results.md` (example and field), `metrics.md` if it lists the clamp.

---

## 4. How it was actually built

- **Lock.** On the tiny model and ARC-Easy's request strings, every score and both task hashes
  match `4333ba4` (the merge of 001) bit for bit: the scorer's outputs and hashes were saved
  from both trees with the same script and compared for exact equality. `results.json` differs only by the `truncation` object and
  `schema_version: 3`, and the predictions files are identical.
- **Why `Scores` stays on the device.** `tokens_cut` is host data, and reducing it on the host
  would skip one tiny copy. It travels on the device anyway, like `num_tokens`, so `Scores` keeps
  one device and later per-request fields need no special case. The cost is one copy of three
  numbers per task, after the metric means have already waited for the GPU.
- **Order in `score_task`.** Truncation is counted before the predictions file is written, so a
  failed count never leaves predictions for a task with no result.
- **Docs.** `results.md` documents the field and `how-scoring-works.md` links to it.
