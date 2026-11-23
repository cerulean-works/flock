# 043: tc-edu many-shot tasks

| | |
| --- | --- |
| Status | built |
| Date | 2026-11-22 |
| Builds on | [039](039-tc-edu.md) (the tc-edu items, format and private dataset), [009](009-tw-mock-exams.md) (`exam_task`), [012](012-task-snapshot.md) (the task snapshot) |
| Keeps | every existing task, request, task hash and suite; `tc_edu` keeps its pinned revision; the snapshot only gains the new tasks and one toy prompt path |
| Result | 17 `tc_edu_manyshot_<budget>_<subject>` tasks (11 budgets and 6 five-shot baselines) on a new revision of `cerulean-works/tc-edu-exams` that adds an ordered `long_context` split, and `Fewshot`'s third form, a split's first `num_shots` rows |

The decision numbers are provisional: `043.D1` to `043.D6` take the next free global numbers after every stage merged before it.

## 1. Goal

tc-edu has no long native passages: every passage group in the reserve fits in about 4,400 tokens, and the long source files share files with the frozen test. A long-context measurement that the items can carry is many-shot in-context learning (Agarwal et al. 2024, arXiv 2404.11018): the frozen test questions, each preceded by many answered questions of the same subject, up to a fixed token budget. The question it answers is how much a model gains from a long prompt of examples over 5 of them.

**Out of scope:** changing `tc_edu`'s tasks, any training use of the items, prefix caching in the scorer, and subjects with no held-out long-context items (arts, health and PE, integrative activities).

---

## 2. Design decisions

### 043.D1. Shots are a fixed, ordered list per subject, and each task shows a prefix of it

The examples are held-out items of the subject's `reserve` that share no question group, normalized passage, source file or near-duplicate (half of their 13-grams, or a 30-character span) with `dev` or `test`, every one read and passed. They are ordered once per subject by SHA-256 of `170:<uid>`. Every budget and the baseline show a prefix of that one order, so a larger prompt holds every example of a smaller one, as Agarwal et al. build theirs ("any K-shot prompt in our setup includes all in-context examples from prompts with less than K examples", p.3). Every doc sees the same examples, and every model sees the same requests.

Agarwal et al. average several random draws of the examples; a fixed draw is one instrument for every model, so these tasks keep one, and the draw's own variance is a known gap (section 5).

### 043.D2. A budget bounds the whole request under nine tokenizers

`4k`, `8k` and `32k` mean 4,096, 8,192 and 32,768 tokens. A task's shot count is the largest prefix for which every complete request (header, the examples each followed by a blank line, the target's choice, and BOS for a model that adds it) is at most the budget under the tokenizers of nine reference models: OLMo 2 1B, Llama 3.2 1B, Qwen3.5 0.8B and 4B, Gemma 4 E2B, TAIDE-LX-7B, Llama-3-Taiwan-8B, and Breeze 7B base and instruct. A model with at least that context is then scored without truncation. OLMo 2's tokenizer, which spends 1.5 to 2 times as many tokens on Chinese as Qwen's, binds every Chinese budget, and TAIDE's and Breeze's bind English. The counts were checked by tokenizing the complete request of each tokenizer's 20 longest targets.

No reference sets budgets this way; it keeps "8k" one prompt for every model, at the cost of fewer examples for models with leaner tokenizers.

### 043.D3. A budget names a task only with at least 10 examples

A budget holding fewer than twice the baseline's 5 examples measures little, and a budget the list runs out before is not filled. Senior-high and junior-high Chinese hold 1 example at 4k (their longest targets take most of it), and senior-high Chinese 7 at 8k; English and math run out of items before 8k (112 and 79). That leaves 11 tasks:

| Subject | 4k | 8k | 32k |
| --- | ---: | ---: | ---: |
| hs_chinese | | | 91 |
| jh_chinese | | 17 | 114 |
| jh_science | 22 | 61 | 228 |
| jh_social | 19 | 46 | 192 |
| jh_english | 80 | | |
| jh_math | 42 | | |

### 043.D4. Each subject's baseline is the first 5 examples of the same list

`tc_edu_manyshot_5shot_<subject>` shows the first 5 rows of the list, so a budget's gain over it comes from the number of examples alone. `tc_edu_<subject>`'s 5 examples are the shortest self-contained `dev` items, a different selection, so it is not the baseline.

### 043.D5. The ordered list is a split of a new dataset revision, and the code names only counts

Revision `83677842` of `cerulean-works/tc-edu-exams` adds `long_context` to the six subjects' configs: the list's rows, copied from `reserve` with the same columns, in the list's order. `dev`, `test` and `reserve` are byte-identical to `66c8db07`, which `tc_edu` keeps. `Fewshot(split="long_context", num_shots=N)`, with no ids, shows each doc the split's first N rows (043.D6), so the code holds no item ids and the selection stays in the private dataset. No suite groups the tasks: subjects differ by budget, so a mean over them would not compare across budgets.

### 043.D6. `Fewshot` takes a split's first `num_shots` rows when it has no ids

`num_shots` already means "show the first `num_shots` examples"; with no ids and no rows, the examples are the split's rows in order. The split must not be the scored one, since those docs would see themselves and nothing drops them by id, and loading fails if the split is shorter than `num_shots`. The cross-split rule still applies: an example that shows a doc's own question and answer is not shown to it. The field already existed, so every task hash is unchanged.

---

## 3. Plan

1. Measure the held-out items and test targets under the nine tokenizers, fix the order, and compute the counts.
2. Add the `long_context` split as a new revision and check the old splits are unchanged.
3. Add `Fewshot`'s prefix form, the `fewshot` argument of `exam_task`, and the 17 tasks.
4. Regenerate the snapshot, add a toy task for the prefix form, test, and update the task docs.

---

## 4. How it was actually built

- **Measure.** Units per subject: senior-high Chinese 1,189, junior-high Chinese 4,555, social 1,862, science 482, English 112, math 79. Contexts: OLMo 2 1B and TAIDE 4,096, Llama-3-Taiwan 8,192, Breeze 32,768, the others 131,072 or more. Without OLMo 2 in the bound the counts would be up to 1.6 times larger; it stays in, so every 4k task scores OLMo 2 too.
- **Revision.** The upload is one commit on top of `66c8db07` adding six parquet files and the README's split entries; all 27 old files kept their LFS hashes, and each new file's order matched the precomputed digest.
- **`exam_task`** takes an optional `fewshot`, the subject's `dev` shots by default, so the many-shot tasks share `tc_edu`'s header, format, metric and PMI context.

---

## 5. Known gaps

- One draw of examples per subject: a different order would give a somewhat different gain.
- The scorer has no prefix cache, so each of the about 4,000 requests of a 32k task scores the full prompt; a bank of the nine models takes tens of H200-hours.
- Under the all-tokenizer bound, models with lean tokenizers see fewer examples than their context allows.
- The held-out items were read once, by language models; a blind second read of 300 found 12 defective (an exact 95 percent upper bound of 6.4 percent).
