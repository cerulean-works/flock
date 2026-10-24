# 005: TMMLU+, the first Traditional Chinese task

| | |
| --- | --- |
| Status | design, for review before any code |
| Date | 2026-10-23 |
| Builds on | [001](001-eval-core.md) (scoring core); 003 (choices as pairs, `Suite`), stacked on its suites PR |
| Keeps | 001's scoring, batching, metrics and results file; 003's `Choice`, `MCDoc`, `Fewshot` and `Suite` |
| Result | not built yet |

The file number and the decision numbers are provisional. The file takes the next free number when its PR is opened, and decisions `005.D1` to `005.D6` take the next free global numbers after every stage merged before it.

## 1. Goal

1. **TMMLU+ as data.** `ikala/tmmluplus` v1.1 (Tam et al. 2024, arXiv 2403.01858), all 66 subjects of its test split, scored as cloze rank classification with `acc_per_char` as the primary metric and gold-answer `bpb` beside it, 5-shot, as OLMES scores MMLU (OLMES, arXiv 2406.08446; OLMo 3, arXiv 2512.13961, sec. 3.3.2).
2. **The aggregate TMMLU+ publishes.** The total is the mean of the four category means (STEM, Social Sciences, Humanities, Other), "matching the ievals methodology" (dataset card, v1.1).
3. **A Taiwan subset.** `geography_of_taiwan`, `ttqav2` and `taiwanese_hokkien`, the three subjects behind the Taiwan columns (台灣地理, 台灣用語, 閩南語) on the Gemma-3-TAIDE model cards, reported per subject. Mapping 台灣用語 to `ttqav2` is our inference: the cards don't name the subject, and `ttqav2` is the only TMMLU+ subject about Taiwanese terms.
4. **A Chinese prompt format** that later Traditional Chinese tasks reuse.

**Out of scope:** TAIDE models (gated), converted or machine-translated sets (they would get their own task names), decontamination against a pretraining corpus, and generative scoring.

---

## 2. The dataset, read at the pinned revision

Read from `ikala/tmmluplus` at `94d86f1d013d59f389b710a902af30b7a0aa8c8f` (tag `v1.1`). `main` is now `639a64db464b`, whose data files and configs are identical; it changes only the card and images. Every config has four columns of choices `A` to `D`, a `question` and a letter `answer`, and three splits: `train` (the `*_dev.csv` files, at most 5 rows), `validation` and `test`.

| | Subjects | test | dev (`train`) | validation |
| --- | --- | --- | --- | --- |
| STEM | 14 | 3,335 | 65 | 377 |
| Social Sciences | 18 | 5,864 | 89 | 657 |
| Humanities | 7 | 1,723 | 34 | 189 |
| Other | 27 | 8,724 | 129 | 974 |
| **Total** | **66** | **19,646** | **317** | **2,197** |

