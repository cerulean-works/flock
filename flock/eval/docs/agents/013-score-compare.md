# 013: scores compared against a banked run

| | |
| --- | --- |
| Status | built |
| Date | 2026-10-27 |
| Builds on | [001](001-eval-core.md) (D7: the task hash), [004](004-suites.md) (suites), [012](012-task-snapshot.md) (definitions pinned in CI) |
| Keeps | every task, request and score; `results.json` unchanged |
| Result | `flock-eval compare BANKED NEW`, `flock-eval run --dtype`, the OLMo 2 1B float32 bank on `base_easy` |

## 1. Goal

Issue #23 asks for regression tests on scores: a change that moves a model's numbers should be caught. Stage 012 pins what every task *is* in CI, offline. What it cannot see is a score: a change in `to_doc` on real rows, in the forward pass, in batching, or in a library that moves a number. Scores need real weights and a GPU, and CI has neither, so this stage adds a comparison that runs as a GPU job on request: score a reference model, and compare its `results.json` with one banked in the repository.

**Out of scope:** GPU jobs in CI and a schedule; a bfloat16 bank; banks for models other than OLMo 2 1B; tolerances measured across stacks; changing any task's requests.

---

## 2. Design decisions

### 013.D1. `flock-eval compare BANKED NEW` compares two `results.json` files, metric by metric, within a tolerance

`compare` reads two results files and pairs their tasks and suites by name. For each task in both it compares every metric in `metrics`, `num_docs` and the three `truncation` counts; for each suite in both, `members` and `score`. Any value that moves past its tolerance is a breach, printed as one line with the name, the field, both values and their difference. Timing fields (`seconds`, `docs_per_second`) are not compared. The exit code is 0 when nothing moved past its tolerance and 1 on any breach.

Each value has a tolerance. An accuracy may move by a net change of 2 docs, as `(2 + 0.5) / num_docs`: an accuracy moves in steps of one doc, and the half step keeps float rounding in the difference from turning the second flip into a breach. Any other metric, today only `bpb`, may move by 1e-4 of its banked value. A suite's score is the unweighted mean of its members' scores, so their tolerances combine as independent errors: the root of their sum of squares, divided by the number of members. A plain mean of the tolerances would let a regression that moves every member just under its own bound pass at the suite too, while drift from reordered sums is independent across tasks and partly cancels. `num_docs`, the truncation counts and `members` must be equal. The bank needs a tolerance because it is meant to check runs on other stacks: another GPU type, kernel or library version reorders float32 sums, so a rerun there moves scores slightly even when nothing is wrong. Under equality every such run would be a breach, and refreshes nobody reads would replace the check. The values are argued, not measured. A real regression, such as a tokenization or truncation bug, moves an accuracy by far more than 2 docs net and `bpb` by 1e-3 or more, while float32 reordering moves a summed log-likelihood by about 1e-6 relative and flips only near-ties. The first rerun on another stack measures the drift, and the values are tightened to a few times it. They are `FLIPPED_DOCS` and `RELATIVE_TOLERANCE` in `compare.py`. A tolerance bounds the net change of a mean, so offsetting flips inside a task pass; a change to a task's requests is refused by its `task_hash` before any score is read (D2), and `bpb` moves with any change to the gold choice's log-likelihood. An infinite or NaN value must repeat exactly. On the bank, `mmlu` (57 subjects of about 100 docs) may move by about 0.002, and the `bpb` suites by 1e-5 to 3e-5.

