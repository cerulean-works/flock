# 044: where each row comes from, lm-eval's hashes, and the tokenizer

| | |
| --- | --- |
| Status | built |
| Date | 2026-11-23 |
| Builds on | [041](041-result-files.md) (041.D2 prediction rows), [043](043-item-text.md) (043.D1 text in the rows, 043.D5 `items`) |
| Amends | 041.D2 (a row also says where its doc comes from), 043.D5 (an item carries its row's `source`) |
| Keeps | every task hash and suite hash, every request and score, every metric, every field schema 11 writes, which files a run uploads |
| Result | predictions schema 12: every multiple-choice row has `split`, `source_index`, `doc_hash` and `prompt_hash`, and `native_id`, `subject` and `meta` where the task names their columns in `row_fields`; perplexity rows have `source` and `domain`; the provenance names the tokenizer and hashes it |

The decision numbers are provisional: `044.D1` to `044.D4` take the next free global numbers after every stage merged before it.

## 1. Goal

A row says what a model answered, but not where its doc sits in the dataset, so joining it to anything else (the dataset row, an lm-evaluation-harness sample log, a subject list, a difficulty level) means rebuilding the task's loading. Analyses on item-level outputs, such as item response theory, per-subject slices and label audits, need those joins and nothing per token. This stage records them in the row, and records the tokenizer, which the per-token values of the next stage need.

**Out of scope:** per-token values (stage 045), checkpoint step or tokens seen per run, a prompt-template id, an exporter to another format, any change to a task hash or a metric.

---

## 2. Design decisions

### 044.D1. A task names the columns its rows copy, by name, outside the task hash

`MultipleChoiceTask` gains `row_fields=RowFields(native_id=..., subject=..., meta=(...))`, column names, as OLMES's task configs name the column of a dataset's own id in `native_id_field` (`oe_eval/tasks/oe_eval_tasks/arc.py` and its neighbours). A declaration is one line per task, and adding one changes no request, so it stays out of the task hash, as `to_doc` and `keep` do, and past results stay comparable. `native_id` is written only where it differs from the doc's `id`: most tasks already use the dataset's id. `meta` holds only strings, numbers, booleans and nulls, as Inspect keeps sample summary metadata to scalars, so a reader can show and filter it without knowing the task. Declared now: HellaSwag (`ind`, OLMES's native id, the activity label as subject, `source_id` and `split_type`), MMLU (`subject`) and Minerva MATH (`level`).

### 044.D2. Every row records its split, its position and lm-evaluation-harness's two hashes

`load` gives each scored doc a `SourceRow`: the split, the row's position in it counted before `keep` drops rows, and `doc_hash`, the sha256 of `json.dumps(row, indent=2, ensure_ascii=False)` of the row as the dataset gives it (the `index` that `row_index` adds is removed first). The row gains `prompt_hash`, the sha256 of the doc's first request's context, few-shot examples included. Both are lm-evaluation-harness's definitions (`lm_eval/evaluator.py`, v0.4.3, the `log_samples` block), so a sample log of the same row joins on `doc_hash` whatever the prompt format. lm-evaluation-harness's tasks that rewrite rows with `process_docs` hash the rewritten row, so those don't join; ours hash the raw row.

### 044.D3. Perplexity rows name their source and domain

Each perplexity row gains `source` (the dataset subset, a Paloma source) and `domain` (the task's `subdomain`), `null` where there is none, so rows of many tasks pool and split again without the task registry. No text, as before.

### 044.D4. The provenance names the tokenizer and hashes its definition

`tokenizer` is the tokenizer's `name_or_path`, or the SentencePiece model file, and `tokenizer_hash` the sha256 of `backend_tokenizer.to_str()` (the whole `tokenizer.json`, normalizer and added tokens included) or of the SentencePiece model's bytes. Token ids are meaningless without the tokenizer that made them; the Open LLM Leaderboard v1 kept hashes of its input tokens for the same reason. Older files read both as `None`.

---

## 3. Plan

1. `RowFields`, `SourceRow`, `MCDoc.source`, set in `load`; rows write the fields; the snapshot pins `row_fields`.
2. Perplexity rows' `source` and `domain`; the provenance's tokenizer fields; schema 12 in the reader, the tracker and the app's list of schemas.
3. `items` and `flock eval items` show the source; tests and docs.

---

## 4. How it was actually built

- `load` numbers the rows before `keep` filters them, so `source_index` finds the row in the dataset whatever the task keeps.
- The hash of a `row_index` dataset's row drops the `index` key that `Dataset.load` adds, so it is the hash of the row as Hugging Face gives it. A test checks both hashes against lm-evaluation-harness's formula, written out.
- `prompt_hash` takes the context of the doc's first request, as lm-evaluation-harness takes `requests[0].arguments[0]`; each doc's requests are consecutive, so the row writer walks them by choice counts.
- A schema 11 folder still reads, and its items carry an empty `source`. The app's list of readable schemas gains 12; its two tests that used 12 as an unknown schema use 13.
- The regenerated task snapshot differs only by the new `row_fields` of every task, and `test_hash_is_stable` keeps every pinned hash.

## 5. Known gaps

- Only three task families declare `row_fields`; the rest record split, position and hashes only.
- `doc_hash` joins lm-evaluation-harness samples only for tasks that keep its rows as they are.
- `meta` values come from the scored rows; a few-shot example's row is not recorded.
