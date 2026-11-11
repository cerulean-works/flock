# 034: private hub copies of every pinned dataset, staged for jobs with no network

| | |
| --- | --- |
| Status | in review |
| Date | 2026-11-08 |
| Builds on | [020](020-hub-datasets.md) (the hub datasets design, amended here); [001](001-eval-core.md) (`Dataset` pinned to a revision); the hub's `013-datasets-tab.md` (D85 to D96, tags) and D115 (public by default, private per exact path, `docs/source/app/share-publicly.md`) |
| Keeps | every task hash and suite hash on `main` (523 tasks), `results.json` schema 8, every request |
| Result | `Dataset.load` reads a staged copy when `FLOCK_EVAL_HUB` is set; `flock eval hub pull`; `flock/eval/scripts/hub_mirror.py` |

## 1. Goal

Every task reads a Hugging Face dataset repo at a commit: 31 repos, six of them Cerulean's own. Compute nodes with no network can only read what is already on disk, and some of the data (the mock exams, the crawled Taiwan text, the held-out FineWeb-2 slice, Paloma under its licence) must stay where Cerulean controls who reads it. This stage puts a private copy of each pinned repo on the Flock hub and lets a job read a copy staged on shared disk, with the same rows, requests and task hashes as the Hugging Face path.

**Out of scope:** making any copy public; datasets that exist only on the hub (020.D1's `flock:` prefix stays unbuilt until one exists); training data; rescoring any result.

---

## 2. Design decisions

### 034.D1. A hub copy is keyed by its Hugging Face pin, so no task hash moves

This amends 020.D1 and 020.D5, which repinned a moved task to `flock:<org>/<path>@<n>`. Both task hashes serialize the whole `Dataset` (`tasks/base.py`, `tasks/perplexity.py`), so a repin moves the hash of every moved task, and every banked result would need rescoring before `compare` accepts it against a new run. Keyed by the pin, a task names the same `Dataset(repo, revision)` wherever its rows come from: the 523 hashes in the snapshot don't move, and the task hash still covers every request, so a copy with other rows gives another hash and `compare` refuses it. The `flock:` prefix stays for a dataset that exists only on the hub; none does yet, so it isn't built.

### 034.D2. The copy lives at `eval/<owner>/<name>`, as the version tagged with the pin's commit

The hub path is the repo id under `eval/` in the org's `flock-datasets` bucket (`eval/allenai/ai2_arc`), so a pin maps to its path with no lookup table and two owners can't collide. One version holds one pin and is tagged with the pin's revision string. 020.D3 refused tags because they move (the hub's D96); here the tag only finds the version, and the version's transaction properties record `source_repo` and `source_revision`, which `pull` and the reader check. A moved tag therefore fails loudly instead of giving other rows. Tagged versions survive cleanup (020.D3). One pin is a tag object, not a commit (`ikala/tmmluplus@94d86f1d`, v1.1, which resolves to `466ccd97`); the copy is keyed by the string the task names.

### 034.D3. A copy holds the configurations and splits some task reads, with each one's features

