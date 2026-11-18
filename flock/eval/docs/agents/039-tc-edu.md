# 039: tc-edu, junior-high and senior-high question banks

| | |
| --- | --- |
| Status | in review |
| Date | 2026-11-18 |
| Builds on | [009](009-tw-mock-exams.md) (the mock exams' format, shots and private repo), [012](012-task-snapshot.md) (the task snapshot) |
| Keeps | every `tw_mock_exams` task, request, task hash and suite; the snapshot only gains the new tasks and suites |
| Result | nine `tc_edu_*` tasks on 9,000 read and passed items from a private dataset, the suites `tc_edu` and `tc_edu:jh` with their `bpb` twins, and the mock exams' task constructor shared as `exam_task` |

## 1. Goal

Score every model on Taiwanese school exam items below senior-high level. The `tc-edu` import holds publishers' question banks for junior high (eight subjects) and senior-high Chinese: 265,271 question units, many of them damaged by Word conversion. This stage freezes a clean, read sample of them as a private dataset and scores it with the instrument the mock exams already use, so tc-edu, TMMLU+ and the mock exams are three measurements under one format.

**Out of scope:** multiple-select and cloze items, the full pool as a task (a different size is a different task), and any training use of tc-edu text.

## 2. Design decisions

### 039.D1. The items live in a private dataset with one config per subject

`cerulean-works/tc-edu-exams` holds nine configs (`hs_chinese`, `jh_chinese`, `jh_english`, `jh_math`, `jh_science`, `jh_social`, `jh_arts`, `jh_health_and_pe`, `jh_integrative_activities`), each with `dev`, `test` and `reserve`. Rows use the mock exams' columns (`hash`, `passage`, `stem`, `options`, `answer`) plus the source fields, a `rights` class and the result of the read (`check`). It is private for the mock exams' reason (009.D1): most items are publishers' copyrighted teaching material with no licence on record. `rights` marks each row `publisher`, `official_paper` (a named national exam paper, kept and scored) or `past_items_unnamed`. Of the 9,000 test items, 252 are `official_paper` and 105 `past_items_unnamed`.

### 039.D2. Which items are eligible

From all 265,271 units, in order:

1. Single answer, every option with text, no asset reference, no picture, table or audio marker in the shown text, no damaged characters, none of the importer's review flags, no Office field code in the shown text and no follow-up stem (承上題 and the like). Single answer and text only follow TMLU (2403.20180) and the mock exams (009.D2); field codes are this source's own conversion damage. 189,786 remain.
2. Whole question groups only: a group the importer parsed only in part loses all its units. 187,783 remain.
3. Duplicates by normalized text (lower case, no whitespace or punctuation) keep the first in dataset order. 182,395 remain.
4. Any unit that shares its exact stem (at least 20 characters) with, or has at least half of its 13-grams inside, `tw_mock_exams` or TMMLU+ is dropped, so the three sets stay separate measurements. 180,824 remain.

The mock exams and TMMLU+ are unchanged. TMMLU+'s junior subjects are largely reprinted in tc-edu; that is recorded, not acted on.

### 039.D3. The split is by question group, and `test` is a read sample of 1,000 per subject

Within a subject, the question groups and single items whose SHA-256 of their record id ranks in the lowest 1 percent (at least one) form `dev`, the few-shot pool; the rest is the test pool. A group is never split, so a test question never sees its own passage among the shots. Hashing, not row order, because the import is ordered by publisher and semester.

The test pool is ordered by SHA-256 of `154:<uid>`. Walking that order, the first 1,000 items that pass a read are `test`; a defective item is skipped and the next one taken. The remaining items, read or not, are `reserve` in the same order. 1,000 per subject keeps a nine-model bank near one H200-hour; the full pool is about 21.

### 039.D4. Every scored item and shot was read before freezing

A text filter still leaves about one defective item in eight (a blind read of 72 kept items found 9). So every candidate was read against a fixed rubric, each reader seeing the passage, stem, options, key and explanation, and coding the item `OK`, `UNSURE` (complete, key not confirmed by the reader) or one or more defects: incomplete stem, missing non-text material, broken options, damaged text, wrong key, no single correct option, or the answer leaked by an artefact. `OK` and `UNSURE` pass. 9,507 items were read; 457 sampled items and 5 shot candidates failed and were replaced. Per subject, the failures were: senior-high Chinese 69, junior-high Chinese 64, English 82, math 118 (mostly exponents flattened into plain digits), science 32, social 42, arts 23, health and PE 15, integrative activities 12. `check` records `ok` or `unsure` on every test row; 582 test rows are `unsure`, most of them in arts (258) and health and PE (124), where readers could not confirm recall keys. No precedent in OLMES or OLMo 3 covers reading a sample before freezing; it is our choice for a source whose defect rate is known.

### 039.D5. One format and one constructor for both exam sets

tc-edu rows have the mock exams' shape (passages, two to six options, English items), so tc-edu is scored exactly as `tw_mock_exams` (009.D3): TMMLU+'s header with the subject's Chinese name, `問題：` and `答案：`, cloze over option texts, `acc_per_char`; English takes OLMES's MMLU header and format. The mock exams' `subject_task` becomes `exam_task(subject, *, prefix, repo, revision, keep)`, and both sets are lists of `Subject` values. English is chosen by the subject's Chinese name, 英文, as `Subject` already documented. Every mock exams request and task hash is unchanged, and the snapshot shows only additions. tc-edu passes no `keep`: every `test` row is scored.

### 039.D6. Shots are the five shortest self-contained `dev` items

As 009.D4: `dev` items with no passage and no reference to other material in the stem, by length of stem plus gold option, then id. Each shot was read like a test item; a failing one was replaced by the next.

### 039.D7. Suites

`tc_edu` is the unweighted mean of the nine subjects, as the mock exams' suite is (009.D5), and `tc_edu:jh` of the eight junior-high subjects. Each has a `bpb` twin, and `tc_edu:all` runs the four unscored. tc-edu is not added to `zh_tw`, whose members and hash stay as they are.

## 3. Plan

1. Rebuild the eligible set from the pinned import and check it against the audited counts per subject.
2. Split, order, read, replace, and freeze the dataset at a pinned revision.
3. Share the mock exams' task constructor and declare the nine subjects.
4. Regenerate the snapshot, test, and load every task from the pinned revision.

## 4. How it was actually built

- **Rebuild.** The four steps of 039.D2 reproduced the audited per-subject counts exactly before anything else ran (180,824 units). Per subject: senior-high Chinese 11,859, Chinese 41,737, English 25,938, math 7,155, science 25,715, social 59,820, arts 2,812, health and PE 3,819, integrative activities 1,969.
- **Read.** 61 batches of up to 150 items in the first round, then 438, 22 and 2 replacements in three more rounds. Each reader returned only ids and codes. Readers differed on two borderline marks, flattened exponents on a numeric base (`10－7`) and stray macrons (`ˉ`): some coded them damaged text, others passed them when the meaning stayed clear, so math's failure count leans on the stricter readers.
- **Schema.** The first upload failed to load one config: `number` (an item's place in its group) was null in every row of one split and an integer in the others, and the loader cannot reconcile a null column. Each config is now written with one schema inferred from all its rows.
- **Load.** All nine tasks load 1,000 docs and 5 shots from the pinned revision.

## 5. Known gaps

- The read is by language models, not people; its rubric and counts are in 039.D4. A defect the readers missed stays in `test`.
- `UNSURE` passes, so a share of arts and health and PE keys is unconfirmed by any reader.
- `reserve` is mostly unread; using it as test data needs the same read first.
- This file follows 038 (`tw-text-v1`), which is not merged yet. If this stage merges first, it takes 038 and its decisions are renumbered.
