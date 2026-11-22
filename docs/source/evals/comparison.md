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

A rerun on a newer layout than the bank's also prints the fields that came with it, such as `schema_version  6  ->  7` and, for a bank written before the SDPA pin, `sdpa_backend  None  ->  EFFICIENT_ATTENTION`.

A breach adds a line such as `piqa  metrics.acc_raw  0.6  ->  0.625  (+0.025)`. A changed task adds a line such as `changed, not compared: lambada (task_hash), base_easy:qa_rc (members), base_easy:qa_bpb (members), base_easy (members)`.


## Refusals

A difference in score only means something if both runs used the same settings. `compare` refuses, before it reads any score, when:
- the provenance's `model`, `dtype`, `batch_tokens`, `max_length`, `add_bos` or `attention` differ. A file written before `add_bos` existed reads as `false`, since no run before it added BOS.
- the provenance's `model_digest` differs, as for two Flock servers that each have a `flock:acme/olmo2-1b@12`. A file written before `model_digest` existed counts as `null`, the value for a Hugging Face model.
- the provenance's `sdpa_backend` differs, the SDPA kernel that changes BF16 scores. A file written before `sdpa_backend` existed is not refused on it: its kernel is unknown. Its missing field prints as `None` among the causes above, as other missing fields do. When both files have the field, a different value is refused in every dtype, so a CPU run, which records `null`, is refused against the banks, which record `EFFICIENT_ATTENTION`.
- no task is in both files
- a file isn't JSON, a field it reads is missing, or a value there has the wrong type

The message names the field and both values. Two runs that differ in a setting need a new bank, not a looser check. A file that isn't a complete results file, such as one from a job killed while writing, needs a new run instead.

## Doc-by-doc diff

