# 003: OLMo 3 Base Easy, and parity with OLMES

| | |
| --- | --- |
| Status | PR 1 (core) built; PRs 2 to 6 to follow |
| Date | 2026-10-22 (design), 2026-10-23 (PR 1 and the calibration) |
| Builds on | [001](001-eval-core.md) (scoring core, tasks as data, ARC-Easy) |
| Keeps | 001's scoring, batching, metrics and results file; changes how a task describes its requests |
| Result | `Choice`, `cloze`, the new `MCDoc`, `description`, `metrics`, `Fewshot.rows` and `num_shots`; ARC-Easy rewritten with identical requests; the bfloat16 tolerance calibrated and fixed (D22) |

## 1. Goal

1. **Base Easy in English.** Every task in OLMo 3's Base Easy suite (arXiv 2512.13961, sec. 3.3.2 and App. A.4.1), defined the way OLMES defines it at a pinned commit, each as a short data definition.
2. **Parity.** Our numbers on `allenai/OLMo-2-0425-1B` match OLMES's within a tolerance fixed before the comparison runs, so a Base Easy number can feed decisions. A task that fails parity is debugged or explained before it is used.
3. **A suite.** `flock-eval run --suite base-easy` scores the whole suite and reports OLMo 3's four aggregates.

**Out of scope:** Base Main (generative tasks with pass@k), the held-out set, Traditional Chinese tasks, and any change to how requests are batched or scored in bfloat16.

---

## 2. What Base Easy is, read from OLMES

