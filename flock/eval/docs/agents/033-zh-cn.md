# 033: `zh_cn`, native Simplified Chinese next to `zh_tw`

| | |
| --- | --- |
| Status | design, in review |
| Date | 2026-11-09 |
| Builds on | [005](005-tmmluplus.md) (the Chinese cloze format, row ids, few-shot rule and parity method), [017](017-zh-tw-suite.md) (one member per benchmark) |
| Keeps | every existing task, request, task hash, suite and score; `zh_tw` and `zh_tw:bpb` unchanged |
| Result | (design) 119 subject tasks from C-Eval and CMMLU, their suites, and `zh_cn` and `zh_cn:bpb` |

The file number and the decision numbers are provisional. The file takes the next free number when its PR is opened, and decisions `033.D1` to `033.D7` take the next free global numbers after every stage merged before it.

## 1. Goal

`zh_tw` and `zh_tw:bpb` score Traditional Chinese. Most baselines we compare against (Qwen above all) are trained mostly on Simplified Chinese, so a Traditional-only comparison can't tell a stronger model from one that is stronger only in the script it was trained on. This stage adds the Simplified side: native Simplified Chinese exam sets, scored with the same format, metrics and few-shot rule as `zh_tw`, so every model gets one number in each script from one instrument.

1. **C-Eval and CMMLU as data.** All subjects of each, in 005's cloze format with Simplified prefixes, `acc_per_char` as the primary metric and gold-answer `bpb` beside it, 5-shot from each subject's `dev` rows.
2. **Each benchmark's own aggregate**, and the categories it publishes, as suites.
3. **`zh_cn` and `zh_cn:bpb`**, one member per benchmark, as `zh_tw` (017).

**Out of scope:** converting any set between scripts (a converted set is never scored under a native name); translated sets such as MMMLU; generative, chain-of-thought or letter (`MC`) scoring; changing `zh_tw`; any rule that says one model beats another.

---

## 2. What the baselines report in Chinese

What each baseline's report or model card says about Chinese, for the models we score. The Qwen3 predecessor row reports base-model results. Other published figures are from post-trained models, with generation conditions stated where available; Gemma establishes thinking mode but not sampling settings. None is for the base models we score in our format. They are context for a reader, not a target for our numbers.

| Model we score | Chinese set reported | Shots, metric, setting | Published number | Source |
| --- | --- | --- | --- | --- |
| Qwen3.5 0.8B and 4B Base | none on the base cards | | | `Qwen/Qwen3.5-0.8B-Base` and `-4B-Base` cards (`dc7cdfe2`, `1001bb4d`): no benchmark table |
| Qwen3.5 0.8B (post-trained) | C-Eval | shots not stated; "top_p=0.95, top_k=20, presence_penalty=1.5, and temperature=1.0" | 46.4 non-thinking, 50.5 thinking | `Qwen/Qwen3.5-0.8B` card at `2fc06364`, Benchmark Results, Language |
| Qwen3.5 0.8B (post-trained) | MMMLU, INCLUDE (multilingual, Chinese one language of many) | as above, thinking | MMMLU 44.3 (34.1 non-thinking), INCLUDE 40.6 | same card |
| Qwen3.5 4B (post-trained) | C-Eval | shots and mode not stated in the table; the card says the model thinks by default | 85.1 | `Qwen/Qwen3.5-4B` card at `851bf6e8`, Benchmark Results, Language |
| Qwen3.5 4B (post-trained) | MMMLU, INCLUDE | as above | 76.1, 71.0 | same card |
| Qwen3.5 family | C-Eval, post-trained Plus and Omni models only | | 92.3 (Plus), 86.0 (Omni-Flash), 92.0 (Omni-Plus) | Qwen3.5-Omni report (2604.15804), p.10 Table 4 |
| Qwen3 base models (predecessor, for the protocol) | MMMLU and INCLUDE, no Chinese-only set | "MMMLU (OpenAI, 2024) (5-shot), INCLUDE (Romanou et al., 2024) (5-shot)" | Qwen3-0.6B Base: MMMLU 50.16, INCLUDE 34.26 | Qwen3 report (2505.09388), p.4 to 5 sec. 3.3 and p.8; C-Eval appears only in its post-training evaluation (p.13) |
| Llama 3.2 1B | no Chinese-specific result | the multilingual table covers Portuguese, Spanish, Italian, German, French, Hindi and Thai; Chinese is not among the supported "English, German, French, Italian, Portuguese, Hindi, Spanish, and Thai" | | `meta-llama/Llama-3.2-1B` card at `4e20de36` |
| Gemma 4 E2B | MMMLU only (instruction-tuned, thinking mode) | "All models are in thinking mode unless explicitly stated" | 67.4 | Gemma 4 report (2607.02770), p.5 Table 5; same row on the `google/gemma-4-E2B` card at `d29ff6b4` |
| OLMo 2 1B | none | | | `allenai/OLMo-2-0425-1B` card at `a1847dff` |

