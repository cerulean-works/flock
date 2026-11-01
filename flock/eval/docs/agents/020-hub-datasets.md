# 020: eval datasets from the Flock hub

| | |
| --- | --- |
| Status | design |
| Date | 2026-11-01 |
| Builds on | [001](001-eval-core.md) (`Dataset` pinned to a revision), [009](009-tw-mock-exams.md) (the private exam set), [014](014-run-provenance.md) (D6: `dataset` in each task result); the hub's `013-datasets-tab.md` (D85 to D96) and `014-model-registry.md` (`flock:` ids) in `app/api/docs/agents/` |
| Keeps | every task hash on `main`, `results.json` schema 6 |
| Result | none yet: design for review |

## 1. Goal

Every task reads a Hugging Face dataset repo at a commit. The private sets (the mock exam set, and the FineWeb-Edu dev set the cycle 3 lab scores) live in private `cerulean-works` repos, while the training data lives as Lance datasets in each org's `flock-datasets` bucket. This stage lets a task read a Lance dataset on the hub with the same guarantee a commit gives today: the rows a definition names can't change under it, and two runs that read different rows get different task hashes. It also says which datasets move and how.

**Out of scope:** moving any dataset before this design is approved; datasets that aren't Lance; writing datasets from `flock.eval`; the training loader.

---

## 2. Design decisions

### 020.D1. A hub dataset is a `Dataset` whose repo starts with `flock:`

`Dataset(repo="flock:cerulean-labs/eval/tw-mock-exams", revision="3", subset="chinese", split="test")` reads `s3://flock-datasets/eval/tw-mock-exams` of the org `cerulean-labs` at Lance version 3. This is how `ModelRef` already names a registry model (`flock:org/name@version`, `flock/eval/model.py:25,55-72`), and `flock eval run --model` takes it, so a reader meets one convention for both. `load` branches on the prefix: the hub path signs in with `flock.hub.s3.get_process(org, read_only=True)` (`flock/hub/s3/client.py:315`), as the trainer's `get_dataset` does (`flock/training/core/data_utils.py:59-61`), and reads with `lance.dataset(uri, version=int(revision))`. `flock.hub` is imported only on that path, as `model.py` does for registry models (`flock/eval/model.py:107-113`), so `flock.eval` gains no dependency.

**No new field.** Both task hashes serialize the whole `Dataset` (`dataclasses.asdict`, `flock/eval/tasks/base.py:548-556`, `flock/eval/tasks/perplexity.py:93`), so a new field, even with a default, would move all of them, Paloma's 309 included. A prefix in `repo` moves none. The `dataset` object in `results.json` keeps its five fields, so schema 6 stands. Rejected: a `HubDataset` class beside `Dataset` (every task type, `Fewshot` and the snapshot would take a union, for one branch in `load`) and a `source` field (moves every hash). This is the second loading path 006 avoided; it is accepted here because the data can't be mirrored to an HF repo without leaving the hub, which is the point of the move.

### 020.D2. One Lance dataset per HF repo, with `subset` and `split` as columns

HF has configurations and splits under one commit; Lance has one table per dataset, versioned as a whole. A hub dataset therefore holds every configuration and split as rows, with two string columns `subset` (null when there is none) and `split`, and `load(split)` reads `filter="subset = ... AND split = ..."` in scan order. One version number then pins every split, as a commit does, and `Fewshot` reading `dev` beside a scored `test` needs nothing new. The writer keeps each split's rows in HF order, so `row_index` and every id-free task count the same positions. `load` refuses rows that already carry their own `subset` or `split` column. Rejected: one Lance dataset per split (`Fewshot` would need a second revision) and one per configuration (ten versions to pin for the exam set).

### 020.D3. `revision` is a version number, never a tag