Read from allenai/olmes at `5a51f502d463b8cdc4a2dcad7d7096c41ff1197e` (2026-03-24, the head of `main` on 2026-10-22), in `oe_eval/configs/task_suites.py:931-1006`. Base Easy is four suites. Each suite's score is the unweighted mean of its members' primary scores (`oe_eval/tasks/aggregate_tasks.py:147-149`), and a member that is itself a suite (MMLU's 57 subjects) is averaged first.

| Suite | Members | Score per member |
| --- | --- | --- |
| `qa_rc` | 21 QA tasks | the task's accuracy metric |
| `qa_bpb` | the same 21 tasks, same requests | `bpb` of the gold answer |
| `math_bpb` | 7 Minerva MATH subjects | `bpb` of the gold solution |
| `code_bpb` | HumanEval, MBPP, multilingual MBPP (17 languages, averaged first) | `bpb` of the gold code |

A `:bpb` twin in OLMES is a copy of the task with `primary_metric` set to `bits_per_byte_corr` (`oe_eval/configs/tasks.py:7830-7853`). Its requests are the same, and `bits_per_byte_corr` is the gold choice's `-logprob / bytes * log2(e)` (`oe_eval/metrics/metric.py:235-240, 286`), which is 001's `bpb`. So `qa_rc` and `qa_bpb` read two metrics off one scored task, as 001's D6 already does for ARC-Easy.

### 2.1 The tasks

"Prompt" is what precedes the scored text. "Shots" says where the few-shot examples come from. In the last column, **data** means the task fits 001's `MultipleChoiceTask` as it stands, and the other entries name the core change of [D16](#d16-a-doc-lists-its-choices-as-context-and-continuation-pairs) to [D20](#d20-a-greedy-match-metric-for-lambada) it needs.

| Task | Dataset, split | Prompt | Shots | Primary | Fits |
| --- | --- | --- | --- | --- | --- |
| `arc_easy` | `allenai/ai2_arc` ARC-Easy, test | `Question: q\nAnswer:` | 5 curated (train ids) | `acc_per_char` | data (001) |
| `arc_challenge` | ARC-Challenge, test | same | 5 curated (train ids) | `acc_uncond` | data |
| `mmlu_<subject>` x57 | `cais/mmlu`, test | subject header, then `Question: q\nAnswer:` | first 5 rows of `dev` | `acc_per_char` | D16 header, D18 suite |
| `csqa` | `tau/commonsense_qa`, validation | `Question: q\nAnswer:` | 5 curated | `acc_uncond` | data |
| `hellaswag` | `allenai/hellaswag`, validation | `<activity>: <ctx>` with no prefixes | 5 curated | `acc_per_char` | D16 |
| `winogrande` | `allenai/winogrande` xl, validation | sentence up to the blank, with the option; one shared continuation | 5 curated | `acc_raw`, no PMI | D16 |
| `socialiqa` | `allenai/social_i_qa`, validation | `Question: context question\nAnswer:` | 5 curated | `acc_per_char` | data (D17 ids) |
| `piqa` | `ybisk/piqa`, validation | `Goal: g\nAnswer:` | 5 curated | `acc_per_char` | data (D17 ids) |
| `coqa`, `drop`, `jeopardy`, `naturalqs`, `squad` | `allenai/<name>-gen2mc` (`nq-gen2mc` for Natural Questions), one split each | passage, title or category lines, then the question | 0 (CoQA) or 5 literal examples | `acc_per_char` | D16, D17 |
| `sciq` | `allenai/sciq`, validation | `Question: q\nAnswer:` | `Random(1234).sample(train, 5)` | `acc_per_char` | data (D17 ids) |
| `qasper_yesno`, `sciriff_yesno` | `allenai/<name>`, train | evidence line, then `Question: q\nAnswer:`, stripped | sampled from the scored split | `acc_raw` | D16, D17 |
| `basic_skills_<skill>` x6 | `allenai/basic-skills`, validation | the raw question | sampled from the scored split, minus the doc | `acc_per_token` | D16, D17, D18 |
| `lab_bench_dbqa`, `lab_bench_protocolqa` | `futurehouse/lab-bench`, train | `Question: q\nAnswer:` | sampled from the scored split | `acc_per_char` | data (D17) |
| `lambada` | `EleutherAI/lambada_openai`, test | the passage minus its last word | 0 | greedy match of the last word | D16, D20 |
| `medmcqa` | `openlifescienceai/medmcqa`, validation | `Question: q\nAnswer:` | sampled from train | `acc_per_char` | data (D17 ids) |
| `medqa_en` | `davidheineman/medqa-en`, test | `Question: q\nAnswer:` | sampled from train | `acc_per_char`, no PMI | data (row-index ids) |
| `minerva_math_<subject>` x7 | `EleutherAI/hendrycks_math`, test | `Problem:\nq\n\nSolution:` | 4 literal (`Minerva:MATH:fixed`) | `bpb` of the solution | D16, D17, D19 |
| `humaneval` | `openai/openai_humaneval`, test | the function signature and docstring | sampled from the scored split, minus the doc | `bpb` of the solution | D16, D17, D19 |
| `mbpp` | `google-research-datasets/mbpp`, test | the problem, then an open code fence | `Random(1234)` shuffle of `prompt`, first 3 | `bpb` of the code | D16, D17, D19 |
| `mt_mbpp_<language>` x17 | `allenai/multilingual_mbpp`, test | same, with the language's fence | same | `bpb` of the code | D16, D17, D18, D19 |

Only `arc_easy` exists today. Doc counts are recorded when each task lands, from the pinned revision.

### 2.2 Dataset revisions

OLMES pins no dataset revision: every task loads the Hub's head (`oe_eval/tasks/base_task.py:172-180`). We pin the head commit of each dataset as of 2026-10-22 (the newest commit before 2026-10-23 00:00 UTC, which was also the head on 2026-10-23), and the parity run passes the same revision to OLMES through its per-task `revision` field, so both sides read the same rows.

| Dataset | Revision | Commit date | Read as |
| --- | --- | --- | --- |
| `allenai/ai2_arc` | `210d026faf99` | 2023-12-21 | files |
| `cais/mmlu` | `c30699e8356d` | 2024-03-08 | files |
| `tau/commonsense_qa` | `94630fe30dad` | 2024-01-04 | files |
| `allenai/hellaswag` | `55aa56627db0` | 2025-09-08 | files |
| `allenai/winogrande` | `01e74176c635` | 2025-07-11 | files |
| `allenai/social_i_qa` | `8835ceb9141d` | 2025-12-01 | parquet conversion `537a2ec8ec56` (2024-05-23) |
| `ybisk/piqa` | `2e8ac2dffd59` | 2024-01-18 | parquet conversion `142c51238b3c` (2024-03-07) |
| `allenai/coqa-gen2mc` | `5e0394a08cc6` | 2025-09-29 | files |
| `allenai/drop-gen2mc` | `33191ccb7014` | 2025-09-29 | files |
| `allenai/jeopardy-gen2mc` | `0bbd389863e5` | 2025-09-29 | files |
| `allenai/nq-gen2mc` | `a7e553744f28` | 2025-09-29 | files |
| `allenai/squad-gen2mc` | `e80f601243d8` | 2025-09-29 | files |
| `allenai/sciq` | `2c94ad3e1aaf` | 2024-01-04 | files |
| `allenai/qasper-yesno` | `f47a392e5444` | 2025-05-29 | files |
| `allenai/sciriff-yesno` | `c5ce62cb03b2` | 2025-06-03 | files |
| `allenai/basic-skills` | `faf63e9719e1` | 2025-05-16 | `<skill>/validation.json` |
| `futurehouse/lab-bench` | `5c77cec64843` | 2025-09-27 | files |
| `EleutherAI/lambada_openai` | `900124bf3b82` | 2025-07-10 | files |
| `openlifescienceai/medmcqa` | `91c6572c4540` | 2024-01-04 | files |
| `davidheineman/medqa-en` | `a9cc2ff94f04` | 2025-05-30 | files |
| `EleutherAI/hendrycks_math` | `21a5633873b6` | 2025-01-12 | files |
| `openai/openai_humaneval` | `7dce6050a7d6` | 2024-01-04 | files |
| `google-research-datasets/mbpp` | `4bb6404fdc6c` | 2024-01-04 | files |
| `allenai/multilingual_mbpp` | `c86b037e7d20` | 2025-05-09 | files |

Revisions are shown to 12 characters; the full SHAs are in the task definitions as each task lands.

Three datasets are loading scripts that `datasets` 5.1 (our lockfile) can't run: `allenai/social_i_qa`, `ybisk/piqa` and `allenai/basic-skills`. OLMES (which pins `datasets<4`) runs the scripts. For SocialIQA and PIQA we read the Hub's parquet conversion (`refs/convert/parquet`) at the pinned commit in the table. SocialIQA's head commit is newer than its conversion, but it only edits `README.md`, so the conversion reflects the current script. Basic skills has no parquet conversion; its script reads one `validation.json` per skill from the same repo, and we read those files directly. Parity checks that both sides see the same docs in the same order (D17 needs the order for sampled shots).

---

## 3. Design decisions

Numbered after D11 to D15 in `002-truncation-count.md`.

### D16. A doc lists its choices as (context, continuation) pairs

001's `MultipleChoiceTask` renders every prompt as `Question: <q>\nAnswer:` and every choice as `" <choice>"`. Most of the tasks above need something else: no prefixes (HellaSwag, LAMBADA, basic skills), extra lines (DROP's passage, SQuAD's title, Jeopardy's category, MMLU's subject header), a context per choice with one shared continuation (WinoGrande), or a gold-only continuation with its own leading-space rule (math and code). Adding a flag per case would grow `MultipleChoiceTask` with every task.

Instead, `to_doc` renders the text, and the task only assembles it:

```python
@dataclass(frozen=True, slots=True)
class Choice:
    context: str  # the doc's prompt for this choice, after the few-shot block
    continuation: str  # exactly the scored text, leading space included


@dataclass(frozen=True, slots=True)
class MCDoc:
    id: str
    choices: tuple[Choice, ...]
    gold: int
```

- **The context of a request** is the task's `description` (MMLU's subject header, empty otherwise), then each shot's gold `context + continuation` followed by `"\n\n"`, then the choice's `context`. This is OLMES's `fewshot_context` (`oe_eval/tasks/base_task.py:555-575`) and gives the same string as 001 for ARC-Easy.
- **Most tasks share one prompt for every choice.** A helper `cloze(question, answers)` returns the `Choice`s for `Question: <q>\nAnswer:` and `" <answer>"`, so a cloze task's `to_doc` stays one call; `gold` goes to the `MCDoc`, as the index it is. WinoGrande gives each choice its own context and the same continuation.
- **Gold-only tasks** (math, code, LAMBADA) are docs with one choice and `gold=0`. Their `to_doc` applies OLMES's leading-space rule for that task: Minerva and HumanEval add a space unless the text starts with a space or newline (`oe_eval/tasks/base_task.py:276-278`), MBPP never does (`oe_eval/tasks/oe_eval_tasks/codex_mbpp.py:212-214`).
- **What a task reports** becomes a field, `metrics`, defaulting to all of 001's metrics. Gold-only tasks report `("bpb",)`, since an accuracy over one choice is always 1. PMI stays `unconditional_context` (WinoGrande and MedQA set it to `None`, as OLMES does).