Llama's card also reports an instruction-tuned MGSM aggregate without its language composition; it is not a Chinese-specific score.

So C-Eval is the one Chinese-only set a baseline reports, and only Qwen reports it. CMMLU is in no baseline's report for these models; it is the other widely used native Simplified exam set, so it joins as the second member so that `zh_cn` doesn't rest on one benchmark, as `zh_tw` rests on two.

---

## 3. The datasets, read at the pinned revisions

| | C-Eval | CMMLU |
| --- | --- | --- |
| Source | `ceval/ceval-exam` at `617524a00b307ff6f9933702f724131fe12ca7ce` | `haonan-li/cmmlu` (now redirects to `lmlmcat/cmmlu`) at `efcc940752ea4a1ea94d2727f11f83858d64fc8e`, file `cmmlu_v1_0_1.zip` (SHA-256 `22ecf70b...c672a6`) |
| Paper | Huang et al. 2023, arXiv 2305.08322 | Li et al. 2023, arXiv 2306.09212 |
| Licence | CC-BY-NC-SA-4.0 | card front matter CC-BY-NC-4.0, card text CC-BY-NC-SA-4.0; we follow the stricter |
| Subjects | 52 | 67 |
| Scored split | `test`, 12,342 docs (108 to 497 per subject) | `test`, 11,582 docs (105 to 411) |
| Shots | `dev`, 5 per subject, 260 | `dev`, 5 per subject, 335 |
| Categories | STEM 20, Humanities 11, Social Science 10, Other 11 (`subject_mapping.json`, `hkust-nlp/ceval` at `cba65ae9`) | STEM 17, Humanities 13, Social Science 22, Other 15; 15 also tagged China specific (`src/categories.py`, `haonan-li/CMMLU` at `d6e7b716`) |

- **C-Eval's test labels are public now.** The paper withheld them for its leaderboard; the dataset card says "[2025.7.27] We have released the complete C-Eval test set to the community!". At the pin all 12,342 test rows carry an answer letter; a spot check at `b4b16302` (March 2025) found no labels in the 171 `computer_network` test rows. Other subjects at that old pin were not checked. lm-evaluation-harness v0.4.3's `ceval-valid` scores the 1,346-doc `val` split because it predates the release. We score `test`, nine times as many docs, which is the split C-Eval's paper and leaderboard report (p.5: "obtain the test accuracy, where a public leaderboard is maintained").
- **CMMLU is a loading-script dataset.** Its repository holds `cmmlu.py` and a zip of CSV files, and `datasets` 4 refuses dataset scripts, so it can't be read as TMMLU+ is. [033.D5](#033d5-cmmlu-is-read-from-a-private-parquet-mirror) mirrors it.
- **CMMLU's counts differ from its paper.** The paper (p.4) says "CMMLU contains 11,528 questions across 67 subjects"; v1.0.1 holds 11,582 test and 335 dev rows. It also says 16 tasks are China specific; its own mapping file tags 15. We follow the files.
- **Quirks kept as they are:** 153 C-Eval and 132 CMMLU test questions repeat an earlier question within their subject; 6 C-Eval and 343 CMMLU choices have outer whitespace, which 005.D1 strips. No choice is empty after stripping. One test doc per set repeats a dev row's question and gold answer (C-Eval `college_chemistry` 196, CMMLU `college_law` 99), which 005.D7 already handles.

