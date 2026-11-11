# 036: registering a written results file with the tracker

| | |
| --- | --- |
| Status | built |
| Date | 2026-11-11 |
| Builds on | [014](014-run-provenance.md) (provenance), [016](016-cli-summary.md) (CLI), [018](018-bank-schema6.md) (`model_digest` in banked files), [023](023-deterministic-scoring.md) (`sdpa_backend`), the hub's `014-model-registry.md` (D106, D107, D109) |
| Keeps | `results.json` schema 8, every task hash and suite hash, every request, `compare`'s refusals, and the tracker run of a live `flock eval run --wandb-project` |
| Result | `flock eval log RESULTS --project P`, which logs a schema 6 to 8 file as written and refuses one already logged |

The file number and the decision numbers are provisional. The file takes the next free number when its PR is opened, and decisions `036.D1` and `036.D2` take the next free global numbers after every stage merged before it.

## 1. Goal

A reader should walk from a hub checkpoint to its scores, and from a score back to the run that trained it. The registry already stores each version's run and step. Every results file names the version it scored, by number, with its digest. The tracker can find a version's eval runs by their `config.model`. What's missing on the eval side is the scores themselves: most results were scored and banked as files, and only the runs started with `--wandb-project` reached the tracker. Rescoring them to get there would cost the GPU hours a second time. This stage logs a written file instead.

**Out of scope:** the training run's dataset and checkpoints (the trainers'), a hub table of eval links, retention, the dashboard, the instrument record of scores taken inside training (the trainer's, flock #94), and which project and organization compared evals share (pending the hub's convention).

---

## 2. Design decisions

### 036.D1. A result names its model version and nothing else of the chain

`provenance.model` is `flock:org/name@N`, a number after resolving, with `model_digest`. The run and step are read from the version, so they have one source. No field is added and the schema stays 8.

This follows how the registry already links a version to its run (hub D106), as MLflow's registry does: "Each registered model version is linked to the MLflow run, logged model or notebook that produced it" (mlflow.org, Model Registry). W&B models the same link as an edge in a graph ("Weights & Biases tracks the inputs and outputs of runs using directed acyclic graphs (DAGs) called *lineage graphs*", W&B docs, Explore artifact graphs); the hub keeps it as columns (`run_pk`, `step`) and removed its eval edge table (hub 014, section 7.5), so a filter on `config.model` matches the hub as built.

The decision holds while a scored version survives retention. Retention is off by default; if it is turned on for versions with eval runs, the fallback is a schema that copies the version's run and step into the provenance at resolve time.

### 036.D2. `flock eval log` logs a written results file as written

`log_to_tracker` builds its config and summary from the results' JSON form: `Results.to_dict()` for a live run, the file's own JSON for a backfill. A live run logs exactly what it logged before. A banked file logs its own provenance. There is no reader that turns a file back into `Results`, because a reader would have to default the fields older files lack.

- **Absent stays absent.** A field the file lacks is not added to the run. A schema 6 file has no `sdpa_backend`, which means PyTorch chose the kernel per batch (023), not `None` (no pin applied). Logging it as `None` would misstate the instrument.
- **Schemas 6 to 8 only.** These hold every field the run is built from (`model_digest`, suite hashes, dataset pins). Older files lack suite hashes, and some lack suites. Every banked table row we'd log is schema 6 or later.
- **A file already logged is refused.** One query on the project's run table, for a run whose `config.model` and `config.created_at` equal the file's. `created_at` is taken once per scoring run, so the pair names one run. `task_hashes` isn't matched: the tracker flattens it into one column per task. Runs logged live with `--wandb-project` match their own files, so the check covers them.
- **The query and the run go to one place.** The hub's wandb endpoint has no run query, and its REST run table refuses wandb keys, so the query uses the `flock login` credential. The run is then logged to that same server (`base_url`) and to the signed-in user's default organization (`entity`), whatever the SDK's own settings say. A live run still uses the SDK's settings.
- **The tracker's own stamps are the logging machine's.** The run's host, start time and git commit are those of `flock eval log`. The scoring job's are `config.created_at` and `config.flock_commit`.

Precedent: W&B's `wandb sync` uploads a run recorded locally after the fact ("Sync offline or incomplete runs to the W&B server", W&B CLI reference), but from the SDK's own run directory, not from a results file. OLMES writes results files and has no tracker backfill. Logging a results file after the fact has no precedent we follow.

---

## 3. Plan

1. `log_to_tracker` takes the results' JSON form, and `evaluate` passes `Results.to_dict()`.
2. `flock eval log RESULTS --project P`, with the duplicate check through the hub's run table.
3. Tests: schema 6, 7 and 8 files cut from banked results, logged through a fake `wandb.init` and a fake Flock API; a live run and its written file log the same config and summary; a missing field stays missing; a duplicate, a refused query and an older schema are refused before any run.
4. Docs: the commands page (`flock eval log`, its limits) and a pointer from the run guide.

---

## 4. How it was actually built

- **`tracking.py`** holds both functions. `log_to_tracker` reads plain dicts, and takes `entity` and `host` as keyword arguments that only the backfill passes, so a live run calls `wandb.init` with the SDK's defaults as before. `log_results_file` checks the schema, asks `me` for the default organization, queries `projects/{entity}/{project}/runs/query`, and logs.
- **A project that doesn't exist yet** answers 404 to the query. That counts as no duplicate, since the first run creates the project. Any other refusal stops the command before a run starts.
- **The Flock API client** is `flock.cli.api.Api`, with the credential from `flock.cli.credentials.resolve` (`flock login` or `FLOCK_TOKEN`). `flock-eval` now declares `flock-cli`, which brings `httpx`; until now it reached `flock.cli` only through the `flock` package.
- **The command** imports `tracking` when it runs, as `evaluate` does, so the other commands don't import wandb.
- **Fixtures** in `tests/eval/logged/` are cut from banked files: OLMo 2 1B's MC run (schema 6), its FineWeb run (schema 7) and Llama 3.2 1B's `tw_text` run (schema 8), each with its provenance as written and one suite's tasks.

## 5. Known gaps

- Until the project and organization for compared evals are agreed, a backfill lands in the logging user's default organization.
- Two files with the same model and `created_at` but different tasks (one run split by hand) would be refused as duplicates. No banked file is split that way.
- The check and the log are two requests, so two people logging the same file at the same moment could both pass the check.
