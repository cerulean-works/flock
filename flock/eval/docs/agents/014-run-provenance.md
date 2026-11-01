# 014: honest provenance and complete results on the run path

| | |
| --- | --- |
| Status | in review |
| Date | 2026-10-27 |
| Builds on | [001](001-eval-core.md) (D7: the task hash, D8: provenance), [002](002-truncation-count.md) (truncation counts), [004](004-suites.md) (suites) |
| Keeps | every score, request and task hash; adds to `results.json` and the predictions files |
| Result | `results.json` schema 5 (`dataset` per task, `suite_hash` per suite, `tokenizers` and `datasets` versions), complete prediction rows, provenance taken at the start, earlier and clearer refusals |

## 1. Goal

A results file should let a reader trace every number to the code, data and settings that made it, and recompute it. A sweep of the run path found small gaps against that: the predictions file lacks the lengths that `acc_per_char` and `bpb` divide by, the provenance claims `"python"` as the command of every API run, records the HEAD of whatever repository the package happens to sit in, ignores new untracked modules, and is taken after scoring, so its dirty flag can describe a tree that changed while the run went on. A results file can't say which dataset revision a task read or whether two suite scores cover the same tasks. A few mistakes are also caught late or silently: a bad `batch_tokens` after the weights load, a second `score_task` into the same directory overwriting predictions, a tracker run left open when logging fails.

None of this changes a score. Every change here either adds a field or refuses earlier.

**Out of scope:** scoring, batching, the task hash and the request strings (locked: unchanged); streaming scores to the tracker; a hash of the CLI's own arguments.

---

## 2. Design decisions

### 014.D1. A prediction row holds every input of every number the task reports

Each row of `predictions/<task>.jsonl` gains `num_chars`, `num_bytes` and `tokens_cut` per choice, and `tokens_cut_uncond` beside `logprob_uncond` for tasks that score PMI. With `logprob`, `num_tokens` and `logprob_uncond` already there, every metric of `multiple_choice` and all three truncation counts can be recomputed from the file alone; a test does exactly that. `ChoiceScores` gains `tokens_cut` and `tokens_cut_uncond`, gathered by `choice_scores` like the other per-request fields, so `prediction_rows` keeps one input. `prediction_rows` raises if the scores don't hold one row per doc, since rows are read by doc position and extra score rows would be dropped silently.

*Prior work:* lm-evaluation-harness v0.4.3 logs each request's full response with `--log_samples` (`lm_eval/evaluator.py:493`, `resps`), so per-doc metrics can be recomputed. OLMES (`5a51f50`) keeps per choice `sum_logits`, `num_tokens`, `num_chars` and the derived `bits_per_byte` in `predictions.jsonl` (`oe_eval/metrics/metric.py:39,106,236-241`), but no byte count; we keep the counts and derive nothing, so a row is the raw inputs only.

### 014.D2. Provenance is collected once the model has loaded, before any task is scored

`evaluate` calls `Provenance.collect` right after `ScoringModel.from_pretrained`. The commit's dirty flag then describes the code at the start of scoring (interactive runs only: job scripts set `FLOCK_COMMIT`), and `created_at` is when scoring started, not when the process started: a cold download can come first, and the modules were imported before it. A run that crashes mid-way loses nothing by this, since `results.json` is written at the end either way.

*Prior work:* lm-evaluation-harness v0.4.3 takes `start_date = time.time()` before evaluating and records it as the results' `date` (`lm_eval/evaluator.py:131,327`).

### 014.D3. The provenance never claims a command that did not run

`EvalConfig.command` defaults to `None` (`null` in `results.json`), not `"python"`. The CLI passes its command line as before: `sys.argv` with the program name written `flock-eval`, so a local install path doesn't land in the file. A caller from Python who wants a command recorded passes its own string. No precedent; a provenance field should be true or empty.

### 014.D4. The commit comes from git only when the package is a flock checkout, and new files under `flock/` make it dirty

