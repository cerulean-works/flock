# 029: `tw_text` at the frozen `v0` of its dataset

| | |
| --- | --- |
| Status | in review |
| Date | 2026-11-06 |
| Builds on | [026](026-tw-text.md) (`tw_text`, its domain table, the repin rule of 026.D6) |
| Keeps | Paloma's 309 tasks, suites and hashes, `fineweb_edu`'s hash, every request and every multiple-choice result |
| Result | suite `tw_text` over 11 sources and 24 domain tasks of `cerulean-works/tw-text-v0` at `v0`; OLMo 2 1B in FP32 scores 1.6874 bits per byte |

## 1. Goal

026 pinned `tw_text` to `sample-2`, an interim revision, and said the dataset's final one would need a repin. The dataset is now frozen: `cerulean-works/tw-text-v0` at revision `1786e787`, tag `v0`, 11 configurations and 11,643 documents. This stage moves the suite to that revision, so that every model is scored on one set that no longer changes.

**Out of scope:** any change to the dataset; any change to `source_suite`, `PerplexityTask` or a metric; scoring any model beyond a smoke run.

---

## 2. Design decisions

### 029.D1. The revision and the domain table move in one commit

026.D6 requires this: a task whose domain has no rows raises, so a table and a revision that disagree fail. The table is copied from a census of the pinned revision (`subdomain` values of each source's `test` rows, most documents first). A second count of the same revision, made when the dataset was sampled, gives the same 24 domains, document counts and token counts.

### 029.D2. A domain under the floor merges into `other`, and an `other` still under it leaves the sample

Paloma's per-domain size is 100,000 tokens (sec. 3), and 026.D2 kept that floor by merging smaller domains into `other`. At `sample-2`, `cnyes` and `zhwiki` were each a single `other` that stayed under it (85,021 and 77,150 tokens). Paloma gives no rule for a remainder that stays too small, so this rule is ours: the sample counts the floor with its own tokens, and a merged `other` still under the floor leaves the sample. So `v0` has no domain named `other`, and every task holds at least 100,000 OLMo 2 tokens, the smallest being `tw_text_ptt__WomenTalk` at 172,778.

The sample lost 40 judgments (90,438 tokens: 23 administrative, 8 supreme court and 9 already in `other`), 1 interpellation record (2,355 tokens) and 1 NCKU thesis (54,318 tokens). The dropped documents stay in the crawled data. Each source's mean therefore no longer holds these registers: the administrative and supreme courts and the interpellations are not in `tw_text`.

### 029.D3. `ptt` joins as the eleventh source, so `v0` doesn't compare with `sample-2`

`ptt` is forum text, a register no other source has, and it has eight boards as domains. `tw_text` is the unweighted mean of its sources (026.D3), so the mean's members changed, along with the documents of every source. A number at `v0` and a number at `sample-2` are two different measurements and never appear in one comparison. `compare` lists such tasks as changed and doesn't compare them, since their task hashes differ.

### 029.D4. What every table of these numbers carries

Two sources differ from the rest in time. `judicial` covers 2026-04-01 to 2026-08-31, where the monthly bundles of judgments end. `theses` is not held out in time, since a thesis carries only its academic year (115, dated from 2026-08-01) and its text may predate the window. `zhwiki` needs a separate caution: the dataset's check for Simplified-only characters and mainland wording kept 40.3 percent of the pages crawled in the window (2,317 of 5,755). That share describes the crawl, not the sample, which holds kept pages only. Why it is this low was not measured.

---

## 3. Plan

1. Census of the frozen revision, compared with the count made at sampling.
2. Revision, table, tests and snapshot in one commit; nothing else in `tasks/` moves.
3. Docs: the `tw_text` page, `tasks.md` and `add-task.md`.
4. The locks against `main`, and a smoke run on OLMo 2 1B in FP32.

---

## 4. How it was actually built

- **Removed tasks (6).** `tw_text_gov_press__other`, `tw_text_judicial__other`, `tw_text_ly__質詢`, `tw_text_theses__NCHU`, `tw_text_theses__NCKU`, `tw_text_theses__NCU`.
- **Added tasks (12) and one suite.** `tw_text_gov_press__{行政院,金管會,農業部}`, `tw_text_judicial__高等`, `tw_text_ptt__{Stock,movie,Tech_Job,MobileComm,WomenTalk,Baseball,Gossiping,HatePolitics}`, and suite `tw_text:ptt`. The tasks go from 18 to 24.
- **Same name, new domain.** `tw_text_cnyes` and `tw_text_zhwiki` now read the domains `cnyes` and `zhwiki`, not `other`. Both clear the floor: 1,193,110 and 2,001,359 tokens.
- **Same name, new rows.** Every task that keeps its name reads the new revision, so its hash moves with the revision, as at the repin in 026.
- **Size.** 11,643 documents and 20,762,474 OLMo 2 tokens. Each source is sampled to about 2 million tokens, or all it has where that is less (`cnyes`, `twreporter`). `ly` holds 25 documents in those tokens and `theses` 34.
- **Locks.** On a tiny Llama model, every scorer output, `results.json` and predictions file is byte-equal to `main`, and the 309 Paloma hashes and `fineweb_edu`'s are unchanged. The snapshot diff is the `tw_text` tasks and suites and nothing else.
- **Smoke** at `v0` (one H200, OLMo 2 1B `a1847dff`, FP32, `batch_tokens` 16384, 1377 s for the suite, at `03c0181`). Every task's metrics are finite and no window is truncated. `tw_text` scores 1.6874 bits per byte. By source: `judicial` 1.6578, `ly` 1.4599, `gov_press` 1.6660, `cna` 1.7930, `pts` 1.8660, `ltn` 1.7873, `cnyes` 1.7363, `twreporter` 1.7653, `ptt` 1.8965, `zhwiki` 1.6668, `theses` 1.2671. `ptt` is the highest source and `theses` the lowest. These don't compare with 026's numbers at `sample-2` (029.D3).
- **A slow node gives the same bytes.** The `--limit 20` run on a node whose GPU was held at about 150 W by a power brake took 2026 s against 304 s and wrote the same 24 predictions files byte for byte, with every metric equal. This covers one model and 20 documents per task, not every run on that node.