`flock eval diff` and [`~eval.diff.diff`] compare two runs, usually of two different models, doc by doc rather than score by score. Each task's docs fall into four cells: both runs right, both wrong, only run A right, and only run B right. A doc's outcome is its `correct` field in the [predictions](results#predictions), the value the task's primary metric averages, so each run's right docs add up to its accuracy.

### Which tasks are compared

A task is compared when both runs scored it under the same `task_hash`, its primary metric is an accuracy, and both runs have its predictions with `correct`. Every other task is listed in `not_compared` with the reason:

| Reason | The task |
|---|---|
| `only_a`, `only_b` | was scored by one run only. |
| `task_hash` | has another hash in the other run, so it scored other requests: it's a different task. |
| `not_accuracy` | is read first by a metric with no per-doc outcome, such as `bpb` or `perplexity`. |
| `no_predictions` | has no predictions file in one of the runs. |
| `no_outcome` | comes from a run whose file is older than schema version 9, so its rows have no `correct`. A schema 9 row without `correct` is damaged, and refuses the diff. |

The diff gives counts only. No significance test comes with it.

### Refusals

The models may differ, and so may `max_length` and `add_bos`, which belong to the model (OLMES adds BOS for Gemma only). The diff refuses runs with another `dtype`, `batch_tokens`, `attention` or `sdpa_backend`, since a doc could then flip because of the instrument rather than the model. It also refuses a compared task whose predictions don't match its results: another row count than the task's `num_docs`, a row without a string `id` or a boolean `correct`, a predictions file that is a link, other doc ids or another order of them, a repeated id, or a share of right docs that isn't the task's accuracy (a missing or non-finite accuracy refuses too).

### JSON

`flock eval diff --json` prints [`~eval.diff.ItemDiff.to_dict`]:

```text
{
  "a": "allenai/OLMo-2-0425-1B@a1847dff35000b4271fa70afc5db10fd29fedbdf",
  "b": "meta-llama/Llama-3.2-1B@4e20de362430cd3b72f300e6b0f18e50e7166e08",
  "tasks": [
    {
      "task": "arc_easy",
      "task_hash": "<sha256>",
      "metric": "acc_per_char",
      "num_docs": 2376,
      "both_right": ...,
      "both_wrong": ...,
      "only_a": ["<doc id>", ...],
      "only_b": ["<doc id>", ...]
    },
    ...
  ],
  "not_compared": [["fineweb_en", "not_accuracy"], ...],
  "schema_version": 1
}
```

| Field | Meaning |
|---|---|
| `a`, `b` | Each run's `provenance.model`. |
| `tasks` | One entry per compared task, in run A's order. `only_a` and `only_b` hold doc ids in A's doc order, and their lengths are the two disagreement counts. |
| `not_compared` | `[task, reason]` pairs, A's tasks first, then B's. |
| `schema_version` | The layout of this JSON, bumped when a field changes. |

In the dashboard, the same diff runs on Flock from two logged runs' files: check two eval runs in a project's table and click **Compare items** ([Eval results in the dashboard](../app/explore-runs#compare-two-eval-runs)).

## Reading items

The diff says how many docs each model got right. [`flock eval items`](commands#flock-eval-items) and [`~eval.items.items`] show which ones and why: each doc's question and choices from the [predictions](results#predictions), with what each run picked and how likely it found each choice. One run or two are read, and the JSON holds a list of runs per doc.

`flock eval items --json` prints one [`~eval.items.Item.to_dict`] per line:

```json
{"task": "arc_easy", "metric": "acc_per_char", "id": "Mercury_7017010", "question": "Question: Which tools are needed to measure the effects of friction on a block of wood?\nAnswer:", "contexts": null, "choices": [" meter stick, spring scale", " balance, stopwatch", " balance, meter stick", " meter stick, stopwatch"], "gold": 0, "runs": [{"model": "allenai/OLMo-2-0425-1B@a1847dff35000b4271fa70afc5db10fd29fedbdf", "pick": 3, "correct": false, "logprob": [-11.73, -7.96, -9.27, -9.11], "prob": [0.014, 0.622, 0.167, 0.196], "share": [0.244, 0.252, 0.246, 0.258]}, {"model": "meta-llama/Llama-3.2-1B@4e20de362430cd3b72f300e6b0f18e50e7166e08", "pick": 0, "correct": true, ...}]}
```

| Field | Meaning |
|---|---|
| `task`, `id` | The task and the doc. |
| `metric` | The task's primary metric, which `pick`, `correct` and `share` follow. |
| `question`, `contexts`, `choices`, `gold` | The doc's text as scored and its correct choice, as in the [predictions](results#predictions). |
| `runs` | Each run's answer, in the order the runs were given. |
| `runs[].pick`, `runs[].correct` | The choice the metric picks, and whether it is gold. `pick` is `null` for `acc_greedy`, which ranks no choices. |
| `runs[].logprob` | Each choice's summed log-probability. |
| `runs[].prob` | Each choice's probability renormalized over the choices: the softmax of `logprob`. |
| `runs[].share` | The softmax over the choices of the score the metric ranks them by: `logprob` for `acc_raw` (so it equals `prob`), `logprob` per token, character or byte (in bits) for the length-normalized accuracies, and `logprob` minus `logprob_uncond` for `acc_uncond`. `null` for `acc_greedy`. |

`share` is not a probability under a length-normalized metric. It shows how the metric ranks the choices, and its values sit close together, since a score per character varies little between choices. OLMES and OLMo 3 define no per-choice share, so read `prob` for how likely the model found each choice, and `share` and `pick` for what the metric made of it. In the example, OLMo found choice 1 most likely but `acc_per_char` picked choice 3. `pick` is the scorer's own, from its FP32 scores, while `share` is computed from the rows in double precision, so at a tie or near tie its largest value can sit on another choice.

### Reading failures with an agent

The JSON lines are made for a language model to read as well as a person. A useful pattern is to sample a model's failures, then ask an agent what they have in common:

```bash
uv run flock eval items results/olmo2-1b --task arc_easy --only wrong --limit 50 --seed 0 --json > wrong.jsonl
uv run flock eval items results/olmo2-1b results/llama32-1b --task arc_easy --cell only_b --json > only-llama.jsonl
```

`--seed` makes the sample the same each time, so two people, or two agents, read the same docs. The few-shot examples every doc was shown are in the first line of the task's predictions file, not in the items.