On one stack, scores do repeat exactly. Parity with OLMES is identical requests plus float32 scores that agree (stage 003, D22's check 2), and a rerun of our own code on the same stack is held to more than that: float32 scoring is bit-identical run to run on one GPU type and one set of library versions (measured twice on ARC-Easy, two runs on one H200 each time: 0 of 19,002 requests differ). The bank's second job extends that measurement to all of `base_easy`. So on the bank's own stack, any float32 difference, even one inside the tolerance, has a cause worth reading: the code, a library, the GPU, or the data. The printed provenance shows which stack a run used.

A task or suite in only one file is listed and is not a breach: a rerun of a few tasks compares against a full bank. A task added to `base_easy` after the bank was written shows as "only in the new run", and each suite holding it as a breach on `members` and `score`, until the bank is refreshed. At least one task must be in both, or `compare` refuses.

**Prior work.** lm-evaluation-harness v0.4.3 pins scores in `tests/models/test_huggingface.py`: Pythia-70M in float32 on CPU, with `torch.use_deterministic_algorithms(True)`, scores the first 10 ARC-Easy docs and checks each log-likelihood with `np.allclose(..., atol=1e-2)`, in CI, downloading the model. That is a per-request pin on a tiny model with a loose tolerance. Here the unit is a task's metrics over its full split on the model we compare against, on a GPU, run on request, with tolerances much tighter than 1e-2 on an accuracy (2.5 docs of ARC-Easy's 2376 is about 0.001). OLMES has no tests. The tolerance values have no precedent and are argued as above.

### 013.D2. `compare` refuses two runs that are not the same instrument

A score difference means something only when both runs scored the same requests under the same settings. `compare` refuses, before comparing any score, with a message naming the field and both values, when:

- `provenance.model`, `dtype`, `batch_tokens`, `max_length` or `attention` differ (the model, or the settings that change scores by design: `max_length` decides which requests are cut from the left). The SDPA kernel pin of a planned stage joins this list as `sdpa_backend` when it lands, since it changes bfloat16 scores;
- a task in both files has a different `task_hash` (its definition, dataset revision, few-shot examples or any rendered request changed) or a different `limit`;
- a suite in both files has a different `metric`, so its score averages another metric over the same members;
- a file is not JSON, or lacks a key `compare` reads, or has a value of the wrong type there.

A refusal is not a breach: the runs are different instruments (every model in a comparison is scored with the same prompts, few-shot examples, normalizations and batching), and the answer is a new bank, not a tolerance. It exits with 2, as a usage error does.

The other provenance fields (`flock_commit`, `torch`, `transformers`, `python`, `device`) and the file's `schema_version` are printed side by side when they differ, before the breaches, because they are the usual causes of one. `device` is the GPU's name as CUDA reports it (`NVIDIA H200`), or `cpu`, so a run on another GPU type shows here. A library upgrade that moves a float32 number is a breach, and the bank is refreshed in the same pull request as the lockfile once the change is understood.

`compare` reads the JSON keys it needs, not the `Results` dataclass, so a bank written under an older `schema_version` stays comparable as long as those keys exist; a missing key refuses naming the key. The printed provenance fields are read only if present, so a field added to provenance later doesn't refuse older banks.

### 013.D3. `flock-eval run --dtype` selects the weights' dtype

The bank is float32, and `flock-eval run` has always loaded weights in bfloat16, so float32 runs went through the Python API. `EvalConfig` gains `dtype: Dtype = "bfloat16"`, where `Dtype` is `Literal["bfloat16", "float32"]` in `run.py`, checked in `__post_init__` and passed to `ScoringModel.from_pretrained`, and `run` gains `--dtype` with the choices of `Dtype`. The provenance already records the dtype that ran. This is the only change to `run`, and with the default every existing command scores as before.

### 013.D4. The bank is a `results.json` beside the task snapshot, written by one documented command

The bank is `tests/eval/regression/olmo-2-0425-1b-float32.json`, beside stage 012's snapshot in `tests/eval/snapshot/`, so the package ships code only. It is the `results.json` of

```bash
uv run flock-eval run \
  --model allenai/OLMo-2-0425-1B@a1847dff35000b4271fa70afc5db10fd29fedbdf \
  --suite base_easy --dtype float32 --out <dir>
```

copied unchanged, so its provenance records the flock commit, the command, the GPU and the library versions that wrote it. The predictions files are not banked; a breach is traced by rerunning both commits. The tolerances are two constants in `compare.py`, not a file per bank.

A refresh is a pull request that reruns the command and replaces the file, reviewed like code: its description shows `compare`'s output from the old bank to the new one and says why each difference is expected. Reasons to refresh: a task added to `base_easy`, a deliberate change to scoring or to a task's requests, or a library upgrade that moves float32 numbers past the tolerances.

