# 026: Taiwan Traditional Chinese text, scored as Paloma scores its sources

| | |
| --- | --- |
| Status | in review |
| Date | 2026-11-04 |
| Builds on | [008](008-paloma.md) (`PerplexityTask`, `source_suite`, a source as the mean of its domains), [017](017-zh-tw-suite.md) (`zh_tw:bpb`, an unweighted mean over Traditional Chinese benchmarks) |
| Keeps | Paloma's 309 tasks, suites and hashes, every request and every multiple-choice result |
| Result | suite `tw_text` over 10 sources and 22 domain tasks of `cerulean-works/tw-text-v0`, windows of 2048 tokens, scored by `bits_per_byte`; pinned to the dataset's first sample, to be repinned when the set is final |

## 1. Goal

The Traditional Chinese tasks on `main` are exams and reading comprehension (017). None measures how well a model fits the text written in Taiwan, across its registers, after the model's training cutoff. `cerulean-works/tw-text-v0` is that text: documents published from 2026-04-01 on, one configuration per source, in Paloma's row shape (`id`, `text`, `subdomain`). This stage scores it the way Paloma is scored:

1. **A task per domain** of each source, in windows of 2048 tokens, reporting `bits_per_byte`, `loss_per_token` and `perplexity`.
2. **A suite per source**, `tw_text:<source>`, the unweighted mean of its domains' `bits_per_byte`, as `paloma:<source>`.
3. **Suite `tw_text`**, the unweighted mean of the 10 sources.

**Out of scope:** building or changing the dataset; scoring any model beyond a smoke run; a public copy of the data.

---

## 2. Design decisions

### 026.D1. One `source_suite` for Paloma and `tw_text`

008 built a Paloma source as a suite of one task per domain in `tasks/paloma.py`. `tw_text` is the same construction over another dataset, so the function moves to `tasks/suite.py` and takes the benchmark's name and the source's dataset: `source_suite("paloma", dataset, domains)` builds exactly the suites and tasks it built before (Paloma's 309 hashes and snapshot entries don't move). Task names keep 008's shape: `<benchmark>_<source>__<domain>`, and `<benchmark>_<source>` for a source with one domain.

A one-domain source of Paloma has rows without `subdomain`. A one-domain source of `tw_text` has rows whose `subdomain` is the source's name. So a source listed with one domain is one task named by the source, filtered to that domain; a source listed with none is one task over rows without a domain. No Paloma source lists exactly one domain, so this changes no Paloma task.

### 026.D2. A source is the unweighted mean of its domains, as in Paloma

Paloma macro-averages a source's domains (sec. 4) and 008 does the same in `bits_per_byte`. `tw_text` follows it, so `tw_text:<source>` reads like `paloma:<source>`. The domains are the `subdomain` values the dataset gives: the court of a judgment (地方 district, 高等 high, 行政 administrative, 最高 supreme; the administrative courts are a separate branch, not a level), the kind of Legislative Yuan record (委員會紀錄, 院會紀錄, 質詢), the ministry of a press release, and the university of a thesis. They are uneven: in the first sample, 3 theses are from NCHU and 150 from NYCU, and 4 judgments are from the Supreme Court. The mean gives each domain an equal say, as Paloma intends ("to tolerate variance equally for each domain", App. C.2.1), and a small domain makes its source's number noisier. Paloma samples about 100 thousand tokens per domain to limit that; this dataset caps tokens per source, not per domain.

### 026.D3. `tw_text` is scored: the mean of its sources

Paloma defines no score over its sources, so `paloma` is unscored. `tw_text` is one held-out set built for one purpose, comparing how well models fit Taiwan's text after their cutoff, so it has a score: the unweighted mean of the 10 source suites' `bits_per_byte`, as `zh_tw:bpb` averages its benchmarks (017). No prior work defines this mean. Each source counts once, whatever its size, so 38 Wikipedia pages weigh as much as 193 Reporter articles. `theses` is in the mean although it is not held out in time (a thesis is dated only by its academic year, 115), and the docs say so next to it. Read the sources first; the mean ranks models.

### 026.D4. Window 2048, Paloma's

Every model the project compares has at least 2048 tokens of context, TAIDE-LX-7B (Llama 2, 4096) included. 2048 is Paloma's window (008) and `PerplexityTask`'s default, which every `tw_text` task keeps.

### 026.D5. Ten sources, no converted text

The dataset's first sample has 11 configurations. `bbc_zh_trad` is text converted from Simplified script, and converted text gets a name of its own (005, 008 section 6), so it moves to its own dataset. The suite has the other 10: `judicial`, `ly`, `gov_press`, `cna`, `pts`, `ltn`, `cnyes`, `twreporter`, `zhwiki` and `theses`.

### 026.D6. The domain table is copied from the pinned revision, and a repin updates both

`SOURCES` in `tasks/tw_text.py` lists each source's domains at the pinned revision, in descending order of documents, as `DOMAINS` does for Paloma. A task whose domain has no rows raises, so a repin that changes the domains must change the table in the same commit. The dataset is private, so the task names, which hold the domains, are the public record of what the set contains.

---

## 3. Plan

1. Move `source_suite` to `tasks/suite.py` with the benchmark and dataset as arguments; Paloma's tests and snapshot unchanged.
2. `tasks/tw_text.py`: the table, the sources and the suite, registered.
3. Tests: the registered suite and its tasks, one-domain sources filtered to their domain, `bbc_zh_trad` absent.
4. Snapshot regenerated: the new tasks and suites, nothing else.
5. Docs: a page for the suite, plus `tasks.md`.
6. A smoke run on OLMo 2 1B in FP32, and the locks against `main`.

---

## 4. How it was actually built

- **Revision.** The tasks pin `24e735df`, the dataset's first sample of about 200 documents per source drawn before the crawler's final filter. In it the smallest domain is NCHU's 3 theses (171,428 OLMo 2 tokens) and the Supreme Court's 4 judgments (8,607), and the largest is the Legislative Yuan's committee records (101 documents, 8.3 million tokens). The final revision replaces it in one commit with its table (026.D6).
- **Theses are full texts.** The `theses` rows are whole theses with their front matter in English and Chinese (median 94 thousand characters), not abstracts. With `ly`, they are most of the suite's tokens and of its scoring time.
- **The domain table is not checked against the split.** A task raises when its domain has no rows, but a row whose `subdomain` no task lists is never scored, as in Paloma, whose table is also copied from a census (`paloma_census.py`). A repin copies the table from a census of the new revision. A repin that leaves a source with one domain, or gives a one-domain source a second, renames its tasks, as Paloma's naming does.
