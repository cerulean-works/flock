# 023: eval results, model links and item comparison in the dashboard

| | |
| --- | --- |
| Status | implemented, pending review |
| Date | 2026-11-21 |
| Builds on | [014](014-model-registry.md) (D106, D108, D109 and section 7.5), [015](015-public-projects.md) (D110 to D116), [021](021-public-access-checks.md); eval's stage 041 (`flock/eval/docs/agents/041-result-files.md`: `results.json` and `predictions/<task>.jsonl` on the run, `correct`, `flock eval diff`) |
| Amends | D106 (eval links come back, matched by number and digest), D109 (retention keeps a version with live eval runs again), D108 (the model page gets its Evals tab back, the run page its **Models scored**) |
| Keeps | D97 to D105, D107, D110 to D118, D130 to D135, D151 to D157 |

## 1. Goal

`flock eval run --wandb-project` logs an eval run whose files are its `results.json` and one
predictions file per task (eval's stage 041). The dashboard shows none of it: a run page has
charts, logs and a file list, and a model version doesn't say which runs scored it. This stage
adds three things:

1. **Results.** An eval run's page gets a **Results** tab: each suite's score, each task's
   primary metric, its number of docs, task hash and dataset, and the provenance (D173).
2. **Model links.** A model version lists the eval runs that scored it, with their suite and task
   scores, and an eval run's page names the version it scored (D171, D172).
3. **Item comparison.** Two eval runs, picked from the runs table or a version's Evals tab, give
   a page that splits each task both scored the same way into four counts (both right, both
   wrong, only A right, only B right) and lists the ids of the docs only one got right (D174).

**Out of scope:** launching evals from the dashboard; datasets and models that belong to a
project (022 on another branch); fetching a doc's text; any change to how `flock eval` scores.

---

## 2. Design decisions

The numbering continues from 021. 022 (folders and project scopes, D158 to D170) is on another
branch; whichever stage merges second takes the next stage and migration number.

### D171. An eval run links to the version whose number and digest it records

- **Restored from 014.** `model_version_runs (version_pk, run_pk, kind)` comes back as Ak first
  built it (`14bd505`, removed in 7.5), with `kind` still only `'eval'`. 7.5 removed it because
  nobody would use it yet; this stage is that use.
- **Why a table, not a query per page.** The run's config is kept as wandb's JSON text, so
  finding a version's runs per request means parsing the config of every run in the org. The
  link is written once, in the transaction that stores the config, and answered by an index. It
  also lets retention see a version's eval runs (below), which a page-time query can't.
- **The match.** When a run's config is stored and its `model` is `flock:{org}/{name}@N` (a
  version number, never a tag) and its `model_digest` is 64 hex characters, the candidates are
  the live versions numbered `N` of live models in the run's own org whose digest is that one.
  The named model's version links if it is one of them. Otherwise the run links only when there
  is a single candidate: a model renamed or moved since the run (D134, and 022's folders) still
  gets its link, and the same bytes saved as two models never link to the one the run didn't
  name. A ref that matches nothing links nothing, and the run is stored as usual. 014's other rules stay: an insert that exists changes nothing, a
  config that later names another version adds a second link, imported runs aren't linked, and
  links aren't gated on `job_type`.
- **The migration** is an expand file. A database that applied the old 0015 still has an empty
  `model_version_runs` (7.5), so it creates the table and its index `IF NOT EXISTS`.
- **Older runs.** A run whose config was stored before this release links the next time its
  config is sent. No backfill: before this stage no eval run carried result files, and registry
  models were scored only in tests.
- **Retention (D109) keeps a version with a live eval run,** as 014 first wrote it, so a score a
  page shows never points at a deleted version. The org settings' docstring already says so.
  Linking holds a share lock on the version and retention an update lock, so a link made while
  the sweep runs is either seen by it or refused because the version is gone.

### D172. A version's Evals tab reads each run's results file

- **Restored from 014** (7.4) with one change: rows are the scores `results.json` names, not
  every numeric summary value. A run's summary holds every metric of every task (about 600 values
  for Base Easy), and only the results file says which one is a task's primary metric.