`num_chars` and `num_bytes` still count the scored continuation, so the leading space is counted (OLMES sec. 3.5) and every metric is unchanged.

**Cost:** ARC-Easy's definition changes shape, so its task hash changes (D7 hashes the definition), while every request string and score stays the same. The PR says so, and the comparison is string for string.

### D17. Few-shot examples: ids where they exist, rows where they don't, frozen either way

OLMES picks shots four ways: curated examples written in its code (`oe_eval/tasks/fewshot_sources.py`), `Random(1234).sample` over the train split, the first rows of a split (MMLU's `dev`), and a sample from the scored split minus the scored doc (HumanEval, basic skills). Reproducing its random draws at run time would tie our tasks to Python's `random` and to row order.

- **Every draw is resolved once, offline, and frozen as data** in the task definition, with a comment naming the OLMES source it reproduces. Nothing is sampled at run time.
- **Curated examples that are rows of the dataset** (ARC, CSQA, HellaSwag, SocialIQA, PIQA) are found by content once and kept as ids, as 001 does for ARC-Easy.
- **Examples that exist only in OLMES's code** (the gen2mc tasks, Minerva, WinoGrande if its examples aren't found in train) are kept as raw rows in the task module, in the dataset's own row format, and go through the same `to_doc`. `Fewshot` gains a `rows` alternative to `split` and `ids`.
- **Shots from the scored split:** `Fewshot` gains `num_shots`. When it holds more ids than `num_shots`, each doc takes the first `num_shots` ids that aren't the doc itself, which is OLMES's sample-k-plus-one-and-drop-self (`oe_eval/tasks/base_task.py:501-507`).
- **OLMES keeps a doc's own example in its context in four tasks:** `qasper_yesno`, `sciriff_yesno` and both `lab_bench` tasks sample shots from the train split they score and never exclude the doc (`oe_eval/tasks/base_task.py:591-595`). For 3 to 5 docs per task, the answer is in the prompt. We follow OLMES here for parity, and the definition says so in a comment. Whether to keep it after parity is an open question (section 6).

