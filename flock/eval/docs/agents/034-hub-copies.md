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

The hub serves a dataset's rows to anyone with its URL unless an admin has flagged that exact path private (D115). The mirror flags each path private before writing it and stops if an anonymous `info` request then answers anything but 404. This holds for the sets 020.D5 kept public on Hugging Face (DRCD, basic-skills, the FineWeb-Edu dev set) too: making a hub copy public is a separate decision. The Hugging Face repos stay the public source.

### 034.D5. Jobs read a copy staged under `$FLOCK_EVAL_HUB`, and a missing copy fails

This builds 020.D6 with two changes. The staging layout is `<root>/<owner>/<name>/<commit>`, one Lance dataset per pin, so staging a second pin of a repo never touches the first. And setting `FLOCK_EVAL_HUB` makes the staged copy required: a missing pin raises and names the `pull` command, rather than falling back to the network, which on a node with no network would fail later and on a node with a warm Hugging Face cache would read the other source silently. A revision that isn't a 40-digit commit hash is refused on this path. `pull` rewrites the tagged version into the staging folder, through a temporary folder renamed at the end, and records the hub URI and version beside the provenance; it doesn't copy Lance's files, so it depends on no file layout. The staging root is where rows come from, not what they are, so it stays out of the task hash (020.D6). A staged copy takes the mode of its parent folder, so the staging root's permissions decide who reads it, private sets included; on NCHC the default root sits under a user's own `/work` folder.

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

Review changed three things. The reader takes a pair's rows by offset, since `write` puts pairs one after another, instead of filtering on a quoted name. `pull` replaces a copy staged from an earlier version of the tag, so a split added later reaches jobs, and refuses a copy that records another repo than its path. The mirror keeps a tagged version only if it holds every pair the registry reads, and otherwise writes a new version and moves the tag.

## 5. Known gaps

- The mirror and `pull` are separate from a hub-wide dataset pull; if the hub gains one, `pull` should use it.
- Copies are written by an org admin's sign-in, since only admins flag paths private.
- `results.json` doesn't record that rows came from a staged copy, or which hub version; the task hash still changes if the rows do.
- With `FLOCK_EVAL_HUB` set, a missing copy fails when its task loads, after earlier tasks have been scored, not before the run starts.
- Replacing a stale staged copy removes the old folder before the new one is renamed in, so a job reading that pin during a re-pull can fail; re-pull between jobs.
- A pull killed mid-write leaves a `.<commit>.*.partial` folder beside the staged copies; nothing reads it, and nothing removes it.
- A staged copy records the hub URI and version but not the org, whose URIs are alike, so a copy pulled from another org at the same version number is kept as current.
- Two pulls replacing one stale pin at once can fail with `FileNotFoundError`; the copy is left whole, and a third pull succeeds.
- Paloma-style sources cache their rows per `Dataset` in a process (`_docs_by_domain`), so changing `FLOCK_EVAL_HUB` mid-process doesn't reread them.
- Growing a pin's pairs reads every pair again from the Hugging Face Hub, not the held ones from the hub copy.
