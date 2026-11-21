# 041: result files on the run, and a doc-by-doc diff of two models

| | |
| --- | --- |
| Status | built |
| Date | 2026-11-21 |
| Builds on | [013](013-score-compare.md) (`compare`), [014](014-run-provenance.md) (provenance), [034](034-hub-copies.md) (private dataset copies), [036](036-eval-log.md) (`flock eval log`), the hub's run files routes and its `0016_public_projects.sql` (projects start private) |
| Keeps | every task hash and suite hash, every request and score, every metric in `results.json`, `compare` and its refusals, a live run's config and summary |
| Result | runs upload `results.json` and `predictions/`; prediction rows carry `correct`; `read_results` reads a folder or a run; `flock eval diff A B` splits each task's docs into four cells; `results.json` schema 9 |

The file number and the decision numbers are provisional. The file takes the next free number when its PR is opened, and decisions `041.D1` to `041.D6` take the next free global numbers after every stage merged before it.

## 1. Goal

A reader should open an eval run in the app and see where a model fails, and put two models side by side doc by doc. Today a run on the tracker holds its config and summary only. `results.json` and the predictions stay on the disk of the job that scored them, no code reads them back, and `compare` refuses two different models by design, since it checks a rerun against a bank. This stage puts the files on the run, gives every prediction row its outcome, and adds the reader and the diff that the app's pages build on.

**Out of scope:** the app's routes and pages, launching evals from the app, any change to prompts, few-shot settings or metrics, uploading item text, and making anything public.

---

## 2. Design decisions

### 041.D1. A run uploads `results.json` and its predictions as run files

`log_to_tracker` takes the written `results.json` (`results_file`) and puts it on the run as `results.json`, with `predictions/<task>.jsonl` for each task that has one, through the SDK's `run.save` (the wandb protocol's `CreateRunFiles`, which the tracker serves at its run files routes). Each file is copied into the run's folder under its name on the run, so a banked copy named otherwise still arrives as `results.json`. `evaluate` passes `out/results.json` when `out` is set, and `flock eval log` passes the file it logs.

This follows OLMES, whose runner writes a `predictions.jsonl` per task beside its metrics (`oe_eval/run_eval.py:732`, allenai/olmes `5a51f50`) and uploads the whole output folder (`upload_directory`, line 1089), and lm-evaluation-harness's `--log_samples` ("write out all model outputs and documents for per-sample measurement and post-hoc analysis", `lm_eval/__main__.py:163`, `v0.4.3`). Rows hold ids, gold indexes and scores, never item text.

A file older than schema 9 uploads `results.json` only: its rows have no outcome (D2) and its tasks no privacy mark (D3).

### 041.D2. A prediction row's `correct` is the per-doc value its primary metric averages

For a task whose primary metric is an accuracy (`acc_*`), each row gains `correct`, the doc's value of that metric from the tensor `score_prepared` just averaged into `results.json`. It is derived once, in the scorer, so a row and the task's accuracy can't disagree. Tasks read first by `bpb` or `perplexity` get no `correct`: they have no per-doc right or wrong. One field, not one per accuracy metric: the primary metric is the one a reader reads first, and a different metric is a different comparison.

OLMES keeps a per-instance `metrics` dict whose `acc_raw` is 0 or 1 and averages it into the task score (`oe_eval/metrics/metric.py`, `base_output_keys` line 39, the mean at line 147). The schema goes to 9.

### 041.D3. `Dataset.private` marks a private repo, outside the task hash

A run in a project has the project's visibility, and anyone can read a public project's run files. So a task whose rows are private (exam sets, crawled text, licensed mirrors) must say so. `Dataset` gains `private: bool = False`, set on the six repos that answer an anonymous read with 401 on the Hugging Face Hub (checked 2026-11-21): `cmmlu`, `fineweb-2-zh-dev`, `tc-edu-exams`, `tw-mock-exams`, `tw-text-v0` and `tw-text-v1`. The factories that build those datasets (`Benchmark`, `exam_task`, `tw_text_suite`) take it as an argument.

Who may read the rows changes no request, so both task hashes drop the field, as the perplexity hash already drops `primary_metric`. Every hash is unchanged, which the task snapshot shows: its diff adds `"private"` to each definition and nothing else.

### 041.D4. A private dataset's predictions go only to a private project, or the run is refused

Before the run is created, a log that would upload predictions of a private task reads the project from the Flock API (`GET projects/{entity}/{project}`). It continues when `visibility` is `private`, or when the project doesn't exist yet (a run creates it, and projects start private). It refuses with `ValueError` when the project is public, when no Flock sign-in exists, or when Flock refuses the query. The run then goes to the server and organization that were checked, as `flock eval log` already does (036.D2). Refusing the whole log, not skipping the private files, means a run is never half uploaded, and `results.json` is already on disk when a live run logs.

### 041.D5. `read_results` reads a folder or a run, schemas 8 and 9

`read_results(source)` takes a folder or `run:<entity>/<project>/<id>` and returns `WrittenResults`: the schema version, the provenance and tasks and suites as their own types, and `predictions(task)`, which reads one task's rows when asked (a full run has hundreds of predictions files, and a diff reads few). A run is read through the run files route as the `flock login` user. The explicit `run:` prefix means a mistyped folder never turns into a network call.

036.D2 avoided a reader because it would have to default the fields older files lack. Schema 8 has every field these types hold but `dataset.private`, which reads as `False`, and `WrittenResults` keeps `schema_version` so a caller can tell. Older schemas are refused. A metric written as `null` (non-finite) reads back as NaN.