- **Rows:** each suite with a score, then each task's primary metric, labelled with the metric.
  A row is one name under one hash: a task two runs scored under different task hashes gets two
  rows, each showing its hash, so numbers side by side are always comparable (principle 3).
  **Columns:** the eval runs, linked to their pages. A run without a usable results file shows
  dashes; one the viewer can't read (deleted since) is left out, as in 014.
- **The dashboard's server** reads the version and then each run's `results.json`, eight at a
  time, as 7.4 did with summaries, with the viewer's own credentials.
- **Compare:** checking two runs' columns offers **Compare items** (D174).
- **The run page's Models scored** (7.4) comes back in the Overview sidebar, from `scored` in
  `GET …/runs/{run}/models`.

### D173. A Results tab on an eval run

- **Shown when** the run has a `results.json` the page can show, whatever its `job_type` says
  (the field can be edited). Older eval runs have none, and other runs never do. The file is the
  run's own upload, so its shape is checked first (`isEvalResults`); a damaged file, or a Flock
  that fails to answer, leaves the tab out rather than failing the page.
- **What it reads:** `results.json` through the run's file route, by the dashboard's server, so
  a private project's file stays behind the project's access check (D112). No new API route.
- **What it shows:** suites (score, metric, members, hash), then tasks (primary metric and its
  value, `num_docs`, task hash, dataset `repo@revision` with a private mark, truncation counts),
  then the provenance (model, digest, instrument, versions, command, time). The dataset shows as
  its repo and subset, with the revision and split on hover. Hashes show their
  first 12 characters, with the whole value on hover and a copy button.
- **Schemas 8 and 9** are read, as eval's reader does. Another schema shows a note, not a table.

### D174. Item comparison runs eval's own diff, on the server

