# Score comparison

`flock eval compare` checks two results files before deciding whether their scores agree. For the workflow, see [Compare with the regression bank](compare-regression).

## What is compared

For each task in both files, `compare` checks `num_docs` and every entry of `metrics` and `truncation`. For each suite in both files, it checks `members` and `score`. Timing isn't compared. A metric that is in one file only is a breach.

### Tolerances

| Value | May move by |
|---|---|
| An accuracy (`acc_*`) | A net change of 2 docs: 2 divided by the task's `num_docs`, plus half a doc for rounding |
| Any other metric, such as `bpb` | 1e-4 of its banked value |
| A suite's `score` | Its members' tolerances combined as independent errors: the root of their sum of squares, divided by the number of members |
| `num_docs`, truncation counts, `members` | Nothing: they must be equal |

These values are a reasoned estimate, not a measurement. A real regression, such as a tokenization or truncation bug, moves an accuracy by far more than 2 docs and `bpb` by much more than 1e-4. Run-to-run drift on another stack should stay well inside both. They live in `flock/eval/compare.py` as `FLIPPED_DOCS` and `RELATIVE_TOLERANCE`. A suite is held tighter than its members, because their drifts are independent and partly cancel: the `mmlu` score, over 57 subjects of about 100 docs each, may move by about 0.002.

A task or suite in one file only is listed and is not a breach. So you can compare a rerun of a few tasks (without `--limit`) against the full bank. A task added to `base_easy` after the bank was written shows as only in the new run, and each suite that holds it is a breach on `members` and `score` until the bank is refreshed.

The output lists, in order:
- the fields that differ and often explain a breach: the provenance's `flock_commit`, `device`, `torch`, `transformers`, `tokenizers`, `datasets` and `python`, and the file's `schema_version`
- the tasks and suites in one file only
- one line per breach, with the task or suite, the field, both values and their difference
- a count of what was compared

A rerun that reproduces the bank prints the two commits and the count:

```text
flock_commit  d23f66846b528327acbeb38fc6ce90279fd8bd79  ->  7dd26fbb6cc5d6452355dfe1336d76e814af2873
compared: tasks 109, suites 10, breaches 0
```

A breach adds a line such as `piqa  metrics.acc_raw  0.6  ->  0.625  (+0.025)`.


## Refusals

A difference in score only means something if both runs scored the same requests with the same settings. `compare` refuses, before it reads any score, when:
- the provenance's `model`, `dtype`, `batch_tokens`, `max_length` or `attention` differ
- a task in both files has a different `task_hash` (see [The task hash](results#the-task-hash)) or `limit`
- a suite in both files has a different `metric`, so its score averages another metric
- no task is in both files
- a file isn't JSON, a field it reads is missing, or a value there has the wrong type

The message names the field and both values. Two runs that differ in a setting or a task hash need a new bank, not a looser check. A file that isn't a complete results file, such as one from a job killed while writing, needs a new run instead.