- **The card says 19,680 test rows; the 66 configs hold 19,646.** The card's v1.1 table counts a 67th subject, `linear_algebra` (34 test, 4 dev, 5 validation rows). Its CSV files are in the repo, but the README declares no config for it, so `load_dataset("ikala/tmmluplus", "linear_algebra")` can't load it, and neither the card's own task list nor lm-evaluation-harness's `tmmluplus` group includes it. With it, every number in the card's table matches ours (19,680, 321, 2,202, and STEM 3,369). We score the 66 configs, which is also what TAIDE reports ("共66個子類別").
- **Categories** come from lm-evaluation-harness's `lm_eval/tasks/tmmluplus/subject.tsv` (v0.4.3, MIT), which maps each subject to a field, and the field-to-category table its `_generate_configs.py` copies from `iKala/ievals` (`ievals/settings.py`). That mapping reproduces the card's v1.1 counts for every category. The three Taiwan subjects are all Social Sciences.
- **Twelve subjects have fewer than five dev rows** after v1.1's corrections: `organic_chemistry` has 3; `agriculture`, `official_document_management`, `pharmacy`, `insurance_studies`, `pharmacology`, `trust_practice`, `trade`, `junior_math_exam`, `junior_social_studies`, `tve_mathematics` and `education` have 4.
- **Subjects are small.** The test splits hold 85 to 989 rows. The Taiwan subjects hold 754 (`geography_of_taiwan`), 112 (`ttqav2`) and 128 (`taiwanese_hokkien`), so the last two have a standard error of about 4.5 points near 50% and are reported, not used alone for a decision.
- **Quirks kept as they are:** 383 test rows repeat an earlier row's question text within their subject (lm-evaluation-harness and the card's leaderboard keep them too), and 74 choices have leading or trailing whitespace, which [005.D1](#005d1-chinese-prefixes-and-no-space-before-the-answer) strips.

---

## 3. Design decisions

### 005.D1. Chinese prefixes, and no space before the answer

A TMMLU+ request is the subject header, the few-shot examples, then:

```text
問題：<question>
答案：<answer>
```

The continuation is the answer text with no leading space, and the unconditional context for PMI is `答案：`. The header is the TMMLU+ paper's (Tam et al. 2024, figs. 8 and 9, p. 19 and 20), with the subject's Chinese name from lm-evaluation-harness's `subject.tsv`:

```text
以下是關於<科目>考試單選題，請選出正確的答案。

```

followed by a blank line, as OLMES's MMLU header ("The following are multiple choice questions (with answers) about <subject>.\n\n", `oe_eval/tasks/oe_eval_tasks/mmlu.py`). Examples are separated by a blank line, as every OLMES task. The question and each answer are stripped of outer whitespace.

No reference implementation scores Chinese as cloze, so this is our choice. It rests on three things.

1. **The Chinese benchmarks we checked all write their prompts this way.** The TMMLU+ paper's own prompts use `問題：` and `答案：` with nothing after the colon, following C-Eval (Huang et al. 2023), and AGIEval's Chinese template is `问题：...\n答案：` (`AGIEval/src/dataset_loader.py`, vendored in OLMES). lm-evaluation-harness's `cmmlu` and `ceval` end with `答案：`. Its `tmmluplus` keeps the English `Answer:`, but scores letters, not answer texts. Chinese text has no spaces between words, so a space after `答案：` is something a model almost never sees there.
2. **The leading space changes how the first character is tokenized.** On all 78,584 test choices, with the tokenizers of `allenai/OLMo-2-0425-1B` (`a1847dff`) and `Qwen/Qwen2.5-0.5B` (`060db649`):

   | | OLMo 2 | Qwen 2.5 |
   | --- | --- | --- |
   | Tokens per choice after `答案：`, no space | 16.5 | 9.8 |
   | Tokens per choice after `答案：`, with a space | 17.2 | 11.1 |
   | Choices where the space is a token of its own | 23.8% | 23.8% |
   | Context ids unchanged in the joint tokenization, `答案：`, no space | 100% | 100% |
   | Context ids unchanged in the joint tokenization, `Answer:`, no space | 94.3% | 94.3% |

   In the other 76% of choices the space merges with the start of the first character, so a word the tokenizer knows becomes byte pieces: Qwen reads `臺灣` as one token, and ` 臺灣` as three. The model is then scored on a spelling of the answer it never sees in Chinese text, and the cost differs by choice. With `答案：` and no space the context/continuation boundary is clean for every choice, while English `Answer:` with no space would merge across the boundary for 5.7% of choices, which 001's encoding (as lm-evaluation-harness's `_encode_pair`) then attributes to the wrong side.