Without `FLOCK_COMMIT`, `flock_commit` asks git for the top level of the package's directory and records `"unknown"` unless the package is that top level's `flock/eval`. A non-editable install in a virtualenv inside some other repository would otherwise record that repository's HEAD. The dirty check now sees untracked files: changes to tracked files count anywhere (the lockfile and `pyproject.toml` sit at the root), new files only under `flock/`, where an uncommitted module may have run; a scratch script or an output beside the code doesn't. New files are listed with `git ls-files --others --exclude-standard --directory --no-empty-directory -- :/flock`, so a user's `status.showUntrackedFiles` setting can't hide them, quoted names don't matter (only emptiness is read), git doesn't walk untracked directories outside `flock/`, and a directory with no new file in it (an empty one, or the `__pycache__` a deleted module leaves) doesn't count. No precedent.

### 014.D5. The `tokenizers` and `datasets` versions are recorded

`tokenizers` decides token boundaries, so it changes `num_tokens`, the truncation counts and every score of a model whose tokenizer it runs; `datasets` reads the rows. Both join `torch` and `transformers` in the provenance. `tokenizers` was only a dependency of `transformers` until now, so `flock-eval` declares it.

### 014.D6. Each task records its pinned dataset next to its hash

`TaskResult.dataset` is the task's `Dataset` (`repo`, `revision`, `subset`, `split`, `row_index`), written as an object. The task hash already covers it, but a hash can't be read back: two results files with different hashes didn't say whether the data moved. The object is the definition itself, so there is no string format to parse.

### 014.D7. A suite's hash covers each member's hash, all the way down

`SuiteResult.suite_hash` is the sha256 of `json.dumps({"suite": name, "metric": metric, "scored": scored, "members": [[name, hash], ...]}, sort_keys=True)`, keys sorted as the task hash sorts them, where a member task gives its `task_hash` and a member suite its own `suite_hash`. `evaluate` computes it after scoring, since a task hash depends on the docs loaded and the `limit`, and the suites are already ordered members first. Equal suite hashes mean the same members, nested the same way, scored the same requests, so a `limit`, a changed task or a grown member list all show. The `results.md` rule for comparing suite scores becomes "compare only equal `suite_hash`es", and the tracker config logs `suite_hashes` beside `suite_members` so dashboard runs can be checked the same way. No precedent: lm-evaluation-harness v0.4.3 records `task_hashes` per task only (`lm_eval/loggers/evaluation_tracker.py:185-195`), and OLMES a config hash per task.

### 014.D8. `schema_version` is 5, and a caller can't set it

The new fields bump the layout to 5. `Results.schema_version` becomes `field(default=5, init=False)`: it stays in the same place in `asdict`'s output, and a file can no longer claim a layout it doesn't have.

### 014.D9. Throughput is `null` when the clock measured no time

`TaskResult.docs_per_second` returns `None` rather than `0.0` when `seconds` is 0, so a zero isn't read as a measurement. The tracker summary leaves it out and the CLI prints no speed for that task.

### 014.D10. Mistakes are refused before work, and the refusal says what to do

- `ScoringModel.from_pretrained` checks `batch_tokens` before it resolves or downloads anything. `__init__` still checks it for direct construction, and `EvalConfig` too, since a config is plain data that may be built where no model loads.
- `score_task(out=...)` refuses an existing `predictions/<task>.jsonl` before scoring, as `evaluate` refuses a non-empty `out`, and `write_predictions` and `Results.write` open their files with mode `x`, so a file another run created while this one scored is refused, not overwritten. Rows are built before the file is created, so a failed check leaves no empty file behind. A training loop that scores between steps gives each step its own `out`. Both errors say why (one directory holds one run; a crashed run leaves predictions behind) and what to do (a new directory, or delete this one).
- `ModelRef.parse` rejects a second `@`, which would otherwise become part of the revision.
- `evaluate` refuses a suite that shares a name with a task before the model loads: results and suite hashes look members up by name.

### 014.D11. A tracker run is finished even when logging fails

`log_to_tracker` opens the run with `with wandb.init(...) as run:`. If the summary update raises, the run is finished with exit code 1 instead of left running. *Prior work:* wandb 0.31.0's `Run.__exit__` calls `_finish(exit_code=1)` when an exception escapes (`wandb/sdk/wandb_run.py:3678`).

---

## 3. Plan