### D18. Suites are data, and their scores are means of members

```python
@dataclass(frozen=True, slots=True)
class Suite:
    name: str
    members: tuple[MultipleChoiceTask | Suite, ...]
    metric: str | None = None  # None: each member's primary metric
```

A suite's score is the unweighted mean of its members' scores, a nested suite first, as OLMES's `_macro` (`oe_eval/tasks/aggregate_tasks.py:147-149`). `qa_rc` and `qa_bpb` are two suites over the same task objects, with `metric=None` and `metric="bpb"`, so `run --suite base-easy` scores each task once. `results.json` gains a `suites` section next to `tasks`; each entry lists its members by name, so a reader can recompute it.

OLMES's `arc:rc::olmes:full` has no `primary_metric` of its own (`oe_eval/configs/task_suites.py:128-130`). By our reading of `aggregate_tasks.py`, that means ARC never forms a group and OLMES's `qa_rc` would have no score. This is checked on the first OLMES run. We count ARC-Easy and ARC-Challenge as two members of `qa_rc`, and the stage file records what OLMES actually did.

### D19. Math and code are gold-only tasks, not a new task type

Each math or code doc is one request: the few-shot prompt, then the gold solution. With D16 that is an `MCDoc` with one `Choice`, and `bpb` is 001's metric, so no new task class or scoring path is needed. The renderers follow OLMES exactly, including quirks, each cited in its `to_doc`:
- Minerva: shots are `Problem:\n<p>\n\nSolution: <solution>` (`oe_eval/tasks/oe_eval_tasks/minerva_math.py:153-198`).
- HumanEval: each shot is `prompt + " " + canonical_solution`, an extra space before already-indented code (`oe_eval/tasks/oe_eval_tasks/codex_humaneval.py:140-149`).
- MBPP: the target is `code.rstrip() + "\n```"` (`codex_mbpp.py:185-202`). Multilingual MBPP replaces `\r\n` with `\n` and strips the code (`multilingual_mbpp.py:190-246`).

### D20. A greedy-match metric for LAMBADA

LAMBADA's `qa_rc` number is greedy accuracy: 1 if every token of the last word is the model's argmax (`oe_eval/metrics/metric.py:320-335`). `ScoringModel` returns summed log-probabilities and token counts, so this needs one more per-request output, computed on the device with the log-probabilities (an `argmax` over the same chunked logits, no extra host sync). This is a change to the numerical path, so it is its own PR with its own before/after check, and LAMBADA lands with `bpb` only until then.

### D21. Parity runs on full splits

ARC-Easy and every other task are compared on the full split each task scores, which is what OLMo 3's Base Easy runs (`::olmes:full` drops OLMES's 1000-doc `Random(1234)` sample, `oe_eval/configs/tasks.py:7818-7826`). OLMES runs the same full splits, so no sampling is reproduced.

