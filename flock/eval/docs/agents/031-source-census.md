# 031: a census for every `source_suite` benchmark, and a check that no domain goes unlisted

| | |
| --- | --- |
| Status | in review |
| Date | 2026-11-09 |
| Builds on | [008](008-paloma.md) (the Paloma census), [026](026-tw-text.md) (`source_suite`, the repin rule of 026.D6), [029](029-tw-text-v0.md) (029.D1: the census keeps the table whole) |
| Keeps | every task, suite and hash: Paloma's 309, `tw_text`'s 24 and `fineweb_edu`'s; every scoring path |
| Result | `flock/eval/scripts/source_census.py` counts Paloma or `tw_text` and exits with status 1 when a listed source's domains differ from the split's |

## 1. Goal

A `source_suite` benchmark lists each source's domains by hand: `DOMAINS` in `tasks/paloma.py` and `SOURCES` in `tasks/tw_text.py`. 029.D1 records that the check runs one way only. A listed domain with no rows raises when its task loads, but a row whose `subdomain` the table leaves out is never scored and nothing says so. A missed domain on a repin would move its source's mean silently.

008's census read Paloma only, from `.jsonl.gz` files. `tw_text`'s table was copied from a count made outside the repository, which a teammate can't rerun. This stage makes the census one script for both benchmarks, and makes it fail when the table and the split differ.

**Out of scope:** any change to a dataset, a table, a revision or a task; a check at run time.

---

## 2. Design decisions

### 031.D1. The census checks the table, not the scoring path

The check could raise when a task loads, if any row of its source carries a domain no task lists. That is stronger, since no run could skip it, but every task would need its source's other domains to decide, and the check would sit on the path that makes the numbers. The census runs where the table changes: on a new source or a repin, which 026.D6 already ties to a census. So it exits with status 1 there, and no scoring path or hash moves. There is no outside precedent; this guards our own hand-copied tables.

### 031.D2. The table checked is the suite's tasks

The census takes a registered suite's name, `paloma` or `tw_text`, and compares each source's task domains with the split's `subdomain` values. It reads the tasks rather than the dict that builds them, so it checks what is scored. A source with one domain and no `subdomain` in its rows is listed with none, as `source_suite` makes it, and the census counts its rows under the domain `""`. A row without a `subdomain` in a source with domains counts under `""` too, which no task of that source scores, so the check reports it as not listed, in words rather than as a domain a table could take. 008's census raised there instead, which would also have stopped the census on a source the suite leaves out. Every task must score the same revision, split and window, which both benchmarks do, so the counts are what the tasks score.

### 031.D3. A source's files are the ones `datasets` resolves; its rows are read raw

Paloma keeps a source's split as `<source>/test/*.jsonl.gz`, and tw-text as `data/<source>/test.parquet`. Both declare each source as a configuration of the dataset whose splits are file patterns. The census asks `datasets` for the configurations and for each one's files in the scored split (`load_dataset_builder`, which reads no rows), so it checks the files `Dataset.load` reads, with `datasets`' own rules for patterns and hidden files, and one rule covers both layouts. It reads the rows of those files itself, as 008's census did, so the counts don't depend on how a `datasets` version parses them. A configuration with no files in the split is still a source the suite may leave out, and is reported as one.

### 031.D4. A source the suite leaves out is reported, not failed

Paloma has 16 sources and `paloma` scores 9 (008). `tw_text` left out `bbc_zh_trad`, which is converted from Simplified script (026.D5). Leaving a source out is a choice made in the open, so the census lists such sources in its output and its printout and passes. Only a listed source whose domains differ fails.

### 031.D5. Domains print in each table's order

The census prints each source's domains in the table's format, ready to paste. Domains with Paloma's rank prefix come in rank order, as 008 lists them, and others with the most documents first, as `tw_text` lists them (026.D6), then by name. Paloma's `dolma-v1_5`, whose domains have no rank, is listed by name in `DOMAINS`, so its printed line orders the same five domains differently. The check compares sets, so the order doesn't affect it. Pasting that line would reorder the suite's members and move its hash; a repin moves every hash anyway, but outside one `DOMAINS` keeps its order.

---

## 3. Plan

1. Rename `scripts/paloma_census.py` to `scripts/source_census.py`: suite name in, configurations and files from `datasets`, `.parquet` beside `.jsonl.gz`, bytes per domain, the check. Files are read as UTF-8 whatever the locale.
2. Tests on toy files in both formats, resolved by `datasets` from a local card, with a toy suite that passes and suites that must fail.
3. Run it on both benchmarks at their pinned revisions and compare with the earlier counts.
4. `add-task.md` gives the command; the two tables' comments name it.

---

## 4. How it was actually built

- **`tw_text` at `v0`.** The census exits with status 0 and prints `SOURCES` line for line. Its counts equal the count made when the dataset was sampled, in every domain's documents, UTF-8 bytes and OLMo 2 tokens, and in the order of each source's domains: 11 sources, 24 domains, 11,643 documents, 20,762,474 tokens. No source is left out at `v0`.
- **Paloma.** The census exits with status 0 and lists the 7 sources `paloma` leaves out. Every printed line equals `DOMAINS` except `dolma-v1_5`'s order (031.D5), and all 160 counts it shares with 008's design census are equal across the 16 sources: documents, bytes, tokens, windows, texts over 2048 tokens, empty texts, repeated ids, and documents and tokens per domain.
- **It fails when it should.** With `高等` dropped from `tw_text`'s `judicial`, the census at `v0` exits with status 1 and names `高等` as not listed.
- **Unchanged.** Under `tasks/` only two comments change. The 334 hashes of Paloma's 309 tasks, `tw_text`'s 24 and `fineweb_edu` are equal at `main` and on this branch, and the snapshot regenerates with no diff.
- **Tests.** `datasets` resolves a local toy dataset with four configurations: a two-domain source in parquet, a one-domain source in two `.jsonl.gz` files with a `val` split and a hidden file the census must skip, a source whose rows all carry its name over a list of two files, and a source the suite leaves out. The census fails on a domain left out, a domain the split lacks, a one-value source listed with none, and a listed source the card doesn't have. The counts of one source are equal from either format, and bytes count UTF-8.