1. `metrics.py`, `tasks/base.py`: `ChoiceScores.tokens_cut` and `tokens_cut_uncond`, gathered in `choice_scores`.
2. `results.py`: `TaskResult.dataset`, `SuiteResult.suite_hash`, `docs_per_second` as `None`, `schema_version` 5 and not an argument, complete `prediction_rows` with its length check.
3. `provenance.py`: `command` optional, the checkout check, untracked files under `flock/`, two more versions, `created_at` as the start.
4. `run.py`: provenance before scoring, the suite hash, `command` default `None`, the predictions refusal and both error messages. `model.py`, `tracking.py`, `cli.py`: D10, D11 and D9's callers.
5. Tests for every item, a test that recomputes every metric and the truncation from the predictions file, and a lock: scores, token counts and hashes byte-equal on the tiny model before and after.
6. Docs: `results.md` (example, suite hash, provenance, predictions table) and `cli.md` (tracker summary).

---

## 4. How it was actually built

- **Two tasks under one name** were on the list as well. They already raise at import: the registry is built with `by_name`, which refuses two different definitions under one name (004). Nothing to add.
- **The recompute test** scores the toy task with `max_length=16`, so the few-shot contexts are cut, then recomputes all six metrics in plain Python from the written rows (within 1e-6 of the float32 means) and the three truncation counts exactly.
- **The suite hash test** pins the formula once for one suite, then checks the behaviour: two runs give the same hashes, two suites with the same members and different metrics differ, a `limit` moves every suite, and replacing one member suite moves it and its parent but not its sibling.
- **The commit tests** build a one-commit git repository laid out like flock in a temporary directory and point the module at it: a scratch file at the root leaves the commit clean, a new file under `flock/eval` with a name git quotes makes it dirty even with `status.showUntrackedFiles=no`, a changed tracked file makes it dirty, and a copy of the package under `.venv/site-packages` inside that repository records `"unknown"`.
- **Review.** Three review rounds before the pull request opened, and one over the whole pull request after `main` was merged in. The first moved the untracked-file check from `git status` to `ls-files` (a user setting and quoted names had hidden files), made the write exclusive, logged `suite_hashes`, and corrected what `created_at` means. The second made `results.json` exclusive too, built rows before creating the file, refused suite and task name clashes, checked the root before scanning, declared `tokenizers`, and fixed the training docs, which still said `score_task` replaced a file. A third round found nothing serious; from it `results.json` is serialized before its file is created, the suite hash sorts its keys, new directories under `flock/` are listed once each, and the hash test isolates the metric. Declined across rounds: a revision containing `@` (git allows it, but a second `@` was ruled out on purpose: pin the commit hash instead), refusing an existing file passed as `out` before scoring, one host copy for all prediction fields, and logging each task's dataset to the tracker. Also declined: one shared helper for the three `batch_tokens` checks (each layer is checked on purpose, see D10), a `Suite.hash` method (the hash needs the run's task hashes, which only `evaluate` holds), and folding `MCRequests.truncation` into `ChoiceScores` (see Known gaps). The last round found that `--directory` counted an empty directory, or one holding only an ignored `__pycache__`, as a new file; `--no-empty-directory` fixed it.
- **The tracker test** fakes `wandb.init` with a run that records how its `with` block ended, and a summary whose `update` raises.
- **`compare` prints the new versions.** `tokenizers` and `datasets` join the provenance fields `compare` prints when they differ (013.D2). The regression bank stays at schema 4 until its next refresh, so a schema 5 run compared with it prints both versions as `None` on the bank's side. `compare` does not yet check the suite hash of D7; the bank's refresh adds that.
- **A correction to stage 013.** Its "How it was actually built" credits the tolerances to review and a second review pass. They were asked for in Ak's comment on #46 and decided by Harry, and one review round ran on them, not two. The values are argued, not measured, as 013's D1 says; the first bank run on another GPU type or library stack measures the drift and tightens them.
- **Lock.** On the tiny model, scorer outputs at two batch sizes, the toy and ARC-Easy hashes (`82293825`, `ad683d6e`) and every value both sides hold are byte-equal before and after; the differences are exactly the new keys (`tokens_cut`, `tokens_cut_uncond` in the scores; `tokenizers`, `datasets`, `dataset`, the new prediction fields and `schema_version` 5 in the outputs). The task snapshot regenerates unchanged.

## 5. Known gaps

- `MCRequests.truncation` still maps requests to docs with its own index, while `ChoiceScores` now carries the per-choice cuts it could count from. Folding one into the other is a refactor and waits for a refactor-only change.