### D22. How parity is measured

**Setup.** OLMES runs from its own locked environment (Python 3.12, torch 2.8, transformers 4.57, lm-evaluation-harness 0.4.3). It is never installed in or imported by flock. Both sides score `allenai/OLMo-2-0425-1B` at commit `a1847dff35000b4271fa70afc5db10fd29fedbdf` with `max_length` 4096 (OLMES's own config for this model, `oe_eval/configs/models.py:350-351`), one H200 job per side and dtype.

**Dtype.** OLMES loads `dtype="auto"`, which is float32 for this checkpoint, so the bfloat16 runs pass `dtype=bfloat16` to OLMES explicitly. Each side runs once in float32 and once in bfloat16.

**Batching.** The two sides can't batch alike. OLMES's command line scores one request per forward pass (`--batch-size` defaults to 1 in `oe_eval/run_eval.py`, and `olmes` passes none), so its requests see no padding. We pack by a token budget (32768) and pad on the left. In float32 this is invisible (001 measured padding differences of at most 1.8e-4 nats). In bfloat16, which requests share a batch moves a request's log-probability by about 0.05 nats on average (001, section 5). Parity runs OLMES at its default, so the bfloat16 gap measures only our side's batching, and the tolerance below is sized from that gap.

**Three checks, in order, per task:**
1. **Same requests.** OLMES writes every request's context and continuation (`requests.jsonl`, `--save-raw-requests`). Ours must be identical, string for string, with the same doc ids. This check needs no tolerance, and a failure is a formatting bug.
2. **Float32: the implementation.** Every request's log-probability is within 1e-3 nats of OLMES's, the predicted choice agrees on at least 99.9% of docs, accuracy is within 0.1 points, and `bpb` is within 1e-4.
3. **Bfloat16: what we report.** Accuracy within 0.2 points and `bpb` within 3e-4 of OLMES's bfloat16 run, and the predicted choice agrees on at least 99% of docs. MMLU and the other subject groups are judged on the group's score, since one doc of a 100-question subject is already 1 point.

A task passes when all three hold. A task that fails is debugged until it passes or is explained, and feeds no decision until then.

**How the bfloat16 tolerance was fixed.** Before any other task was compared, ARC-Easy ran on both sides in both dtypes, and the tolerance became the larger of the observed bfloat16 gap (rounded up to one significant figure) and our own run-to-run noise (0.05 points, 2e-5 `bpb`), with 0.5 points and 0.005 as the ceiling:

| ARC-Easy, 2376 docs | float32 | bfloat16 |
| --- | --- | --- |
| Requests identical (19002) | yes | yes |
| Largest per-request difference | 1.5e-4 nats | 0.50 nats (mean 0.059) |
| Docs whose `acc_per_char` choice differs | 0 | 11 (0.46%) |
| `acc_per_char` difference | 5e-8 | 0 (the 11 docs cancel) |
| `acc_raw` difference | 0 | 0.17 points |
| `acc_uncond` difference | 1e-8 | 0.13 points |
| `bpb` difference | 8e-9 | 2.2e-4 |

The float32 run passes check 2, so the implementation matches. The accuracy tolerance comes from the largest accuracy gap (`acc_raw`, 0.17 points, so 0.2), not from the primary metric's, which is 0 only by cancellation. About 0.5% of docs change their choice in bfloat16, so on tasks of a thousand docs or fewer chance alone can exceed 0.2 points; choice agreement is the sturdier check there, and such a miss goes through the explained path above.

**`acc_per_token` is not compared.** OLMES at `5a51f50` records one token for every continuation: `_unmask_tokens` returns `Ellipsis` when nothing is masked, and `len(cont_toks[...])` of a `[1, seq]` tensor is 1 (`oe_eval/models/eleuther_huggingface.py:100-108, 505`). Its `acc_per_token` is therefore its `acc_raw` on every task. Ours divides by the real token count. Where OLMES's primary metric is `acc_per_token` (basic skills), parity compares our `acc_raw` with OLMES's number, and we report our `acc_per_token` as the task's metric.

### D23. A second model for rankings

Levels can agree while rankings don't, for instance if one side's bias depends on the model. Both sides also score `allenai/OLMo-2-0425-1B` at `stage1-step50000-tokens105B` (commit `5d0cce11a655ebed42a7a2b5a15348bd65ce5733`), a 105B-token checkpoint, close to the 100B-token budget Base Easy was built for (OLMo 3, sec. 3.3.2). For each task and suite, the sign of the difference between the two models must be the same on both sides whenever OLMES's difference is larger than the tolerance in D22.

---

## 4. Plan

Each PR is 200 to 400 lines of code and one idea. Docs and tests ride with the PR they describe.

1. **Core: choices as pairs (D16, D17).** `Choice`, the new `MCDoc`, `description`, `metrics`, `Fewshot.rows` and `num_shots`; ARC-Easy rewritten on them. Check: every ARC-Easy request string and score is identical before and after (the hash changes, as D16 says).
2. **Suites (D18).** `Suite`, the `suites` section of `results.json`, `run --suite`, and `base-easy` with only the tasks that exist so far.
3. **OLMES-format QA.** ARC-Challenge, CSQA, HellaSwag, WinoGrande, SocialIQA, PIQA, and MMLU's 57 subjects generated from a list.
4. **Other QA.** The five gen2mc tasks, SciQ, Qasper, SciRIFF, basic skills, LAB-Bench, MedMCQA, MedQA, and LAMBADA (`bpb` only).
5. **Greedy match (D20).** The extra scorer output and LAMBADA's accuracy, with a before/after check of every other score.
6. **Math and code (D19).** Minerva's 7 subjects, HumanEval, MBPP, and multilingual MBPP's 17 languages.

Parity (D22) runs on each group of tasks as it lands, and a group's PR reports its parity table.

---

## 5. Tests

- **Rendering:** for every task kind (cloze, no prefix, MMLU header, WinoGrande, gold-only), one fixture row renders to the exact string OLMES writes for it, copied from an OLMES `requests.jsonl`.
- **Few-shot:** `num_shots` skips the scored doc, literal rows go through `to_doc`, and missing ids raise.
- **Suites:** nested means on hand values; `qa_rc` and `qa_bpb` read one scored task.
- **Gold-only tasks** report only `bpb`, and it matches 001's definition on a hand value.
- **Greedy match:** on the tiny model, it equals an unbatched argmax check per request, at every batch budget.

---

## 6. Open questions

- After parity, keep OLMES's self-including shots in Qasper, SciRIFF and LAB-Bench (D17), or exclude the doc and rename the tasks?
- LAMBADA, basic skills and the 17 code languages add many small tasks for little signal at our scales. Are any of them dropped from the first public suite?

---

## 7. How it was actually built

### PR 1: choices as pairs (D16, D17)

- **`MCDoc` validates itself.** An empty `choices` or a `gold` outside it raises with the doc's id when `to_doc` builds the doc, on the host. `MCRequests` can then rely on every doc having a choice, so it has no check of its own for that case.
- **`Fewshot`** takes either `split` and `ids` or `rows`, never both, and raises on a repeated id or on a `split` next to `rows`. `num_shots` must leave one spare example, since OLMES draws one more than it shows for exactly this case, and needs `ids`.
- **`num_shots` needs the scored split.** It drops a doc's own example by id, and ids are unique within one split only, so the task rejects `num_shots` on examples from another split (a train pool with HF index-style ids would otherwise drop a test doc's namesake, silently). D17's uses are all from the scored split.
- **`MCRequests.docs` and `.requests` are tuples**, so a frozen `MCRequests` can't change after its hash is taken.
- **`metrics`** is `None` by default, meaning every metric the task computes. `reported_metrics` resolves it, and `score_task` keeps only those. An empty tuple is rejected rather than read as `None`, so the two don't hash differently for the same behaviour.
- **`question_prefix` and `answer_prefix` left the task.** Rendering moved into `to_doc`, and `cloze` takes `question_prefix` for tasks such as PIQA (`Goal: `). Its signature is `cloze(question, answers, question_prefix="Question: ")`: no `gold`, which belongs to the `MCDoc`. A bare string as `answers` raises, since it would render one choice per character; a gold-only doc passes `(answer,)`.
- **Check.** Every ARC-Easy request string, doc id and gold index is identical before and after (all 19002 requests compared). The task hash changes once, as D16 says. On an H200 in bfloat16 the branch scores within run-to-run noise of `main`: bfloat16 scoring is not bit-reproducible, even for one commit run twice on one node (about 5% of requests move, by at most 0.29 nats, so `acc_raw` moves by up to 0.08 points and `bpb` by 5e-5). The branch differs from its base by no more than that, and `acc_per_char` is 0.7677 in every run.