A tag moves (the hub's D96, `app/api/docs/agents/013-datasets-tab.md:355-356`, and `PUT tags/{tag}` at :296), as a branch does on HF, so `revision` holds the decimal version number and `load` refuses anything else. Two hub behaviours make a number weaker than a commit hash: a dataset deleted and written again at the same path reuses numbers (the hub's own cache keys on the manifest's ETag for this reason, :493-496), and `cleanup_old_versions` removes untagged versions (D89 at :165-166; tagged ones are kept, D96 at :346-347). The first is caught where it matters: a task hash covers every request (multiple choice, `flock/eval/tasks/base.py:558-560`) or every doc's id and text (perplexity, `flock/eval/tasks/perplexity.py:94-95`), so other rows give another hash and `compare` refuses. The second fails loudly at `load`. Two asks keep both from happening (section 4). Recording the manifest's digest in `results.json` would catch the first without a rerun, at the cost of schema 7; held until a reuse is seen.

### 020.D4. Who can read a private set

A `flock-datasets` bucket is one per org (the hub's D85, `app/api/docs/agents/013-datasets-tab.md:53-58`) and readable by every member of that org and nobody else (D54, `app/api/docs/agents/009-s3-proxy.md:108-118`), which is what a private `cerulean-works` repo gives today. A job needs `FLOCK_TOKEN` (or `flock login`) where it now needs `HF_TOKEN` (`flock/hub/s3/client.py:11,58`). Read-only keys are asked for, and the key is renewed before it expires (`flock/hub/s3/client.py:155-163,227-228`), so a long run doesn't lose access. The org is part of the id, never a default, so a task never reads another org's bucket by accident.

### 020.D5. What moves, and how

| Dataset | Now | Plan |
| --- | --- | --- |
| `cerulean-works/drcd` | public mirror, CC-BY-SA 3.0 | stays on HF |
| `cerulean-works/basic-skills` | public mirror, ODC-BY | stays on HF |
| `cerulean-works/tw-mock-exams` | private, `e62f9adf` | moves once the loader lands |
| `cerulean-works/fineweb-edu-dev` | private, built for the lab | stays on HF until the lab ends, then moves |

Public data stays on HF: the first public version of the suite must run for anyone without a Flock account, and a public mirror costs nothing to keep. A move changes `repo` and `revision`, so the moved tasks' hashes and the hashes of the suites holding them (`tw_mock_exams`, `zh_tw`, `zh_tw:bpb`) move once; no move is hash-free. Each move follows the rule the exam set followed when it changed owner: write the Lance dataset from the pinned HF commit, repin, regenerate the snapshot, and prove with a dump that every request (or doc) is byte-equal to the HF-backed one. The HF repo then stays, read-only, until the results that cite it are rescored. The dev set waits because every pair in the lab is ranked under one task hash; a move during the lab would split the ranking for no gain.

---

## 3. Plan (after approval)

1. `tasks/base.py`: the `flock:` branch in `Dataset.load`, the version check, the column filter and the refusal of D2. About 30 lines.
2. A script that writes an HF commit to the hub as D2 says, with `transaction_properties` recording the source repo and commit, and tags the version (section 4).
3. Tests on CPU with a local Lance dataset (`lance.write_dataset` to a temporary folder, the `flock:` branch pointed at it): rows equal to the HF path's rows for a tiny dataset with two configurations and splits, a tag refused as revision, a missing version, a clashing column, and every task hash on `main` unchanged in the snapshot.
4. The exam set's move as D5, in its own PR, with the dump comparison in its note.

## 4. Open questions for the hub's owner

1. **The org and the path.** Which org holds eval datasets (`cerulean-labs`, as the trainer's loader uses)? Is `eval/<name>` the folder?
2. **Pinned versions survive.** Can every version a task pins carry a tag (`eval-<version>`) so `cleanup_old_versions` keeps it, and can datasets under `eval/` be changed only by new versions, never by deleting and rewriting the path (D3)?
3. **The dev set's ids out of training.** Does the FineWeb-Edu dataset in `flock-datasets` keep FineWeb-Edu's `id` column? We propose dropping the 5,000 ids with one Lance delete (`delete("id IN (...)")`), which makes a version whose history shows the delete and its row count, and pinning that version in the lab's training config. We would then read that version's `id` column alone and bank the count of dev ids found in it (expected zero). Would you rather filter in the loader? Either way, exclusion by id leaves near-duplicates from other CommonCrawl dumps, since FineWeb-Edu deduplicates within each dump (Penedo et al. 2024).
4. **Reach.** Can NCHC compute nodes reach the S3 proxy (the trainer's loader, `flock/training/core/data_utils.py:59-61`, suggests yes), and is a 5,000-row read per run acceptable load, given Lance has no local cache like `HF_HOME`?
