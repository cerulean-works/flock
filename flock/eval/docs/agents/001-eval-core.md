# 001: scoring core, tasks as data, ARC-Easy

| | |
| --- | --- |
| Status | in review |
| Date | 2026-10-19 |
| Builds on | nothing (first eval stage) |
| Keeps | n/a |
| Result | 61 tests in `tests/eval/` pass on CPU in about 1 s; see [section 5](#5-how-it-was-actually-built) |

## 1. Goal

`flock.eval` was an empty package. This stage builds the core every later task stands on:

1. **A scoring model.** It wraps a causal LM and its tokenizer and returns the log-probability of
   (context, continuation) pairs in batches, on the GPU.
2. **Tasks as data.** A multiple-choice task is a short definition: a pinned dataset, a
   row-to-doc function and few-shot ids. It is not a new code path.
3. **Metrics.** Accuracy under each OLMES normalization, and bits per byte of the gold answer.
4. **One task end to end.** ARC-Easy, scored the way OLMo 3's Base Easy scores it.
5. **Results with provenance**, a CLI, and a picklable entry point that job runners can call.

**Out of scope:**
- parity with the OLMES reference implementation (a later stage measures it);
- health metrics (attention entropy and the like);
- generative tasks and vLLM;
- Traditional Chinese tasks;
- running evals in CI.

**Constraint:** the same code, prompts and settings score every model. A number that needs
another setting is another task with another name.

---

## 2. Design decisions

### D1. Follow OLMES and OLMo 3, read but never imported

The task format, normalizations and few-shot examples follow OLMES (Gu et al. 2024,
arXiv 2406.08446). The bits-per-byte metric follows OLMo 3 (arXiv 2512.13961, sec. 3.3.2). We
read their reference code (allenai/olmes, which builds on lm-evaluation-harness 0.4.3) to match
details the papers leave out, and we don't depend on it. A later stage checks parity against it.

### D2. Tokenize the way OLMES does

`ScoringModel.encode` copies lm-evaluation-harness's `_encode_pair`, which OLMES uses:
- whitespace at the end of the context moves to the start of the continuation;
- the continuation's tokens are `tokenize(context + continuation)[len(tokenize(context)):]`, so a
  token that spans the boundary counts as continuation;
- no special tokens are added. OLMES only adds a BOS for Gemma models, so OLMo 2 and Llama 3.2
  see no BOS;
- an empty context (after moving its whitespace) becomes the BOS token, or EOS if there's no BOS
  (a tokenizer with neither raises);
- inputs longer than `max_length` (default: the config's `max_position_embeddings`) lose
  tokens from the left.

A continuation with no tokens raises, as OLMES asserts. Scoring it as 0 (the log-probability of
nothing) would rank it above every real choice.

### D3. Batch by a token budget, no syncs of our own

Requests are sorted longest first (as lm-evaluation-harness does) and packed greedily into
batches of at most `batch_tokens` padded tokens (default 32768). Rows are left-padded with an
attention mask and explicit position ids, so every continuation ends at the last position. The
model's own forward then runs with `logits_to_keep` set to the batch's longest continuation, so
full-vocabulary logits are never built for the few-shot context, and any rescaling the model
applies after its LM head (Gemma's soft-capping, Granite's `logits_scaling`) is kept. Only those
logits are used: the target's logit, in float32, minus the float32 `logsumexp` over the
vocabulary, which builds one float32 copy of them where `log_softmax` then `gather` builds two.
Since a batch's rows times `keep` is at most `batch_tokens`, the logits hold at most
`batch_tokens` times the vocabulary size: about 6.6 GB in bfloat16 for OLMo 2's 100k vocabulary
at the default budget, plus 13 GB for the float32 copy. Tokenizing a call's requests is one batched tokenizer
call for the joint context-plus-continuation strings and one for the distinct contexts, so a
few-shot context repeated across choices is tokenized alone once. Index tensors are built on the
host before the forward pass, and per-request sums are written into one tensor on the device, so
our code reads nothing from the GPU until the task's mean metrics are computed. transformers'
own mask handling may still sync once per batch (its SDPA path checks the padding mask on the
host).
With weights split across devices, the targets move to the device the logits come out on.

Weights load in bfloat16, and log-softmax runs in float32. The model is switched to eval mode
for scoring and each module is put back in the mode it was in, so a training loop can score between steps.

### D4. Metrics are pure functions of `[docs, choices]` tensors

`metrics.multiple_choice` takes a `ChoiceScores` (every doc's choices padded to the widest doc,
with a `valid` mask) and returns per-doc tensors. Names and definitions match OLMES's
`MCAccuracy`:

| Metric | Score per choice | Notes |
| --- | --- | --- |
| `acc_raw` | summed log-probability | |
| `acc_per_token` | log-probability / tokens | count at least 1 |
| `acc_per_char` | log-probability / characters | leading space counted (OLMES sec. 3.5) |
| `acc_per_byte` | lowest bits per byte | |
| `acc_uncond` | log-probability minus log-probability after `"Answer:"` | PMI (OLMES sec. 3.3) |
| `bpb` | the gold choice's `-logprob / bytes / ln 2` | OLMo 3 sec. 3.3.2 |

Ties go to the first choice, as `list.index(max(...))` does in OLMES.

### D5. A task is a frozen dataclass plus one function

`MultipleChoiceTask` holds a `Dataset` (repo, commit, subset, split), a `to_doc` function,
optional `Fewshot` ids, the primary metric, and the prompt strings. Rendering is shared by every
task:

```text
Question: <shot question>\nAnswer: <shot gold>\n\n ... Question: <question>\nAnswer:
```

The continuations are `" <choice>"`. PMI adds one request per choice with the context
`"Answer:"`. Adding a task means writing a `to_doc` and a definition, then registering it in
`tasks/registry.py`.

Few-shot examples are ids into a split of the same dataset, not copied text. ARC-Easy uses the
five curated OLMES examples (`OLMES:ARC-Easy`), which are all in the train split at the pinned
revision.

### D6. ARC-Easy scores the full test split

`arc_easy` scores all 2376 test docs, as OLMo 3's Base Easy does (`arc_easy:rc::olmes:full`).
OLMES itself samples 1000 docs with `Random(1234)`. Comparing against that sample means
scoring the same 1000 docs, which belongs to the parity stage. The primary metric is
`acc_per_char` (OLMES Table 2). `bpb` is in the same results, so Base Easy needs no second task.

`arc_doc` finds the gold index with `labels.index(answerKey)`. This agrees with OLMES's
digit-to-letter mapping on all 2376 test docs.

### D7. The task hash covers the task's definition and what was scored

`MCRequests.hash` is the sha256 of the task's data fields (everything but `to_doc`, so the name
and primary metric too), every (context, continuation) string, and every gold index. A changed
prompt, few-shot set, dataset revision or `to_doc` that changes any string or label changes the
hash. Two results with the same hash scored the same requests under the same task definition. A
renamed task gets a new hash on purpose: a number scored differently is a different task.

### D8. One results file per run, with provenance

`results.json` (schema version 2, which added `max_length`) holds each task's mean metrics, doc
count, wall time and throughput, plus a `Provenance`:
- the model as `repo@commit`. Branches and tags are resolved to a commit through the Hub, and
  a run fails if the Hub reports none. A full commit hash is used as is, so pinned models load
  offline from the cache;
- the flock commit, from `FLOCK_COMMIT` (job scripts set it) or `git rev-parse HEAD` with a
  `-dirty` suffix;
- the command line, device name, dtype, batch budget, `max_length` (the left-truncation
  window, which can change scores), the attention implementation (which changes bfloat16 scores), and the torch, transformers and Python versions.

`predictions/<task>.jsonl` keeps each doc's per-choice log-probabilities and token counts, so
two runs can be compared doc by doc.

Nothing in scoring is random, so there are no seeds to record.

### D9. One entry point for the CLI, job runners and training

`run.evaluate(EvalConfig) -> Results` loads the model and runs tasks. `EvalConfig` is a frozen
dataclass of plain fields, so it pickles to a remote worker. `run.score_task(ScoringModel, task)`
scores an already loaded model, which a training loop can call between steps. `flock-eval run`
is a thin argparse layer over `evaluate`, registered as a console script in the root
`pyproject.toml`. The root project is the only package that builds a wheel, since the member
`pyproject.toml` files only declare dependencies.

### D10. Tracker logging is optional

With `--wandb-project`, `tracking.log_to_tracker` logs one `eval` run through the `wandb` SDK:
the provenance and task hashes as config, and `<task>/<metric>` as summary. The SDK's own login
decides the host. The results file is always written.

---

## 3. Plan

1. Add `torch`, `transformers`, `datasets` and `wandb` to `flock-eval`. Check that torch's
   CUDA 13.0 wheels for Python 3.14 run on the cluster's driver.
2. `requests.py`, `model.py`: requests, encoding, batching, scoring.
3. `metrics.py`: normalizations and bits per byte.
4. `tasks/`: `Dataset`, `Fewshot`, `MCDoc`, `MultipleChoiceTask`, `arc.py`, `registry.py`.
5. `results.py`, `run.py`, `tracking.py`, `cli.py`.
6. CPU tests with a tiny random Llama and a byte-level BPE tokenizer trained in the test.
7. Smoke test: OLMo 2 1B on ARC-Easy on one H200.
8. User docs: introduction and quickstart.

---

## 4. Tests

`tests/eval/` builds a 2-layer, 32-wide Llama and a 300-token byte-level BPE tokenizer (no
BOS, like OLMo 2) in the test. Nothing is downloaded: dataset rows come from a fixture.
- **Scoring** matches an unbatched, unpadded forward pass per request, including Traditional
  Chinese strings and an empty context.
- **Batch-size and padding invariance:** budgets from 1 token (one request per batch) to
  10,000 give the same scores within 1e-4. Reversing the request order reverses the scores.
- **The boundary:** context and continuation tokens concatenate to the joint tokenization, and
  trailing context whitespace moves to the continuation.
- **Edge cases:** empty continuations and contexts, a tokenizer with no BOS or EOS, left
  truncation, over-long continuations, and the model's train mode being restored.
- **Metrics:** bits per byte against a hand value (`" 玉山"` is 7 bytes), each normalization
  on a case where it changes the winner, tie-breaking, and padded choices.
- **Tasks:** a `primary_metric` the task doesn't report is rejected, the exact few-shot prompt, request layout with 3 and 4 choices, character and
  byte counts with multi-byte text, and a hash that is stable and changes with each input.
- **End to end:** `evaluate` and the CLI write `results.json` and predictions with the expected
  fields.

The tests pin torch to one CPU thread. On a shared machine, more threads made the suite 50x
slower.

---

## 5. How it was actually built

- **Toolchain.** torch 2.14.1+cu130 was already in the lockfile through `flock-training`, and
  transformers 5.20.1 resolves on Python 3.14. CUDA works on the cluster's driver (580.65,
  CUDA 13.0).
- **`device_map` loading.** `from_pretrained` loads the weights straight onto the device with
  `device_map`, which avoids a CPU copy and satisfies ty's types. It needs `accelerate`,
  which the first smoke job showed (the CPU tests never call `from_pretrained`).
- **Left padding checked on the GPU.** On 300 ARC-Easy docs (2398 requests) with OLMo 2 1B on
  an H200, batched scores match an unpadded forward per request within 1.8e-4 nats in float32,
  at budgets of 32768 and 512 tokens. In bfloat16 the same comparison differs by 0.05 nats on
  average and 0.49 at most: bfloat16 results depend slightly on which requests share a batch,
  so runs compared with each other use the same `batch_tokens` (recorded in the provenance).
- **Intermediate commits.** The tests import the whole package, so only the branch's last
  commit passes the test gate on its own.
