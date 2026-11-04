# 027: held-out web text in English, Traditional Chinese and Simplified Chinese at one window

| | |
| --- | --- |
| Status | in review |
| Date | 2026-11-04 |
| Builds on | [008](008-paloma.md) (`PerplexityTask`, `bits_per_byte` as a ratio of sums), [019](019-fineweb-edu-dev.md) (`loss_per_token` and `perplexity`, the FineWeb-Edu dev set) |
| Keeps | `fineweb_edu` and its hash, Paloma's 309 hashes, every request and every multiple-choice result |
| Result | suite `fineweb` with `fineweb_en`, `fineweb_zh_hant` and `fineweb_zh_hans`, windows of 2048 tokens, scored by `bits_per_byte`; OLMo 2 1B in FP32 scores 0.6359, 1.8175 and 1.6803 bits per byte, suite 1.3779 |

## 1. Goal

Small proxy runs forecast the main run by their held-out loss on the kind of text they train on. The training mix is web text in English and Chinese (DCLM, FineWeb-Edu and FineWeb-2 in `flock-datasets`), but the only held-out web set in `flock.eval` is `fineweb_edu`, which is English and needs a context of 8192 tokens that the proxies and OLMo 2 1B (4096) don't have. This stage adds one suite that every model can score:

1. **`fineweb_en`**: the FineWeb-Edu dev set of 019 at window 2048.
2. **`fineweb_zh_hant`** and **`fineweb_zh_hans`**: FineWeb-2's held-out Chinese, split by script.
3. **Suite `fineweb`**: the three, scored as the mean of their `bits_per_byte`.

**Out of scope:** changing `fineweb_edu`; text from Taiwan specifically (FineWeb-2's Traditional text is written wherever Traditional is written); removing near-duplicates of the dev sets from training data; any change to `flock.training`.

---

## 2. Design decisions

### 027.D1. `fineweb_en` is 019's dev set under a new name, not a second window of `fineweb_edu`

A task's window decides its requests, so a number at 2048 tokens is a different number from the one at 8192 and gets its own name. `fineweb_edu` stays the 8192-token task (019.D4) and its hash does not move. `fineweb_en` reads the same rows, `cerulean-works/fineweb-edu-dev` at revision `2484afe0`, split `validation`, so the ids training data drops are the same list. FineWeb-Edu is FineWeb kept by an educational-quality classifier, while the Chinese members are FineWeb-2 without such a filter, so English and Chinese differ in filtering as well as in language. The English member reuses 019's set so the lab's held-out list stays one list.

### 027.D2. The Chinese members are FineWeb-2's `test` split of `cmn_Hani`, split by script with `hanzidentifier`

FineWeb-2 (Penedo et al. 2025, arXiv 2506.20920) has a `test` split in most languages, which its dataset card says "should not be trained on"; the paper draws it "as a random subset (by a hash function applied on the document content)" (App. A.13). That is a held-out set of the same distribution as the training split, with no sampling of ours needed to hold it out. Chinese is one language code, `cmn_Hani`, with no script split. Mimir (arXiv 2605.25263, p. 3) splits it with the `hanzidentifier` library, and this stage does the same: `hanzidentifier.identify` (1.3.0, with zhon 2.1.1) on each document's whole text, TRADITIONAL to `zh_hant` and SIMPLIFIED to `zh_hans`.

At revision `af9c1333` the split is one file of 33,296 documents: 25,978 SIMPLIFIED, 3,383 TRADITIONAL, 3,935 MIXED, and none BOTH or UNKNOWN. MIXED documents, those with characters unique to each script, are dropped. A majority rule would keep some of them, but no prior work sets its threshold. The MIXED documents are longer (2,910 characters on average, against 1,048 for TRADITIONAL and 1,352 for SIMPLIFIED), so both members lean toward shorter pages. The dataset card says so. FineWeb-2 draws the `test` split before filtering and then filters it "with the same process as the train set" (App. A.13), so it passed the same filters as the text a model trains on.

### 027.D3. `zh_hant` takes every Traditional document and `zh_hans` samples 5,000

019 sampled 5,000 documents, which has no precedent beyond being enough tokens and quick to score. FineWeb-2 has only 3,383 Traditional documents, so `zh_hant` takes all of them and needs no seed. `zh_hans` draws 5,000 of the Simplified ones without replacement, uniformly, with seed `20261104`. The two members differ in size, which doesn't bias either number: `bits_per_byte` and `loss_per_token` are ratios of sums over a member's own documents (008.D5, 019.D1). By the OLMo 2 tokenizer, `zh_hant` is 4,828,303 tokens (9,665,366 bytes) and `zh_hans` 8,252,130 (19,737,910 bytes).

The rows are the private `cerulean-works/fineweb-2-zh-dev`, configurations `zh_hant` and `zh_hans`, split `test`, with FineWeb-2's `id`, `text`, `url`, `dump` and `date`. FineWeb-2's licence (ODC-By 1.0) allows a public copy. It stays private until the team decides to publish the suite's data.

### 027.D4. Window 2048 for all three members

2048 is Paloma's window (008) and the context of the 190M proxy runs, and every model the project compares has at least that much. One window for the three members keeps their numbers comparable as one instrument.

### 027.D5. The suite reads `bits_per_byte`; each member keeps `perplexity` as its primary metric

The suite's score averages its members, and only `bits_per_byte` has a unit that doesn't depend on the tokenizer, since it is normalized by UTF-8 bytes, not tokens. It does not make the languages equal: a Han character is three UTF-8 bytes and most English characters one, so the members sit at different levels, and the suite's mean ranks models, not languages. Each member counts once, as in every suite (OLMES's `macro` aggregate), so Chinese carries two thirds of the score: the suite leans toward the language the project adds. No prior work averages bits per byte across languages; Paloma averages within a source (008). Each member reports `loss_per_token` and `perplexity` as well, which are what a proxy's forecast fits against a run with the same tokenizer (019.D1). The members' `primary_metric` is `perplexity`, as `fineweb_edu`'s is, because that is the number the forecast reads one task at a time.

