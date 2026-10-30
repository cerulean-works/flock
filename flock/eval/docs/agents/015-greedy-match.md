# 015: greedy match, and LAMBADA's accuracy

| | |
| --- | --- |
| Status | in review |
| Date | 2026-10-29 |
| Builds on | [003](003-base-easy.md) (D20: a greedy-match metric for LAMBADA), [007](007-fewshot-example.md) (D2: `GoldOnlyTask`) |
| Keeps | every request string; every other task's scores, metrics and hash |
| Result | `Scores.is_greedy`, the `acc_greedy` metric, LAMBADA as a `GoldOnlyTask` reporting `acc_greedy` and `bpb`, LAMBADA in `base_easy:qa_rc`, `is_greedy` in prediction rows, `results.json` schema 6 |

The file number and the decision numbers are provisional. The file takes the next free number when its PR is opened.

## 1. Goal

OLMES scores LAMBADA in Base Easy's `qa_rc` by greedy match: a doc counts when every token of the passage's last word is the model's most likely next token (`GreedyAccuracyMetric` in `oe_eval/metrics/metric.py`, `is_greedy` in `oe_eval/models/eleuther_huggingface.py`). Stage 003 planned it as its own change (D20), because the scorer returned only summed log-probabilities and token counts, so LAMBADA has reported `bpb` alone and sat only in `base_easy:qa_bpb`. This stage adds the per-request output, the metric, and LAMBADA's place in `qa_rc`.

**Out of scope:** greedy match for multiple-choice tasks, generation, and any change to how a log-probability is computed.

---

## 2. Design decisions

### 015.D1. The scorer returns `Scores.is_greedy` for every request

`ScoringModel.loglikelihood` gains a fourth output, bool `[len(requests)]`: whether every continuation token is the argmax of its position's logits. It is computed in `_target_logprobs`, in the same chunks that already convert logits to FP32 for the log-softmax, so no logits are kept longer and nothing waits for the GPU. Ties go to the lower token id, as `torch.argmax` breaks them in OLMES.

OLMES takes the argmax of its log-softmax in the model's dtype. We take it over the FP32 logits, which the normalizer doesn't reorder; in BF16 OLMES's rounded log-probabilities can tie where ours don't, so a near-tie can resolve differently. It is computed for every request rather than behind a flag: an argmax over a chunk already in memory is cheap next to the forward pass (section 5 gives the measured cost), and a flag would be a second scoring path.

### 015.D2. `acc_greedy` reads the gold choice's `is_greedy`, and only a gold-only task reports it

`multiple_choice` returns `acc_greedy`: 1.0 where the gold choice's continuation is greedy. Its name is OLMES's `greedy_acc` in our `acc_*` family, so `scale` reads it as an accuracy and a suite can average it with other tasks' accuracies, and `compare` holds it to the accuracy tolerance, with no special case.

Greedy match compares no choices, so on a multiple-choice task it would be a different question from that task's accuracy. `MultipleChoiceTask` never reports it, and a task that names it in `metrics` is refused. `GoldOnlyTask` can report `acc_greedy` and `bpb`, and reports `bpb` alone unless `metrics` says otherwise, so the math and code tasks are unchanged. Every other task's metrics, `results.json` entries and hash are the same as before.

### 015.D3. LAMBADA is one `GoldOnlyTask`, read by `acc_greedy` in `qa_rc` and by `bpb` in `qa_bpb`

OLMES has two tasks on the same requests: `lambada` (primary `greedy_acc`) and `lambada:bpb` (primary `bits_per_byte_corr`, `oe_eval/configs/tasks.py`). We keep one task, `lambada`, with primary `acc_greedy` and metrics `("acc_greedy", "bpb")`, the way every QA task sits in both suites and `qa_bpb` reads its `bpb`. LAMBADA joins `base_easy:qa_rc` as its last member.

Its request strings are unchanged. Its hash changes once, because `primary_metric` and `metrics` are part of the definition, so `compare` refuses a banked run that holds LAMBADA as another instrument, before any score is read, until the bank is refreshed. A comparison that leaves LAMBADA out of both runs is unaffected.

### 015.D4. Prediction rows record `is_greedy` per choice, and `results.json` goes to schema 6

014.D1 says a prediction row holds every input of every number the task reports, and `acc_greedy` is read from `is_greedy`, which no other field gives. So each row gains `is_greedy` per choice, after `num_bytes`, and `acc_greedy` can be recomputed from the file alone. The rows changed, so `schema_version` goes from 5 to 6, as 014.D1's new fields took it from 4 to 5 (014.D8). Apart from the version, `results.json` changes only as 015.D3 says: LAMBADA's hash and `base_easy:qa_rc`'s members and score. `compare` reads neither the rows nor the version beyond printing it, so the version alone causes no breach: the regression bank, at schema 4 until its refresh, is refused only for LAMBADA's hash (015.D3).

