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

A task or suite in one file only is listed and is not a breach. So you can compare a rerun of a few tasks (without `--limit`) against the full bank.

### Changed tasks and suites

A task or suite in both files that was scored differently is listed as changed and isn't compared. Everything else is still compared and printed. A task is changed when its `task_hash` (see [The task hash](results#the-task-hash)) or `limit` differs. A suite is changed when its `metric` or `suite_hash` (see [Suites](results#suites)) differs, or when it holds a changed task or suite, directly or through another suite. A file written before schema 5 has no suite hashes, so its suites change only through their `metric` or a changed member.

For example, after the definition of `lambada` changed, the comparison of the old bank with a new `base_easy` run listed `lambada` and the suites that hold it (`base_easy:qa_rc`, `base_easy:qa_bpb`, and `base_easy` through them), and compared the other 108 tasks. A changed name makes the exit status `2`, as a refusal does, because the bank no longer measures that task or suite. Refresh the bank to compare it again (see [Compare with the regression bank](compare-regression#refresh-the-bank-after-an-intentional-change)).

The output lists, in order:
- the fields that differ and often explain a breach: the provenance's `flock_commit`, `device`, `sdpa_backend`, `torch`, `transformers`, `tokenizers`, `datasets` and `python`, and the file's `schema_version`
- the tasks and suites in one file only
- the changed tasks and suites, which aren't compared, each with the first field that differs (`members` for a suite that holds a changed task or suite)
- one line per breach, with the task or suite, the field, both values and their difference
- a count of what was compared

A rerun that reproduces the bank prints the two commits and the count:

```text
flock_commit  d23f66846b528327acbeb38fc6ce90279fd8bd79  ->  7dd26fbb6cc5d6452355dfe1336d76e814af2873
compared: tasks 109, suites 10, breaches 0
```

A breach adds a line such as `piqa  metrics.acc_raw  0.6  ->  0.625  (+0.025)`. A changed task adds a line such as `changed, not compared: lambada (task_hash), base_easy:qa_rc (members), base_easy:qa_bpb (members), base_easy (members)`.


## Refusals

A difference in score only means something if both runs used the same settings. `compare` refuses, before it reads any score, when:
- the provenance's `model`, `dtype`, `batch_tokens`, `max_length` or `attention` differ
- the provenance's `model_digest` differs, as for two Flock servers that each have a `flock:acme/olmo2-1b@12`. A file written before `model_digest` existed counts as `null`, the value for a Hugging Face model.
- the provenance's `sdpa_backend` differs, the SDPA kernel that changes BF16 scores. A file written before `sdpa_backend` existed is not refused on it: its kernel is unknown, and in FP32 the kernel changes no score, so an older FP32 bank stays comparable. Its missing field prints as `None` among the causes below, as other missing fields do.
- no task is in both files
- a file isn't JSON, a field it reads is missing, or a value there has the wrong type

The message names the field and both values. Two runs that differ in a setting need a new bank, not a looser check. A file that isn't a complete results file, such as one from a job killed while writing, needs a new run instead.