This narrows 020.D2's "every configuration and split". Whole repos copy data no task reads, such as `cais/mmlu`'s `auxiliary_train` repeated under each subject. The mirror collects each pin's (subset, split) pairs from the registry, scored and few-shot, and records each pair's row count and `datasets` features. Rows of all pairs share one Lance table with two string columns, `hf_subset` (null without a configuration) and `hf_split`, not 020.D2's `subset` and `split`: HellaSwag has a `split` column of its own. A source column with either name is refused. Pairs with different columns share the table's union of columns, and a column whose type differs between pairs is stored once per type (`timestamp`, `timestamp__1`: Paloma's `timestamp` is a time in some sources and text in others), so no value is ever cast to another type to fit. Each pair records which stored columns are its own and under which names; the reader selects those, renames them and casts them to the pair's recorded features, so `datasets` gives the same Python rows as from the Hugging Face Hub, ClassLabels and nested sequences included. A task that later reads another split needs a new version of the same path.

### 034.D4. Every copy is private, flagged before its first write

The hub serves a dataset's rows to anyone with its URL unless an admin has flagged that exact path private (D115). The mirror flags each path private before writing it. A path that already holds a copy must then answer an anonymous `info` request with 404, and a member one with `public` false, before more rows go to it; a new path can only be checked after its first write, and the mirror stops unless an anonymous request then answers 404 and a member one 200 with `public` false (a missing path answers 404 to both, so the anonymous status alone proves nothing), moving a new path that fails to the trash, where only admins can restore it. A path is new only if a member's `info` answers 404; any other answer than 200 or 404 stops the mirror before it writes, since it leaves unknown whether the path is public or holds other pins' tagged versions, and a path that held versions before is never trashed, since the trash would take every pin's versions with it. This holds for the sets 020.D5 kept public on Hugging Face (DRCD, basic-skills, the FineWeb-Edu dev set) too: making a hub copy public is a separate decision. The Hugging Face repos stay the public source.

### 034.D5. Jobs read a copy staged under `$FLOCK_EVAL_HUB`, and a missing copy fails

This builds 020.D6 with two changes. The staging layout is `<root>/<owner>/<name>/<commit>`, one Lance dataset per pin, so staging a second pin of a repo never touches the first. And setting `FLOCK_EVAL_HUB` makes the staged copy required: a run checks every pin its tasks read before the model loads, and a missing pin or split raises and names the `pull` command, rather than falling back to the network, which on a node with no network would fail later and on a node with a warm Hugging Face cache would read the other source silently. A revision that isn't a 40-digit commit hash is refused on this path, by `pull` as by the reader, and so is a repo id that isn't `<owner>/<name>`, so a staged folder never leaves its root. `pull` signs in to the org it names with read-only keys, so the org it records is the one it read, and rewrites the tagged version into the staging folder, through a temporary folder renamed at the end, and records the org, hub URI and version beside the provenance; it doesn't copy Lance's files, so it depends on no file layout. It refuses a version that records no pairs. A staged copy that records the same source and pairs is kept, whichever org or version it came from, since those decide its rows; one that records other pairs, holds another number of rows than it records or can't be read is replaced, so pulling again repairs a copy that is short, stale or broken in a way its row count or Lance shows. Pulls of one repo into one root take turns under an `flock()` lock on the repo's folder, as `flock.hub.models.cache` locks a model version, so two jobs staging the same pin write it once. A copy that is there but can't be read (broken, or another user's) is reported as that, with its owner, not as unstaged. The staging root is where rows come from, not what they are, so it stays out of the task hash (020.D6). The staging root's mode decides who reads every copy in it, private sets included, whatever the umask: the folders between the root and a copy take the root's mode, setgid included, so a group's root keeps its group, and a copy's folders and files take it without group and other write bits (files without execute bits), so nobody the root lets read a copy can change it. A root others can write to must have the sticky bit, which the folders below it keep, so nobody but a copy's owner can move or replace it; `pull` refuses one without it, and refuses a folder below the root that others can write to without it, such as one made by hand. A new folder is made owner-only before it takes the root's mode, so it is never briefly open to others' writes. A missing root is made with mode 0o700; on NCHC the default root sits under a user's own `/work` folder.

---

## 3. Plan

1. `flock/eval/hub.py`: `write` (pairs to one Lance version with provenance), `pull` (tagged version to the staging folder), `staged_rows` (one pair back as a `datasets.Dataset`).
2. `Dataset.load` reads `staged_rows` when `FLOCK_EVAL_HUB` is set.
3. `flock eval hub pull ORG/OWNER/NAME@COMMIT [--to]`, over `flock.hub.s3.get_process` with read-only keys.
4. `flock/eval/scripts/hub_mirror.py`: every registry pin to the hub, private first, tagged, with the anonymous and member status of each path.
5. Tests on CPU with a local Lance folder standing in for the hub.

## 4. How it was actually built

The plan held, with two corrections forced by real data in a dry run before anything was written to the hub. Every pin the registry reads (31 repos, 523 tasks) was written with `hub.write` to a local folder standing in for the hub, tagged, staged with `hub.pull`, and read back.

- **HellaSwag has a `split` column**, so 020.D2's `subset` and `split` would have clashed. The bookkeeping columns became `hf_subset` and `hf_split` (034.D3).
- **Paloma's `timestamp` is `timestamp[s]` in some sources and a string in others**, so one table schema couldn't hold both. Widening the type would have changed values; the column is now stored once per type (034.D3).

Then every task's hash and every split's rows were computed twice, from the Hugging Face Hub and from the staged copies with `HF_HUB_OFFLINE=1`. All 523 task hashes and all 371 (repo, subset, split) row digests are equal, over 605,463 rows. The copies take 306 MB. `cais/mmlu` (114 pairs) and `ikala/tmmluplus` (132) take about two minutes each to write from the Hugging Face cache; the rest take seconds.

Review changed a good deal of this, and the changes are grouped here by what they touch.