*Prior work:* OLMES (`5a51f50`) keeps `is_greedy` per choice in `predictions.jsonl` next to `sum_logits` (`oe_eval/models/eleuther_huggingface.py:507`, written per choice as `model_output` in `oe_eval/metrics/metric.py:106`), which is how its `greedy_acc` was checked against ours without a new run.

---

## 3. Corrections to 007 and 010

**D2, `GoldOnlyTask`'s fixed fields.** 007.D2 says `primary_metric`, `metrics` and `unconditional_context` are set by the class and can't be passed. `primary_metric` and `metrics` can now be passed, with the old values as defaults, so LAMBADA can ask for `acc_greedy`. `unconditional_context` is still fixed to `None`, and what the task can report is fixed by the class (015.D2).

**010, the toy task.** Section 7 of 010 says `toy:gold_only` renders toy rows in DRCD's format, so the task snapshot pins the `文章：` passage line. The snapshot keeps one toy task per prompt path, and `toy:gold_only` is now this stage's English gold-only task reporting `acc_greedy` and `bpb`. DRCD's rendering, passage line included, is pinned by `test_drcd.py` alone.

---

## 4. Plan

1. `requests.py`: `Scores.is_greedy`. `model.py`: `_target_logprobs` also returns whether each target is the argmax; `_score` reduces it per request; `loglikelihood` fills it.
2. `metrics.py`: `ChoiceScores.is_greedy`, `acc_greedy` in `MULTIPLE_CHOICE_METRICS` and `multiple_choice`. `tasks/base.py`: `choice_scores` lays it out per doc; `MultipleChoiceTask` leaves it out of what it computes; `GoldOnlyTask` computes `acc_greedy` and `bpb`.
3. `tasks/lambada.py`: LAMBADA as a `GoldOnlyTask`. `tasks/base_easy.py`: LAMBADA in `qa_rc`.
4. Tests: `is_greedy` equals an unbatched argmax per request at every batch budget, on requests built from the model's own greedy tokens so both outcomes occur; argmax ties; `acc_greedy` reads the gold column; `toy:gold_only` in `tests/eval/toy.py` reporting `acc_greedy`, and the snapshot regenerated.
5. Docs in `docs/source/evals/`: `task-definitions.md`, `tasks.md`, `metrics.md` and `comparison.md`. `results.py`: `is_greedy` in `prediction_rows` and schema 6, with `results.md`'s predictions table (015.D4).
6. Checks: every other score unchanged before and after; LAMBADA's `acc_greedy` against OLMES on OLMo 2 1B (stage 003, D22).

---

## 5. How it was actually built

All numbers are on `allenai/OLMo-2-0425-1B`, final checkpoint `a1847dff` unless named, on one H200, against OLMES at `5a51f50`. OLMES needed no new run: its `lambada:bpb` predictions already record `is_greedy` per doc, which is what its `greedy_acc` averages.

- **Nothing else moved.** On the tiny test model, for every task this stage does not change, every log-probability, token count, truncation count and metric is byte-equal before and after, and so are `results.json` and the predictions once the new `is_greedy` field and `schema_version` are set aside. ARC-Easy's 19,002 requests and hash are unchanged, and its FP32 log-probabilities and metrics are byte-equal on the GPU. LAMBADA's FP32 log-probabilities are byte-equal and its `bpb` is 0.4381838 on both sides, as banked. In BF16, two runs before against two after differ by at most one doc on any accuracy and by at most 4.8e-5 `bpb`. Two runs of one commit differ by one doc and up to 4.1e-5, so BF16 scoring is not deterministic run to run, and earlier ARC-Easy runs measured that noise at up to 5e-5.
- **FP32 parity.** `acc_greedy` is 0.60198 on both sides, and all 5,153 docs agree on `is_greedy`. The largest request gap is 2.3e-4 nats.
- **BF16 parity.** `acc_greedy` 0.60101 against OLMES's 0.60043, 99.4% of docs agree, inside 003's D22 tolerance (0.2 pt, 99%). LAMBADA's BF16 `bpb` gap is 4.2e-4, over D22's 3e-4, the same gap measured when LAMBADA's `bpb` was added; `base_easy:qa_rc` reads `acc_greedy`, which passes. From OLMES's FP32 answer, 27 docs change their greedy match in our BF16 run and 36 in OLMES's. At step 50000 (`5d0cce11`): 0.54609 against 0.54628, 99.6% agree, inside the tolerance. Both sides rank the two checkpoints the same way.
- **Cost.** None measurable: ARC-Easy takes 85.3 s against 85.5 s in FP32, and LAMBADA 20.2 s against 20.1 s.
- **The tests' greedy requests.** A random tiny model is almost never greedy on fixed text, so a test of only fixed requests would check one outcome. The test adds requests built from the model's own next one and two greedy tokens after each context, and asserts that both outcomes occur.

---

## 6. Known gaps

- The FP32 bank in `tests/eval/regression/` predates LAMBADA's new hash and `qa_rc` member (015.D3), so `compare` refuses it against a new run of `base_easy` until its next refresh.