### 027.D6. Task names use `_`, the suite `fineweb`

No task name on `main` has a `:`: it marks suites (`paloma:c4_en` holds `paloma_c4_en`). The members follow that and are named `fineweb_<language>`.

---

## 3. Plan

1. Build the Chinese dev sets from FineWeb-2 with a fixed script, upload them, and pin the revision.
2. `tasks/fineweb.py`: the three tasks and the suite, registered.
3. Tests: the registered suite, its members' windows, datasets and metrics, and `fineweb_edu` unchanged.
4. Snapshot regenerated: three new tasks and one new suite, nothing else.
5. Docs: a page for the suite, plus `tasks.md`.
6. A smoke run on OLMo 2 1B in FP32 and the locks against `main`.

---

## 4. How it was actually built

- **Dev sets.** Built from FineWeb-2 revision `af9c1333`, file `data/cmn_Hani/test/000_00000.parquet`, with hanzidentifier 1.3.0 and zhon 2.1.1. A second build is byte-identical: both parquet files, both id lists and the record of counts and hashes. The plan's 5,000 documents per script met only 3,383 Traditional ones, so `zh_hant` takes them all (027.D3). Of the 3,935 MIXED documents, 1,513 have at least 95% Traditional-only characters among their script-unique ones and 1,800 at least 95% Simplified-only; they are dropped all the same (027.D2).
- **Dataset revisions.** The first upload's card said the `test` split was unfiltered and named the tasks with a `:`. Both were corrected before any number was kept, and the tasks pin the third revision, `6d54ae9e`. The parquet files are the same in all three.
- **`flock-datasets/fineweb-2` holds the `test` split.** Version 1 has 36,711 rows whose `_hf_split` is `test`, among them all 33,296 of `cmn_Hani`, and every one of the 8,383 Chinese dev ids is in it once. Training on it unfiltered trains on both Chinese members. A trainer that reads it keeps only rows whose `_hf_split` is `train`. The rest of the dataset is `cmn_Hani` too: 636,058,984 of its 636,386,306 `train` rows.
- **Locks.** On a tiny Llama model, every scorer output, `results.json` and predictions file is byte-equal to `main`, and the 309 Paloma hashes and `fineweb_edu`'s are unchanged. The snapshot diff is the three tasks and the suite.
- **Smoke** (one H200, OLMo 2 1B `a1847dff`, FP32, `batch_tokens` 16384, 1293 s for the suite). `fineweb_en` scores perplexity 9.2985, `loss_per_token` 2.2299 and `bits_per_byte` 0.6359; `fineweb_zh_hant` 12.4524, 2.5219 and 1.8175; `fineweb_zh_hans` 16.2135, 2.7858 and 1.6803. No window was truncated. Traditional is above Simplified in bits per byte, as expected of a model that saw little Traditional text, though its perplexity is lower: perplexity is per token, and the two scripts tokenize differently.
- **Task layout.** The two Chinese tasks were first built in one generator over the two scripts. Review asked for each task written out in full, as every other task module does, so the revision appears twice.