The run-path provenance stage in flight adds three things the bank gains at its first refresh after it merges: each task's dataset as `repo@revision` beside its hash (today the hash covers the revision but cannot be read back), a hash over each suite's members (which `compare` will check in place of `members`), and the `tokenizers` and `datasets` versions (printed with the other library versions). `compare` changes with it: it checks the suite hash where it now checks `members`, and prints the two versions with the other library fields. The refusal and tolerance rules stay.

---

## 3. Plan

1. `flock/eval/compare.py`: read two results files, refuse different instruments (D2), list breaches (D1).
2. `cli.py`: `compare` and `run --dtype`; `run.py`: `EvalConfig.dtype` (D3).
3. Tests on small results files built in the test: equal files pass; a metric or suite score past its tolerance, or a changed truncation count, is a breach with exit 1, and one within it is not; a changed task hash, `limit`, model, dtype, `batch_tokens` or `attention` refuses with exit 2; one-sided tasks are listed; no common task refuses; `--dtype float32` reaches the model on the tiny model.
4. The bank: the command of D4 as a GPU job, its `results.json` copied to `tests/eval/regression/`, then a second job that reruns it and runs `compare` against the first, which must report no breach.
5. Docs: a `regression.md` page, `compare` and `--dtype` in `cli.md`.

---

## 4. How it was actually built

- `flock/eval/compare.py` holds `compare`, `Comparison` and `Breach`; `cli.py` adds `compare` and `format_comparison`, and `run --dtype`. `run.py` adds `Dtype` and `EvalConfig.dtype`.
- Review added two refusals, two print rules and one equality rule to D1 and D2, all in the decisions above. The refusals: a common suite with a different `metric` refuses (it changes what the score averages, while `members` and `score` stay breaches); a file that is not JSON, or has a value of the wrong type where a key is read, refuses like a missing key. The print rules: the printed provenance fields are read only if present, and `schema_version` is printed with them when it differs. The equality rule: a NaN on both sides is equal, since NaN never equals itself and a NaN that repeats is reproduced.
- Review also replaced equality on scores with the tolerances of D1, since the bank is meant to check runs on other GPU types and library versions. A second review pass made a suite's tolerance combine its members' as independent errors instead of averaging them (a mean let 2 flips in each MMLU subject pass at `mmlu`), made an infinity repeat exactly instead of taking an infinite relative tolerance, and gave a task with no docs a zero tolerance instead of dividing by zero. The bank's rerun had 0 breaches under equality, so it has 0 under the tolerances.
- The bank was written by the command of D4 at `d23f668`, on one H200 (2 h 58 min, of which scoring 2 h 53 min): 109 tasks and 10 suites, 10 truncated requests in all, ARC-Easy `task_hash` `ad683d6e`. It was copied unchanged to `tests/eval/regression/olmo-2-0425-1b-float32.json`, about 76 kB.
- The rerun at `7dd26fb` (the bank's commit plus the bank file) ran on a different node with the same GPU type and libraries (`torch` 2.14.1+cu130, `transformers` 5.20.1), then `compare` against the bank: `compared: tasks 109, suites 10, breaches 0`, exit 0, with only `flock_commit` printed as differing. Beyond what `compare` reads, all 109 predictions files were byte-identical, so every request's log-likelihood repeated, not only the means. This extends the float32 run-to-run measurement of D1 from ARC-Easy to all of `base_easy`, and across two nodes.
- A CI test rebuilds `Results` from the bank, writes it in the current layout and compares it with the bank, offline, so a change to what `Results` writes or to what `compare` reads that the bank can no longer pass fails in CI rather than in a GPU job.

## 5. Known gaps

- A task added to `base_easy` does not fail CI against the bank: the bank is refreshed by hand in that PR or a later one, and until then `compare` lists the task and reports breaches on the suites that hold it (D1).
- The check needs about three H200 hours and is run on request; there is no schedule.
- A bfloat16 bank waits for the SDPA kernel pin.