3. **`acc_per_char` then counts only the answer.** OLMES's leading space adds one character to every English choice (OLMES sec. 3.5), which barely matters at English lengths. TMMLU+ choices are short (median 9 characters, 9% at most 2), so one extra character with a choice-dependent log-probability would move the normalization noticeably. Without it, our `acc_per_char` is exactly lm-evaluation-harness's `acc_norm` (log-likelihood over `len(choice)`), which the parity check in [005.D6](#005d6-parity-against-lm-evaluation-harness-rendering-our-format) relies on. `bpb` is unaffected in kind: it still divides by the UTF-8 bytes of the scored text, 3 per CJK character.

**Code.** `cloze` gains two keyword arguments, `answer_prefix` (default `"Answer:"`) and `separator` (default `" "`), so every English task renders as before and the Chinese format is `cloze(question, answers, question_prefix="問題：", answer_prefix="答案：", separator="")`. The two Chinese prefixes are module constants that later Chinese tasks import, so the format is defined once.

### 005.D2. 66 subject tasks, and the categories and total as suites

Each subject is a `MultipleChoiceTask` named `tmmluplus_<subject>` (lm-evaluation-harness's names), generated from one table of `(subject, Chinese name, category, dev rows)` in the task module. Nothing else differs between subjects. The aggregates are 003's `Suite`s, whose score is the unweighted mean of their members, so the card's method needs no new code:

| Suite | Members | Score |
| --- | --- | --- |
| `tmmluplus:stem`, `:social_sciences`, `:humanities`, `:other` | that category's subject tasks | mean `acc_per_char` |
| `tmmluplus` | the four category suites | mean of the category means, as the card |
| `tmmluplus:taiwan` | the three Taiwan subject tasks | mean `acc_per_char`; each subject's own score is in `tasks` |
| the same five with a `:bpb` suffix | the same tasks, `metric="bpb"` | mean `bpb` |
| `tmmluplus:all` (unscored) | `tmmluplus`, `tmmluplus:bpb`, `tmmluplus:taiwan`, `tmmluplus:taiwan:bpb` | none: groups the runs, as `base_easy` |

`tmmluplus:taiwan` is the `tmmluplus_taiwan` task named earlier, spelled as 003's suites are. Every task is scored once however many suites read it, and below about 300M parameters `bpb` is the metric to decide on, since accuracy sits near chance there (OLMo 3, sec. 3.3.2).

### 005.D3. Docs are identified by their row index

TMMLU+ rows have no id column, and `Fewshot` and the predictions file need one. As OLMES does for MMLU (`native_id_field: "index"`, docs mapped `with_indices=True` in `oe_eval/tasks/oe_eval_tasks/mmlu.py`), a doc's id is its row's position in its split. **Core change:** `Dataset` gains `row_index: bool = False`; when set, `load` adds each row's position as `"index"` and raises if the dataset already has that column, and `to_doc` reads `str(row["index"])`. MMLU and MedQA need the same thing in 003's later PRs, so whichever lands first adds it and the other uses it. Passing the index to `to_doc` as a second argument was the alternative; it changes the signature of every task for the sake of a few.

### 005.D4. Few-shot examples: each subject's dev rows, in file order

Each subject shows its `train` split (the `*_dev.csv` file) in file order, as MMLU's first 5 `dev` rows in OLMES and lm-evaluation-harness's `first_n` sampler: `Fewshot(split="train", ids=("0", ..., str(k - 1)))`. Normally `k` is 5. The twelve subjects with fewer dev rows show all of them (3 or 4), and the subject table records `k` for each, so the shortfall is visible in the definition and frozen with it. We don't top up from `validation`: no prior work does, and it would make the shots differ from the reference in [005.D6](#005d6-parity-against-lm-evaluation-harness-rendering-our-format). The dev rows are a different split from the scored one, so no doc ever sees itself.

### 005.D5. Pin and scope

`ikala/tmmluplus` at `94d86f1d013d59f389b710a902af30b7a0aa8c8f`, the `test` split of the 66 configs, 19,646 docs. `linear_algebra` is left out (section 2). `validation` is not used.

### 005.D6. Parity against lm-evaluation-harness, rendering our format

**No OLMES reference exists.** OLMES at `5a51f502` has no TMMLU+ task and registers no Chinese task at all: its only Chinese text is AGIEval's `gaokao-*` few-shot examples, and `create_core_agi_eval_tasks` builds only the English AGIEval sets. lm-evaluation-harness's `tmmluplus` (v0.4.3, the version OLMES pins) can't be the reference as it stands. It scores the letters `A` to `D` after `Answer:`, reads `ZoneTwelve/tmmluplus` (an unpinned copy), and its `first_n` sampler asserts at 5-shot on the twelve short subjects above.

**The reference** is lm-evaluation-harness v0.4.3, from the same locked environment that runs OLMES (never installed in or imported by flock), with a task config per subject generated from our subject table. It renders exactly our format: the same dataset revision and split, our header as `description`, `問題：{{question}}\n答案：` as `doc_to_text`, the four stripped answer texts as `doc_to_choice`, `target_delimiter: ""`, `fewshot_split: train` with `first_n` and `num_fewshot` set to the subject's `k`. This checks the implementation on Chinese text (rendering, the tokenization boundary, scoring, `acc_per_char` against `acc_norm`, and `bpb` computed from its logged log-likelihoods and UTF-8 byte counts) against an independent one. It can't check that the format is the right one, which is [005.D1](#005d1-chinese-prefixes-and-no-space-before-the-answer)'s job.

**The rule, fixed before any run.** The task is at parity when checks 1 and 2 pass, with 003's D22 tolerances:
1. **Same requests.** lm-evaluation-harness's logged `arguments` (`--log_samples`) equal our conditional requests, string for string, in doc order.
2. **Float32.** Every request within 1e-3 nats, the predicted choice agrees on at least 99.9% of docs, accuracy within 0.1 points, `bpb` within 1e-4.
3. **Bfloat16, reported.** Accuracy within 0.2 points, `bpb` within 3e-4 and choice agreement on at least 99% of docs are each reported as pass or fail, next to both sides' drift from lm-evaluation-harness's float32 run. They don't decide parity. lm-evaluation-harness sums each request's bfloat16 log-probabilities in bfloat16 (`models/huggingface.py`, v0.4.3), as OLMES does, while we sum in float32, so its bfloat16 run is a rounded number and the shared float32 answer is the reference. Our own bfloat16 drift from it is the task's noise floor: a difference between two checkpoints smaller than that is not a result.

Accuracy means `acc_raw` (its `acc`) and `acc_per_char` (its `acc_norm`). Checks 2 and 3 are taken on `tmmluplus`, its four categories and `tmmluplus:taiwan`; single subjects (85 to 989 docs) are reported only, as D22 does for MMLU. `acc_uncond` isn't compared, since lm-evaluation-harness has no PMI. The model is `Qwen/Qwen2.5-0.5B` at `060db6499f32faf8b98477b0a26969ef7d8b9987`, which reads Chinese and has no BOS token, in float32 and bfloat16, with lm-evaluation-harness at its default batch size 1 so the bfloat16 gap measures only our batching. For rankings (D23), both sides also score `allenai/OLMo-2-0425-1B` at `a1847dff35000b4271fa70afc5db10fd29fedbdf` in bfloat16, and the sign of every judged difference between the two models must agree whenever the reference's difference exceeds the tolerance.

---

## 4. Plan

One PR, stacked on 003's suites PR and rebased when it merges:

1. **Core:** `cloze`'s `answer_prefix` and `separator` ([005.D1](#005d1-chinese-prefixes-and-no-space-before-the-answer)) and `Dataset.row_index` ([005.D3](#005d3-docs-are-identified-by-their-row-index)), unless 003's MMLU PR has added it first.
2. **Task:** `flock/eval/tasks/tmmluplus.py` with the subject table, the generated tasks, the suites ([005.D2](#005d2-66-subject-tasks-and-the-categories-and-total-as-suites)), and their registration; a docs page entry.
3. **Smoke** on `allenai/OLMo-2-0425-1B` and `Qwen/Qwen2.5-0.5B` (pins above) on one H200: 19,646 docs, and Qwen above chance (25%).
4. **Parity** ([005.D6](#005d6-parity-against-lm-evaluation-harness-rendering-our-format)): six H200 jobs (Qwen in both dtypes and OLMo 2 in bfloat16, each on both sides), and the PR reports the table.

---

## 5. Tests

On CPU with tiny random models, as every eval test:
- **Rendering:** a fixture row renders to the exact header, question, answer and few-shot strings above, with no space before the answer and stripped choices.
- **Chinese lengths:** `acc_per_char` divides by characters and `bpb` by UTF-8 bytes (3 per CJK character) on a multi-byte answer; an English `cloze` call renders as before.
- **Row ids:** `row_index` numbers rows from 0 per split, and raises on a dataset that already has `index`.
- **Short dev splits:** a subject with 3 dev rows shows 3 examples.
- **Suites:** the total is the mean of the four category means, not of the 66 subjects, on hand values; the table has 66 distinct subjects in 4 categories.

---

## 6. Open questions

- Whether a top-up from `validation` should give the twelve short subjects five shots after all, as a separately named task if ever.
- Whether `ttqav2` and `taiwanese_hokkien`, at about 120 docs each, earn their own lines in reports or only count inside `tmmluplus:taiwan`.

---

## 7. How it was actually built

Not built yet.