**The reader.** It takes a pair's rows by offset, since `write` puts pairs one after another, instead of filtering on a quoted name. One check of a copy's provenance serves `pull`, the reader and the mirror, and a recorded pair is a small typed record parsed once per process. A copy that holds another number of rows than it records is refused, with the `pull` command that replaces it. A copy that is there but unreadable is told from a missing one and names its owner. `FLOCK_EVAL_HUB` is read in one place, made absolute, and keys the perplexity row cache, so a process that changes it reads again.

**`pull`.** It replaces a copy staged from an earlier version of the tag, so a split added later reaches jobs, and one that is broken or holds the wrong number of rows. It keeps a copy by its source and pairs rather than by hub version or org. The new copy is written to a temporary folder, renamed in after the stale one is moved aside, and the stale one is deleted only then, or moved back if the rename fails; a failed pull removes its temporary folder. Pulls of one repo take turns under a lock, which replaced an earlier check-again-after-writing that left a race. `pull` takes the repo and builds the hub path itself, checks the repo id and the commit hash itself rather than in the CLI, signs in to the org it records, and refuses a version without pairs or with pairs it can't read.

**Who can read and replace a copy.** Modes come from the staging root, not the umask, and a missing root is made private. A copy's contents lose their write bits before its top folder opens to the root's readers. A root others can write to needs the sticky bit, as does every folder below it, and `pull` checks that even when the copy is already staged; a link below the root is refused, since it would take the copy out of it.

**Before a run.** Each task type reports the pairs its `load` reads (`reads`), with a test that every registered task reads exactly those. The mirror and the check before a run collect them, so a run checks every staged pin before its model loads and lists one pull command per missing pin, with the root quoted.

**The mirror.** It keeps a tagged version only if it holds every pair the registry reads, and otherwise writes a new version and moves the tag. It writes only to a path members read (200, and checked private first) or find missing (404), requires a member 200 beside the anonymous 404 afterwards, checks a path straight after writing and tags it only then, trashes a new path that fails and says so when the trash request fails, never trashes a path that existed, and stops on a Lance error other than a missing path. It uses the CLI's API client, and is tested against a stand-in for the Flock API.

## 5. Known gaps

- The mirror and `pull` are separate from a hub-wide dataset pull; if the hub gains one, `pull` should use it.
- Copies are written by an org admin's sign-in, since only admins flag paths private.
- A new path's privacy can only be checked after its first write, so its rows are on the hub, flagged private, a moment before the check: the hub reads a path's flag only through `info`, which answers 404 while nothing is written there.
- The mirror is tested against a stand-in for the Flock API and a local folder for the bucket, never against a live hub. Its record per dataset is the evidence there.
- `results.json` doesn't record that rows came from a staged copy, or which hub version; the task hash still changes if the rows do.
- Replacing a stale staged copy removes the old folder, so a job reading that pin during a re-pull can fail; re-pull between jobs. In a shared root with the sticky bit a copy can be replaced by its owner and by the owners of the folders above it, up to the root; anyone else's pull refuses with the owner's name. A stale copy whose files the puller can't delete is left beside the new one as `.<commit>.*.stale`, as is one whose pull was killed between moving it aside and renaming the new copy in, which leaves the pin unstaged until the next pull.
- A pull killed by a signal it can't handle leaves a `.<commit>.*.partial` folder beside the staged copies; nothing reads it, and the next pull of the pin doesn't remove it. A pull that fails otherwise removes its own.
- The pull lock is `flock()` on a folder. A filesystem without `flock` support refuses every pull, as it would the model cache, and whether NCHC's WekaFS shares `flock()` locks between nodes is unchecked: if it doesn't, two nodes pulling one pin at once can still race. Pull from one node.
- Whoever can write to the staging root can stage a pin first, and every later pull keeps that copy, since a copy is kept by what it records, not by who made it. A shared root trusts everyone who can write to it.
- A staged copy is checked by what it records and by its row count, not by its bytes: a copy with a data file changed or removed is found only when its rows are read.
- A second user's pull can meet a folder another pull has just made and not yet opened to its group, and fail with a permission error; pulling again works.
- Each read of a staged split opens the copy again. Measured on the 132 pairs of `ikala/tmmluplus` staged on shared disk, an open and its check take under a millisecond once the folder is cached and all 132 reads take 0.7 s together, so the copy is not cached.
- Growing a pin's pairs reads every pair again from the Hugging Face Hub, not the held ones from the hub copy.