---

## 4. Design decisions

### 033.D1. 005's format with Simplified prefixes

A request is the subject header, the five examples, then:

```text
问题：<question>
答案：<answer>
```

with no space before the answer and PMI's unconditional context `答案：`, exactly 005.D1 with `問題：` written in Simplified. `答案` is the same in both scripts. AGIEval's Chinese template is this prefix pair (`"问题：" + question + "\n" "答案："`, vendored in OLMES), and the TMMLU+ paper follows C-Eval's prompt with `答案：`, so the format traces to C-Eval's own. C-Eval writes no question prefix and CMMLU writes `题目：` and `答案是：`; using either would make `zh_cn` a different format from `zh_tw` (one instrument). `问题：` is two tokens (`问题`, `：`) in all five tokenizers we score, and `答案：` is two in four of them (OLMo 2 splits `答` into bytes).

The code adds `SIMPLIFIED_QUESTION_PREFIX = "问题："` beside 005's `CHINESE_QUESTION_PREFIX`; the answer prefix is 005's constant.

**The space check, rerun on these sets** (011's method: each stripped choice tokenized after `答案：` alone and joined, over all 49,368 C-Eval and 46,328 CMMLU test choices):

| Tokenizer | Tokens per choice, no space / with space (C-Eval, CMMLU) | Space a token of its own (C-Eval, CMMLU) | Context ids unchanged, `答案：` no space (C-Eval, CMMLU) |
| --- | --- | --- | --- |
| OLMo 2 1B | 13.1 / 13.8, 10.6 / 11.4 | 28.6%, 23.4% | 99.56%, 99.16% |
| Qwen2.5 0.5B | 7.7 / 9.1, 5.8 / 7.3 | 28.6%, 23.4% | 99.56%, 99.16% |
| Llama 3.2 1B | 9.5 / 10.1, 7.5 / 8.1 | 18.7%, 12.8% | 99.56%, 99.16% |
| Qwen3.5 0.8B | 7.0 / 7.9, 5.3 / 6.3 | 87.7%, 90.3% | 99.56%, 99.16% |
| Gemma 4 E2B | 8.0 / 8.6, 6.2 / 6.8 | 46.5%, 49.1% | 98.21%, 97.40% |

As on TMMLU+, a space adds tokens to every choice and is often a token of its own, so no space stays. Unlike TMMLU+ (100% under OLMo 2 and Qwen2.5), the boundary isn't clean for every choice. Under the first four tokenizers 610 choices merge with the colon, 600 of them opening with `“` (`：“` is one token); under Gemma 4, 2,091 do, adding 1,449 that open with `《`, mostly law titles such as `《中华人民共和国个人所得税法》`, and 33 with `（`. For those, 001's encoding (as lm-evaluation-harness's `_encode_pair`) takes the continuation as the joint ids after the context's length, so the opening quote falls on the context's side and isn't scored, while `acc_per_char` and `bpb` still count its character and bytes.

We keep the format and report the rate. The reference encodes the same way, so parity is unaffected; changing the encoding would change how every task handles such a merge and is a core stage of its own; and editing the choices would no longer score the set as published. The `results.json` of a run can't show the rate, so the docs page states it per tokenizer. The `SentencePieceTokenizer` path refuses such a merge outright (`model.py`), so the build first runs this check on that tokenizer.

### 033.D2. Each benchmark's own header

The header is the one each benchmark's paper prompts with, with the subject's Chinese name, followed by a blank line, as 005.D1:

- C-Eval: `以下是中国关于<科目>的单项选择题，请选出其中的正确答案。` (Huang et al. 2023, fig. 6, p.16; lm-evaluation-harness's `ceval` `description`).
- CMMLU: `以下是关于<科目>的单项选择题，请直接给出正确答案的选项。` (Li et al. 2023, p.5: "We introduce each question with the phrase “以下是关于[主题]的单项选择题，请直接给出正确答案的选项”"; lm-evaluation-harness's `cmmlu` `description`).

The Chinese subject names come from lm-evaluation-harness v0.4.3's per-subject configs (C-Eval's also equal `subject_mapping.json`'s second field). Both headers ask for an answer while we score the answer's text, as TMMLU+'s header does under 005.D1.

### 033.D3. 119 subject tasks; each benchmark's aggregate is the mean over its subjects

Each subject is a `MultipleChoiceTask` named `ceval_<subject>` or `cmmlu_<subject>` (the datasets' config and file names), generated from one table per benchmark of `(subject, Chinese name, category)`. Unlike TMMLU+, whose card averages category means (005.D2), both papers average subjects: C-Eval's "“Average” column indicates the average accuracy over all the subjects" (Table 3 caption, p.6) and CMMLU's "“Overall” = macro average score over all subjects" (Table 1 caption, p.5). Each suite follows its own benchmark.

| Suite | Members | Score |
| --- | --- | --- |
| `ceval` | the 52 subject tasks | mean `acc_per_char` |
| `ceval:stem`, `:social_science`, `:humanities`, `:other` | that category's subjects | mean `acc_per_char` |
| `cmmlu` | the 67 subject tasks | mean `acc_per_char` |
| `cmmlu:stem`, `:social_science`, `:humanities`, `:other` | that category's subjects | mean `acc_per_char` |
| `cmmlu:china_specific` | the 15 tagged subjects (also in their category) | mean `acc_per_char` |
| the same with a `:bpb` suffix | the same tasks, `metric="bpb"` | mean `bpb` |

`ceval` and `cmmlu` are suites of tasks, not of their category suites, so a category's size weighs as the papers weigh it.

### 033.D4. Five examples from `dev`, in file order, with 005.D7

Each subject shows its five `dev` rows in file order (`first_n`, as lm-evaluation-harness's `ceval` and `cmmlu` and as 005.D4), answer only: C-Eval's `explanation` field is for chain of thought and isn't shown. Every subject of both sets has exactly five `dev` rows, so `k` is 5 throughout. 005.D7 applies unchanged and drops one example for each of the two repeats in section 3. CMMLU's paper drops "the longest examples" when a context is too long; the original approximate survey does not establish maximum actual request length or absence of truncation. Exact verification must include the real headers, all choices, 005.D7 and configured BOS.

### 033.D5. CMMLU is read from a private parquet mirror

The zip's CSV files are converted to parquet, one config per subject with `test` and `dev` splits, every row and column kept in order, and uploaded with the original card and licence to a private `cerulean-works` repository on the Hugging Face Hub, pinned by revision, as DRCD's mirror (010). The repository is private before its first write: the text is under a non-commercial licence, and making it public is a separate decision. The conversion script and the zip's SHA-256 are recorded with the mirror, and a check reads both and compares every cell. C-Eval is read from its own repository.

### 033.D6. Docs are identified by their row index

CMMLU's CSV files have an unnamed index column and C-Eval has an `id` column; both number rows from 0 within each split, but neither name is one `Dataset` reads. As TMMLU+ (005.D3), `Dataset(row_index=True)` gives every doc its position in its split, so both sets work the same way and no new field is needed. The build asserts that C-Eval's `id` equals the row index.

### 033.D7. `zh_cn` and `zh_cn:bpb`, one member per benchmark

| Suite | Members | Metric |
| --- | --- | --- |
| `zh_cn` | `ceval`, `cmmlu` | each member suite's score (`acc_per_char` means) |
| `zh_cn:bpb` | `ceval:bpb`, `cmmlu:bpb` | `bpb` |

As 017.D2: the benchmarks' scored suites, weighed equally, so neither set's subject count decides the mix. `zh_cn` reads public data, but CMMLU's mirror is private, so it runs with access to the Cerulean organization, as `zh_tw` does for the exam set. A model's `zh_tw` and `zh_cn` are then read side by side; how a milestone uses them is decided outside this stage.

---

## 5. Parity

As 005.D6, with no change. No OLMES reference exists for either set (OLMES registers no Chinese task). lm-evaluation-harness's own `ceval` and `cmmlu` score letters after `A. ... D.` lines, and `ceval-valid` scores `val`, so neither is the reference as it stands. The reference is lm-evaluation-harness v0.4.3 from the locked OLMES environment with one generated config per subject that renders exactly our format: the same revision and split (CMMLU through the mirror), our header as `description` (with the extra `\n` its Jinja rendering needs, as in 005), `问题：{{question.strip()}}\n答案：` as `doc_to_text`, the four stripped choices as `doc_to_choice`, `target_delimiter: ""`, `fewshot_split: dev` with `first_n` and 5 shots.

**The rule, fixed before any run** (005.D6 and its correction in 005 section 7):

1. **Same requests.** The reference's logged `arguments` equal our conditional requests, string for string, in doc order, on every doc except the two 005.D7 repeats, which are named.
2. **FP32 decides.** On `Qwen/Qwen2.5-0.5B` at `060db6499f32faf8b98477b0a26969ef7d8b9987`: every request within 1e-3 nats, the predicted choice agreeing on at least 99.9% of docs, `acc_raw` and `acc_per_char` within 0.1 points and `bpb` within 1e-4, on `ceval`, `cmmlu`, their categories, `cmmlu:china_specific`, `zh_cn` and `zh_cn:bpb`. Single subjects are reported only.
3. **BF16 is reported**, with the same bounds and both sides' drift from the reference's FP32 run, and doesn't decide parity.

A miss is handled as earlier stages handled theirs: a request over 1e-3 nats is scored alone to separate our batching from a real difference before anything is ruled.

---

## 6. Plan

1. **Mirror** CMMLU (033.D5), private, and pin its revision.
2. **Core:** `SIMPLIFIED_QUESTION_PREFIX` beside 005's constants. No other core change.
3. **Tasks:** `tasks/ceval.py` and `tasks/cmmlu.py` with their subject tables and suites, `tasks/zh_cn.py` with the two suites, and their registration.
4. **Snapshot:** `tasks.json` gains 119 tasks and the new suites with no existing entry changed; a toy task pins the Simplified prefix's requests.
5. **Docs:** a section on the tasks page with both sets, the suites, the licence and the boundary rate of 033.D1.
6. **Parity** (section 5), then the baseline models scored on `zh_cn` and `zh_cn:bpb` in FP32 at the batch budget their other suites used.

---

## 7. Tests

On CPU with tiny random models:
- **Rendering:** a fixture row of each set renders to the exact header, `问题：`, `答案：` and few-shot strings above, with stripped choices and no space before the answer.
- **Few-shot:** five `dev` examples in file order; a test doc equal to a dev row's question and gold answer sees four.
- **Suites:** `ceval` and `cmmlu` are means over subjects, not over categories, and `zh_cn` weighs the two benchmarks equally, on hand values.
- **Tables:** 52 and 67 distinct subjects in four categories each, and 15 China specific subjects, all inside CMMLU's table.
- **Snapshot:** the new tasks and suites are pinned, and no existing task hash or request changes.

---

## 8. Open questions

- Whether the boundary merge of 033.D1 also occurs on TMMLU+ under the Llama 3.2, Qwen3.5 and Gemma 4 tokenizers, which 005 checked only under OLMo 2 and Qwen2.5.
- Whether a letter-scored (`MC`) variant, which is how both papers and the baselines score these sets, is worth a separately named task once models score above chance on the cloze one.
