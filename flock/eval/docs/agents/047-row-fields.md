# 047: row fields for every task family, and owner-only perplexity predictions

| | |
| --- | --- |
| Status | built |
| Date | 2026-11-23 |
| Builds on | [044](044-row-sources.md) (044.D1 `row_fields`), [042](042-private-predictions.md) (private predictions), [046](046-perplexity-tokens.md) (perplexity per-token values) |
| Amends | 044.D1 (declared beyond three families), 042 (a private perplexity task's predictions are owner-only too) |
| Keeps | every task hash and suite hash, every request and score, every metric, schema 12's fields, which files a run uploads |
| Result | 12 more task families name their dataset id, subject and scalar columns where the dataset has them; a private perplexity task's predictions file is created mode 600 like its other files |

The decision numbers are provisional: `047.D1` and `047.D2` take the next free global numbers after every stage merged before it.

## 1. Goal

Stage 044 declared `row_fields` for HellaSwag, MMLU and Minerva MATH only, so per-subject and per-level views worked on those three and nowhere else. This stage declares them for every family whose dataset has such a column, and closes the one file of a private task that was still readable beyond its owner: perplexity predictions.

**Out of scope:** subjects that are not dataset columns (a benchmark's configuration name, its category table), derived values, text passages, any change to a task hash or a metric.

---

## 2. Design decisions

### 047.D1. A task declares a column only when the dataset holds it as a scalar, and a private set only its metadata

The rule of 044.D1, applied to each family's columns at its pinned revision: `native_id` where the doc's `id` is a row position or a hash and the dataset has its own id (OLMES's `native_id_field` where OLMES has the task), `subject` for a subject or category column, `meta` for scalars worth slicing by (a level, an exam, a year, a flag). Text passages, lists and objects stay out. The private mock exams and tc-edu name only metadata columns (paper, group, number, section, exam, organizer, year, rights, provenance flags), never the stem, options, passage, answer, explanation or a group title that can hold instructions. Declared:

| Family | Declared | Left out, and why |
| --- | --- | --- |
| CSQA | `meta`: `question_concept` | `id` is already the doc id; `choices` is a list |
| Qasper yes/no | `meta`: `nlp_background`, `topic_background`, `paper_read` | `title`, `abstract`, `full_text`, `evidence` are text or objects; `question_writer` is an annotator hash |
| LAB-Bench | `subject`: `subtask` | `source` is always null; `canary` is a constant string |
| MedMCQA | `subject`: `subject_name`; `meta`: `topic_name`, `choice_type` | `exp` is text |
| MedQA | `meta`: `meta_info` | no id column (OLMES uses the position too) |
| Jeopardy | `subject`: `category_original` | the rest is text |
| SQuAD | `subject`: `title_original` | the rest is text |
| Basic skills | `subject`: `domain`; `meta`: `sub_domain` | `answer_function`, `unit_tests` are code |
| Minerva MATH | adds `subject`: `type` | `solution` is text |
| Multilingual MBPP | `subject`: `language` | `task_id` is already the doc id |
| DRCD | `subject`: `title` | `context` is a passage |
| Mock exams | `subject`: `doc_subject`; 11 `meta` columns | item text, `group_title`, `answer_source`, and the flags `keep` makes constant |
| tc-edu | `native_id`: `uid`; 8 `meta` columns | item text, `explanation` |

Nothing to declare for ARC, WinoGrande, SocialIQA, PIQA, SciQ (`support` is a passage), SciRIFF (`metadata` is an object), CoQA, DROP, Natural Questions, LAMBADA, HumanEval, MBPP, TMMLU+, C-Eval (its `id` equals the row position in all 12,342 rows) and CMMLU. TMMLU+, C-Eval and CMMLU keep their subject in the configuration and their category in the suite, not in a column.

### 047.D2. Every file of a private task is its owner's only

`write_perplexity_predictions` takes `private`, as the other three writers do, and creates the file 600 for a task on a private dataset. Its rows hold ids and numbers and no text, but one rule for every file of a private task leaves nothing to check per file type.

---

## 3. How it was actually built

- `_gen2mc_task` and `exam_task` take `row_fields` as a required argument (a default instance in a signature is ruff's B008), so each caller states its columns.
- Every declared task was loaded over all its rows: no missing column and no non-scalar value.
- Every multiple-choice task's hash over its full data is equal before and after, and equal to the bank's where it has the task; the regenerated snapshot differs only in `row_fields`.
- A test scores a private and a public text task under umask 022 and checks 600 and 644 for both files.
- Review found two gaps in 044's copying, fixed here: a null id or subject was written as the string `"None"`, which would join unrelated rows on one id, and is now left missing; a NaN or infinite `meta` value would have written a line no strict JSON reader takes, and is now `null`, as `results.json` writes a non-finite metric. No declared column holds either today.
