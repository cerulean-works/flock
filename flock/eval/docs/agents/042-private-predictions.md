# 042: a private dataset's predictions upload under `private/`

| | |
| --- | --- |
| Status | built |
| Date | 2026-11-21 |
| Builds on | [041](041-result-files.md) (041.D1 uploads, 041.D3 `Dataset.private`, 041.D4 the check before the run, 041.D5 `read_results`); the app's stage 024 (`app/api/docs/agents/024-private-run-files.md`: run files under `private/` are members-only) |
| Amends | 041.D1 (a private task's predictions have another name on the run), 041.D5 (the reader looks there) |
| Keeps | 041.D2 to 041.D4, 041.D6, every task hash, every score, the run folder's layout, `results.json` schema 9 |

The file number and the decision numbers are provisional. The file takes the next free number when its PR is opened, and `042.D1` and `042.D2` take the next free global numbers after every stage merged before it.

## 1. Goal

041's first known gap: the privacy check and the upload are two requests, and the tracker kept no mark on the files, so a project made public later, or a run moved into a public project, served a private dataset's predictions to anyone. The tracker now keeps every run file under `private/` to the organization's members (app stage 024). This stage names a private task's predictions that way and reads them back from there.

**Out of scope:** any change to the folder `flock eval run` writes, to the rows, or to which datasets are private.

---

## 2. Design decisions

### 042.D1. A private task's predictions are `private/predictions/<task>.jsonl` on the run

`log_to_tracker` reads each task's `dataset.private` before it names the file, and uploads a private task's predictions as `private/predictions/<task>.jsonl`; public tasks keep `predictions/<task>.jsonl`. `WrittenResults.predictions` reads a private task from `private/predictions/` on a run, and never falls back to the public name, so a stray copy can't stand in for the real rows. The run's folder keeps every task in `predictions/`: the folder is never served, and a folder written by 041 reads as before.

041.D4 stays. A server older than app stage 024 serves the prefix to anyone, and a project made public there exposes the files, so the private project is still the first protection. The server must be deployed before clients upload under the prefix.

### 042.D2. A schema 9 results file is logged only as `results.json`

A backfill takes the predictions beside the results file. Two hand-renamed copies of results in one folder would both pick up the same `predictions/`, and the run would hold another run's rows. A schema 9 file named otherwise is refused before the run is created. `Results.write` always writes `results.json`, so this only refuses hand-renamed copies. Older schemas upload no predictions, so a renamed bank (such as `olmo2-1b-results.json`) still logs. Binding each predictions file to its results by a digest would also close it, but needs a new schema for the same protection.

---

## 3. Plan

1. Name the files by their task's mark in `_run_files`, which already reads every predictions file, and refuse a renamed schema 9 file there.
2. The reader's name for a private task on a run.
3. Tests with the fake tracker: the upload names, the refusal, a remote read of a private task, and no fallback to the public name.
4. The tracker runs section of the commands page, and the results page.

---

## 4. How it was actually built

- `_run_files` now reads the privacy mark (and refuses a damaged one, as `_private_tasks` did) while it gathers the files, so `_private_tasks` is gone: the private files are the names that start with `private/`. The refusal for a public project lists those names.
- `WrittenResults.predictions` tells a run from a folder by its `source`, which is `run:entity/project/id` for a run.

## 5. Known gaps

- A run on a server older than app stage 024 keeps 041's gap.
- A program that reads a run's files on the server for an outsider (such as a diff route) gets the private rows if it reads `private/` names without the server's check.
