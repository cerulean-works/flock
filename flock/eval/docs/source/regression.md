# Checking scores against the bank

A change to scoring code, a task's data or a library can move a model's numbers without failing any test. CI checks that every task's definition stays the same, but it has no GPU, so it can't score a model. This page shows how to check scores on a GPU: you rerun a reference model and compare its `results.json` with the one banked in the repository.

## The bank

The bank is `tests/eval/regression/olmo-2-0425-1b-float32.json`. It is the unchanged `results.json` of OLMo 2 1B scored on `base_easy` in float32:

```bash
uv run flock-eval run \
  --model allenai/OLMo-2-0425-1B@a1847dff35000b4271fa70afc5db10fd29fedbdf \
  --suite base_easy \
  --dtype float32 \
  --out results/regression
```

Its [provenance](results#provenance) records the flock commit, the GPU and the library versions that wrote it.

The bank is float32 because float32 scores repeat exactly when you rerun the same code on the same GPU type with the same library versions. So any difference is a real change, and the check allows none. bfloat16 scores can differ between two reruns on one GPU, because the attention kernel is chosen at run time, so there is no bfloat16 bank.

## Running the check

1. On a GPU machine, run the command above at the commit you want to check, into a new directory.
2. Compare the new results with the bank:
   ```bash
   uv run flock-eval compare tests/eval/regression/olmo-2-0425-1b-float32.json results/regression/results.json
   ```

`compare` exits with:

| Code | Meaning |
|---|---|
| `0` | Every compared value is equal. |
| `1` | At least one value differs (a breach). Each one is printed. |
| `2` | The runs are different instruments, so nothing was compared. |

## What is compared

For each task in both files, `compare` checks `num_docs` and every entry of `metrics` and `truncation`. For each suite in both files, it checks `members` and `score`. Timing isn't compared. A metric that is in one file only is a breach.

A task or suite in one file only is listed and is not a breach. So you can compare a rerun of a few tasks (without `--limit`) against the full bank. A task added to `base_easy` after the bank was written shows as only in the new run, and each suite that holds it is a breach on `members` and `score` until the bank is refreshed.

The output lists, in order:
- the provenance fields that differ and often explain a breach: `flock_commit`, `device`, `torch`, `transformers` and `python`
- the tasks and suites in one file only
- one line per breach, with the task or suite, the field, both values and their difference
- a count of what was compared

```text
flock_commit  0695663f...  ->  4c1e2b7a...
piqa  metrics.acc_raw  0.6  ->  0.625  (+0.025)
compared: tasks 109, suites 10, breaches 1
```

## Refusals

A difference in score only means something if both runs scored the same requests with the same settings. `compare` refuses, before it reads any score, when:
- the provenance's `model`, `dtype`, `batch_tokens`, `max_length` or `attention` differ
- a task in both files has a different `task_hash` (see [The task hash](results#the-task-hash)) or `limit`
- a suite in both files has a different `metric`, so its score averages another metric
- no task is in both files, a file isn't JSON, or a field it reads is missing

The message names the field and both values. A refused pair needs a new bank, not a looser check.

## Refreshing the bank

You refresh the bank when a change is meant to move scores: a task added to `base_easy`, a deliberate change to scoring or to a task's requests, or a library upgrade that moves float32 numbers.

1. Run the command in [The bank](#the-bank) at the new commit.
2. Run `compare` from the old bank to the new `results.json`.
3. Replace `tests/eval/regression/olmo-2-0425-1b-float32.json` with the new `results.json`, unchanged.
4. In the pull request, paste `compare`'s output and say why each difference is expected.

The refresh is reviewed like code. A library upgrade refreshes the bank in the same pull request as the lockfile.