- **One instrument.** The counts and ids are exactly what `flock eval diff --json` prints for the
  same two runs: the API calls `flock.eval.diff.diff` on the two runs' files, so the dashboard
  never has a second implementation of the rules (equal `task_hash`, primary accuracy, schema 9,
  equal instrument, equal ordered ids) (eval's 041.D5).
- **The route:** `GET /api/v1/projects/{entity}/{project}/runs/{run}/diff?with={entity}/{project}/{run}`,
  on the public router like the run's file route. Both runs must be readable by the caller under
  D112: either one unknown or unreadable answers the same 404. A run without `results.json`
  answers 404 naming it. A refusal of the diff (other instrument, damaged or mismatched files)
  answers 409 with eval's message. The answer is `ItemDiff.to_dict()`, schema 1, unchanged.
- **Files are read on the server.** Reading the files of two Base Easy runs over HTTP took about
  3 minutes (220 requests); here the diff reads each file from the file store as it needs it
  (only the tasks both runs can compare), so nothing goes through the client. It runs in a pool
  of four threads of its own: a file read needs the default executor's threads, so a diff
  waiting in one of them could deadlock the server, and four at a time bound the memory held.
- **The page:** `/{entity}/{project}/runs/{run}/compare?with={entity}/{project}/{run}`. A header
  names both runs and their models; a table has one row per compared task (task, hash, docs, the
  four counts); a row opens to the two id lists; tasks not compared are listed with eval's
  reason in words. A pair that can't be compared (no results file, another instrument) shows
  the reason on the page. **Entry points:** the runs table's selection bar shows **Compare
  items** when exactly two runs are selected, and a version's Evals tab when two runs are
  checked. Both are for members, as selection is (D130); a public project's visitor reaches the
  page by its link.

### D175. Eval's results reader imports no torch

The API image has neither torch nor `flock.eval` (its Dockerfile copies `flock/hub` and two CLI
modules). So the modules the diff needs become importable without torch, `transformers` or
`datasets`, and the image copies them:

- `results.py`'s write-side types (`torch.Tensor`, `ChoiceScores`, `Scores`, `MCDoc`, `TextDoc`)
  are annotations only, imported under `TYPE_CHECKING`.
- `Dataset` moves from `tasks/base.py` to `flock/eval/dataset.py`, importing `datasets` and
  `flock.eval.hub` inside `load`. `tasks` keeps exporting it.
- `Truncation` and `scale` move to `results.py`, the file whose records they describe.
- `Provenance.collect` imports torch and the library versions it records inside itself.
- `WrittenResults.read(source, read_file)` parses a results file from any reader; `read_results`
  calls it with a folder's or a tracker run's reader, and the API with the file store's.

A test imports `flock.eval.diff` in a fresh interpreter and checks no torch, `transformers` or
`datasets` module was loaded.

### D176. Doc ids are shown as text

A predictions file's ids are the dataset's own ids or content hashes (eval's 041), not row
positions, the tasks read Hugging Face repos, and the Datasets tab pages rows by offset with no
lookup by id. So the id lists are plain, copyable text beside the task's dataset `repo@revision`,
and no doc text is fetched. A link to a row needs a lookup by id in the Datasets tab and a
mapping from a task to its hub copy (eval's 034); both are left for later.

## 3. Plan

One branch, `feat/app-eval-results`, stacked on eval's `feat/eval-result-files` until that
merges.

### Part 1. Eval

- The torch-free reader (D175) and `WrittenResults.read`.

### Part 2. API

- Migration `0022_eval_links.sql` (D171).
- `Models.link_eval` called from `Store.upsert_run`; `evals` and `eval_runs` on versions and trash
  entries; `scored` in `GET …/runs/{run}/models`; retention (D171).
- `GET …/runs/{run}/diff` (D174); the Dockerfile copies the eval modules it imports.

### Part 3. Web

- The Results tab (D173), the Evals tab and **Models scored** (D172), the compare page and its two
  entry points (D174). Parsing of `results.json` lives once, in `src/lib/evals.ts`.

### Tests

- `tests/eval`: the reader imports no torch; `WrittenResults.read` over a dict of files equals
  `read_results` over the same folder.
- `tests/app/test_models.py`: links from configs (number and digest match; a tag, another org,
  a wrong digest, a deleted version link nothing), `evals`, `eval_runs`, `scored`, retention.
- `tests/app/test_evals.py`: the diff route against `flock eval diff` on the same files; 404 for
  an unknown run, for an anonymous caller on a private project (either side), and for a run with
  no `results.json`; 409 for another instrument.
- `tests/app/test_web_access.py`: the compare page and a private run's Results tab answer an
  anonymous visitor as every private page does.
- Web specs for `src/lib/evals.ts` and the three components.

### Docs

- `docs/source/app/api/projects.md` (the diff route, `scored`), `docs/source/app/api/models.md`
  (`evals`, `eval_runs`), `docs/source/app/browse-models.md` (the Evals tab), and a section on the
  Results tab and comparison where the app's run pages are described; eval's `comparison.md`
  links to it.

## 4. How it was actually built

- **Eval (D175).** `results.py` keeps the write side's annotations under `TYPE_CHECKING`;
  `Provenance.collect`'s two torch annotations are strings, because the docs read its signature
  where torch isn't imported. `WrittenResults.read` holds the parse that `read_results` had;
  stage 041's later tidy-up (`_read_task`, `_check_types`, `predictions_name`) moved into it
  unchanged. `write_run`, the test helper that writes a run folder, moved to `tests/eval/toy.py`
  so the app's tests share it.
- **API.** The link table, `evals`, `eval_runs`, `scored` and retention's check are 014's code
  (`14bd505`, reverted from `b55a966`), with the match of D171. The diff route's file reads go
  through `Store.read_file`, scheduled on the event loop from the diff's thread with
  `run_coroutine_threadsafe`. The API image copies five eval modules; importing the API with
  only its own locked dependencies and those files loads no torch.
- **Web.** Ak's Evals tab, its server route and **Models scored** came back from `2d2be2c`;
  the tab reads results files instead of summaries. `src/lib/evals.ts` holds the parsing and
  row logic both the tab and the server use; `src/lib/server/evals.ts` reads a results file.
- **Tests.** As planned, plus: a database that kept the early `model_version_runs`
  (`test_migrations.py`), two models with the same bytes, and the built dashboard's answers to
  a member, an outsider and a signed-out visitor for the run page, the compare page and the
  Evals route (`test_web_access.py`).

## 5. Known gaps

- A run whose config was stored before this release links only when its config is sent again.
- Doc ids aren't linked to dataset rows (D176).
- Each comparison reads both runs' prediction files again; nothing is cached.
- Public visitors have no selection in the runs table, so they open a comparison by its link.

