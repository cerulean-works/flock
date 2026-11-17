# 038: `tw_text_v1`, the second version of the Taiwan text set as its own suite

| | |
| --- | --- |
| Status | in review |
| Date | 2026-11-17 |
| Builds on | [026](026-tw-text.md) (`tw_text`, `source_suite`, the census) and [029](029-tw-text-v0.md) (the frozen `v0` pin, the floors) |
| Keeps | every `tw_text` name, pin, definition and task hash, and every other task and suite |
| Result | suite `tw_text_v1` over 11 sources and 63 domain tasks of `cerulean-works/tw-text-v1` at `v1`; no model scored |

## 1. Goal

A second version of the Taiwan text set is frozen: `cerulean-works/tw-text-v1` at revision `b60d46e6`, tag `v1`, 11 configurations and 11,626 documents. Its builder (harvest stage 006) re-parses the pages fetched for `v0` with repaired extraction, takes each source's domains from its own metadata, samples domains equally and removes withdrawn judgments. This stage scores it under its own name, so that numbers banked on `tw_text` keep their meaning and a `v1` number names the instrument it came from.

**Out of scope:** any change to either dataset, to `source_suite`, `PerplexityTask` or a metric; scoring any model; building the exclusion index from `v1`.

---

## 2. Design decisions

### 038.D1. A new version is a new suite, and the old one stays

A number that needs different documents is a different task with a different name. `v1` changes the documents of every source and the domains of seven, so moving `tw_text`'s pin, as 029 did from an interim revision, would put two measurements under one name. `tw_text_v1` is added beside `tw_text`, with its own sources `tw_text_v1:<source>` and tasks `tw_text_v1_<source>__<domain>`. No name is shared, so a results file of one version and a bank of the other list each other's tasks as `only_new` and `only_banked` in `compare`, never as `changed`. Freezing `v0` was what made its numbers worth keeping; `tw_text` remains the suite for every comparison already banked on it.

### 038.D2. The domain table is the split's, `other` included

As 026.D6 and 029.D1 require, the table lists every distinct `subdomain` of each source's `test` rows at the pin, most documents first and by name on ties. `v1` keeps 029.D2's floors (a domain under 100,000 OLMo 2 tokens merges into `other`, and an `other` still under the floor leaves the sample), but because `v1` samples domains equally, six `other` domains clear the floor, in `judicial`, `cna`, `pts`, `ltn`, `cnyes` and `theses` (where it is a single thesis of 198,261 tokens). Each is scored as a domain like any other. The Reporter's podcast domain did not clear it and left the sample, so `twreporter` keeps one domain.

### 038.D3. One builder for both revisions

`tw_text_suite(name, repo, revision, sources)` builds either version from its table, so a version is a table and one line, not a copy of the suite's construction. Each source's dataset is its configuration of the pinned repository, split `test`, with `PerplexityTask`'s window of 2048 tokens (026.D4). The suite is scored as `tw_text` is: a source is the unweighted mean of its domains' `bits_per_byte` (Paloma, sec. 4) and the suite the unweighted mean of its 11 sources (026.D3). The sources keep `v0`'s order, so a source's rows line up in two tables.

### 038.D4. The time caveats carry over unchanged

`v1` re-parses `v0`'s fetched pages and adds none, except theses, which were listed again from version 3 of the `ndltd-pdfs` dataset instead of version 2 (harvest 006.D13: 320 in the window against 305). So 029.D4 holds for it as written: `judicial` covers 2026-04-01 to 2026-08-31, because the September and October bundles did not exist at either build, and `theses` is not held out in time, since a thesis carries only its academic year. A later judgment month is a new revision of the dataset, and so a new suite under 038.D1.

### 038.D5. What training-data exclusion takes from `v1`, stated but not built

The user docs list the references an exclusion index takes from the set (harvest 006.D11): every row of both versions as a whole document, with its configuration, `id`, `url`, `subdomain`, `source`, the SHA-256 of its UTF-8 `text` and its file's SHA-256; the identity of every document fetched in the window, sampled or not; and tombstones for withdrawn judgments. Only the first is in the dataset files. flock scores the set and builds no index, so the docs state that no training data has been checked against these references yet.

---

## 3. Plan

1. Verify the pin: the tag resolves to `b60d46e6`, every file's SHA-256 matches the build's receipt, and the card's 11 configurations hold 11,626 rows.
2. The table from the split, the builder, `TW_TEXT_V1` and its registration, tests and snapshot in one change; `tw_text` doesn't move.
3. Docs: the `tw_text` page covers both versions, plus `tasks.md` and `add-task.md`.

---

## 4. How it was actually built

- **The pin.** The `v1` tag resolves to `b60d46e622d3c9ce7f022831004aab0bef91b32d` and the repository is private. All 11 parquet files, the card and the sample receipt match the SHA-256 values the build recorded. Each configuration's rows match the card's count, every `id` is unique within its configuration, no `text` is empty, and every row has a `url`.
- **The table.** Copied from each configuration's `subdomain` counts at the pin, with documents read by `pyarrow`, and checked equal in set and order. `source_census.py tw_text_v1` was not run: it needs more than the 4 GiB a login node allows, and no compute node was free. Every one of the 63 tasks loaded its domain's rows from the pin, 11,626 documents in all, so no listed domain is missing from the split, and the counts per domain match the split's. The census remains the check for a later revision.
- **Domains.** 63 tasks: `judicial` 9, `ly` 3, `gov_press` 4, `cna` 9, `pts` 8, `ltn` 7, `cnyes` 5, `twreporter` 1, `ptt` 8, `zhwiki` 1, `theses` 8. Every domain holds at least 100,000 OLMo 2 tokens, the smallest being `tw_text_v1_pts__產經` at 106,196, by the build's own count.
- **Locks.** The snapshot diff adds 63 tasks and 12 suites and changes no existing entry, so every `tw_text` definition, and with it every `tw_text` task hash, is unchanged. Moving `tw_text` onto the builder changed no definition.
- **Tests.** The suite is registered with the same 11 sources in `v0`'s order, every task pins `v1`'s revision and `test` split, and no name is shared with `tw_text`. On synthetic metrics, a source with several domains counts each domain once and the suite counts each source once. No test downloads the dataset.
- **Not done.** No model is scored at `v1`. The exclusion index takes nothing from `v1` yet (038.D5). zh-Wikipedia's category domains stay out: `zhwiki` is one domain at `v1`.