### 041.D6. `diff` compares two models doc by doc under equal task hashes, with counts only

`diff(a, b)` compares every task both runs scored under the same `task_hash` with an accuracy as its primary metric and `correct` in both runs' rows, and lists every other task with its reason (`only_a`, `only_b`, `task_hash`, `not_accuracy`, `no_predictions`, `no_outcome`). A task with another hash is another task (principle of one instrument). Each compared task gives both-right and both-wrong counts and the ids of the docs only one run got right.

- **The models may differ; the instrument may not.** `dtype`, `batch_tokens`, `attention` and `sdpa_backend` must be equal, as `compare` requires. `max_length` and `add_bos` belong to the model (OLMES adds BOS for Gemma only) and may differ.
- **Rows must match their results.** Equal hashes mean the same docs, so another row count than `num_docs`, a row without a string `id` or a boolean `correct`, another id sequence (rows follow the scored requests, so order counts), a repeated id, or a share of right docs more than 1e-6 from a finite accuracy (the FP32 mean of 0s and 1s rounds once) refuses the diff: one of the files is damaged or mixed up.
- **Counts only.** No significance test: no paired test is established in the suite to cite, so the diff reports the cells and leaves inference to the reader.
- **The JSON is a contract.** `ItemDiff.to_dict()` is what `flock eval diff --json` prints and the app renders, with its own `schema_version` (1).

---

## 3. Plan

1. `Dataset.private`, kept out of both hashes; the six private repos marked; the snapshot regenerated.
2. `correct` in `prediction_rows`, passed from `score_prepared`; schema 9.
3. Uploads and the privacy check in `log_to_tracker`; `flock eval log` uploads too.
4. `read_results`, `WrittenResults`, and `read_run_file` for a run.
5. `diff`, `ItemDiff`, `TaskDiff` and `flock eval diff`.
6. Tests on the tiny models and toy tasks; docs on the results, commands, comparison and Python API pages.
7. One GPU job: two banked models on Base Easy, logged to the tracker, read back from the runs, diffed, and compared with the bank.

---

## 4. How it was actually built

- **`results.py`** writes `correct` as a JSON boolean from one host copy of the tensor, beside `id` and `gold`. `read_results` builds the dataclasses from the JSON, and its folder reader returns `None` for a missing file so a folder and a run behave alike.
- **`tracking.py`** imports the Flock API client at the top now: reading a run and checking a project need it, and `wandb` already costs more. `_signed_in` resolves the `flock login` credential, asks `me` for the default organization, and returns a fresh client, because an `httpx` client can't be opened again once closed. A file is read from `projects/{entity}/{project}/runs/{id}/files/{name}`; a 404 saying `file not found` is a missing file, and any other refusal raises.
- **Uploads** are copied into `run.dir` and saved with `policy="now"`, so each starts at once, and leaving the run's block waits for them.
- **Always a new run.** `wandb.init` returns an active run by default outside notebooks (`reinit="return_previous"`), so inside a training script the eval summary and files would have gone to the training run, in its project, past the visibility check. `log_to_tracker` passes `reinit="create_new"`.
- **Names, links and marks from a file are checked.** A task name must be one file name, on upload and on read, so a results file can't point outside `predictions/` or the run's folder, and a predictions file or folder that is a link is refused, so no other local file goes up or is read under a task's name. A non-numeric metric refuses the read. A schema 9 task with predictions must have a boolean `dataset.private`; anything else refuses rather than passing as public.
- **The run is checked after it starts.** A sweep or launch agent can replace the entity and project given to `wandb.init`, so before copying private predictions the run's own `entity` and `project` must equal the checked ones, or the run finishes as failed with nothing uploaded.
- **A disabled SDK stages nothing.** With `WANDB_MODE=disabled` the run is a no-op, and no file is copied for it.
- **Only a backfill is forced online.** A live run with private predictions is sent to the checked server, but keeps the SDK's `WANDB_MODE`. An offline run with private predictions is refused before anything is staged, since `wandb sync` can send it to any project; `flock eval log` uploads the folder later, online and checked.
- **Reading a run file** resolves the credential only, with no organization lookup: the run names its own, and a diff reads one file per task.
- **`diff.py`** is its own module beside `compare.py`: it shares the instrument check's idea but not its inputs, since `compare` reads raw JSON of any schema and `diff` reads typed results.
- **The CLI** prints a table by default and the JSON with `--json`, and exits 2 on a refusal, as `compare` does.

## 5. Known gaps

- The visibility check and the upload are two requests, and the tracker keeps no mark on the files: a project made public during or after the upload serves the private predictions to anyone. The same holds when a run is moved to a public project. Closing this needs the tracker to remember which files are private and refuse outsiders, or refuse to make such a project public or move such a run.
- A live run with private predictions contacts Flock for the check even under `WANDB_MODE=disabled`, and refuses without a sign-in. Passing `--wandb-project` while the SDK is disabled asks for both at once.
- A schema 8 file reads every dataset as not private. It is never uploaded with its predictions, so this only matters to a caller that reads `dataset.private` from an old file.
- `read_run_file` needs a Flock sign-in, even for a public project.
- The multiple-choice task hash covers requests and gold indexes, not doc ids. The diff closes the gap for itself by requiring both runs' ids in the same order, but the hash alone doesn't say two runs named their docs alike. Covering ids would change every hash and every bank.
- Doc ids are listed in full. A task with many thousands of disagreements makes a long JSON; the app may page it.
