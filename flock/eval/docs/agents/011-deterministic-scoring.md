# 010: bfloat16 scores that repeat run to run

| | |
| --- | --- |
| Status | design, for review before any code |
| Date | 2026-10-27 |
| Builds on | [001](001-eval-core.md) (batched scoring, provenance), [008](008-paloma.md) (the rolling path) |
| Keeps | float32 scores, every parity result, `results.json`'s existing fields |
| Result | SDPA pinned to the memory-efficient kernel while flock scores on CUDA, and the kernel recorded in the provenance |

## 1. Goal

Scoring one model twice at one flock commit in bfloat16 does not give the same numbers. On ARC-Easy with OLMo 2 1B, 386 of 19,002 requests moved between two runs, by up to 0.28 nats, and acc_per_char moved 0.04 pt. The cause is PyTorch's scaled dot product attention (SDPA). For most padded batches it dispatches to cuDNN attention, and only those batches move. Restricting SDPA to its memory-efficient kernel gave bit-identical runs, and that setting was the fastest bfloat16 one measured. With every scoring forward pass pinned, a before/after check on the GPU can claim byte equality in bfloat16, which today holds only in float32.

**Out of scope:** float32 behaviour, the bfloat16 drift from float32 (a separate noise floor), FlashAttention 3 and packed sequences (the TODO in `_score`).

---

## 2. Evidence

Every number is OLMo 2 1B (`allenai/OLMo-2-0425-1B@a1847dff`) in bfloat16 on one H200, torch 2.14.1+cu130, cuDNN 9.24, transformers 5.20.1. Each setting ran twice in its own process, alternating default, efficient, default, efficient within one job, and speed compares each setting's faster run.

| Task (flock commit) | Requests | Differ run to run: default / efficient | Differ default vs efficient | docs/s, faster run: default / efficient |
| --- | --- | --- | --- | --- |
| ARC-Easy (`8725220`) | 19,002 | 1,385 / **0** | 15,889, up to 0.49 nats | 156.0 / 174.3 |
| MedQA-EN (`a48a53a`) | 6,365 | 215 / **0** | 6,365, up to 0.45 nats | 22.2 / 23.3 |
| SciRIFF yes/no (`a48a53a`) | 6,328 | 0 / **0** | 3,164, up to 0.12 nats | 26.8 / 26.7 |
| Paloma `wikitext_103`, window 2048 (`c1c43e2`) | 61 docs | 0 / **0** | 61 docs, bits per byte 0.706505 to 0.706527 | 17.2 / 17.1 |
| ARC-Easy in float32 (`8725220`) | 19,002 | 0 / 0 | **0** | 27.9 / 27.8 |

MedQA-EN and SciRIFF yes/no are the Base Easy tasks with the longest inputs (SciRIFF is the only one whose contexts reach the 4096-token limit). Paloma's windows are full and unpadded. The efficient kernel was bit-identical run to run on every task. It was faster on the padded short inputs, 5% faster on MedQA-EN, and within 1% on SciRIFF and Paloma. On SciRIFF and Paloma the default was also repeatable, but on a different kernel, so the pin still moves their scores. In float32 the default already runs the efficient kernel on every batch: the two settings are byte-equal.

Sources:

- PyTorch's reproducibility notes, "CUDA Scaled Dot Product Attention" (https://docs.pytorch.org/docs/2.14/notes/randomness.html), say of cuDNN attention: "This backend is disabled when torch.use_deterministic_algorithms(True, warn_only=False) is set, regardless of whether inputs require gradients or the call is inside a torch.no_grad() context." The same table lists every backend's forward pass as deterministic, which the cuDNN runs above contradict. Our measurement wins, and the rule below does not depend on the table.
- The same page: "Bitwise matching numerics across different SDPA backends are not guaranteed, even for the same inputs and dtype." So any pin changes bfloat16 scores once.
- `torch.nn.attention.sdpa_kernel` (https://docs.pytorch.org/docs/2.14/generated/torch.nn.attention.sdpa_kernel.html): "Upon exiting the context manager, the previous state of the flags will be restored, enabling all backends."
- No evaluation framework pins the backend. OLMES and lm-evaluation-harness leave it to PyTorch.

---

## 3. Design decisions

### 010.D1. The pin wraps the scoring forward passes, entered and left on every call

`ScoringModel` enters `sdpa_kernel(SDPBackend.EFFICIENT_ATTENTION)` in the same `with` as `_eval_mode`, around the batch loop. Today that is `loglikelihood`. Once the rolling path lands, it is `_logprobs`, which both paths share, so the pin is written once. Since `sdpa_kernel` restores the previous flags on exit, a training loop that calls `score_task` between steps gets its own SDPA choice back for the next step. Nothing is set at import time or globally.

Known limit: SDPA's backend flags are process-wide, not per thread. A training step running on another thread while flock scores would see the pin. Flock's training loop scores between steps on one thread, so this does not arise today.

### 010.D2. The pin applies on CUDA with SDPA attention, and a model the efficient kernel can't run fails loudly

The pin applies when the model is on a CUDA device and its attention implementation is `sdpa`. Elsewhere scoring runs as it does today:

- **CPU.** PyTorch has no memory-efficient kernel on the CPU: pinned to it, SDPA raises "No viable backend for scaled_dot_product_attention was found." The CPU has no cuDNN attention either, so there is nothing to pin away. CI's tests run here, and a test states that CPU scores are unchanged.
- **`eager` or another attention implementation.** SDPA is never called, so the pin would do nothing.

If the efficient kernel can't run a model on CUDA (an unsupported head size, dtype or mask), PyTorch raises and the run stops. There is no fallback to PyTorch's choice. A silent fallback would let two models in one comparison be scored by different kernels, and the scores move when the kernel changes. The error names the reason, and a model that needs another kernel is a change to this decision, made in code and recorded.

### 010.D3. The provenance records the kernel as `sdpa_backend`

`Provenance` gains `sdpa_backend: str | None`: `"EFFICIENT_ATTENTION"` (PyTorch's `SDPBackend` name) when the pin applied, `None` when it didn't (CPU, or attention other than SDPA). `ScoringModel.sdpa_backend` gives the same value, so `evaluate` and `score.py`-style scripts record it the same way. `results.json` goes to schema 5, so a file without the field is known to come from before the pin, when PyTorch chose per batch. `results.md` documents the field and `how-scoring-works.md` lists the kernel among the settings that change bfloat16 scores.

### 010.D4. No option to unpin

Neither `flock-eval run` nor `ScoringModel` gets a switch. A different kernel gives different bfloat16 numbers, so a run with another kernel is a different instrument, and an option would make it easy to compare two runs that differ only in a flag. Pre-pin numbers come from an older flock commit, which the provenance names.

### 010.D5. bfloat16 scores change once, float32 scores don't

Pinning moves bfloat16 numbers wherever PyTorch picked another kernel: ARC-Easy acc_per_char from 0.7677 to 0.7673, MedQA-EN 0.2804 to 0.2781, SciRIFF yes/no 0.8559 to 0.8590, and Paloma's `wikitext_103` bits per byte from 0.706505 to 0.706527 (section 2). Results files from before the pin compare to later ones only within the bfloat16 noise floor, as before. In float32, PyTorch already picks the efficient kernel, and pinned and unpinned ARC-Easy predictions are byte-equal. Parity is judged in float32, so no parity result changes.

---

## 4. Plan

1. `model.py`: the pin in `_logprobs` (or `loglikelihood` before the rolling path), the `sdpa_backend` property.
2. `provenance.py`, `run.py`, `results.py`: the field, schema 5.
3. Tests (CPU): `sdpa_backend` is `None` on the CPU and CPU scores equal the unpinned ones; the SDPA flags after `loglikelihood` equal those before it; on CUDA, skipped in CI, the pin is active during the forward pass.
4. Docs: `how-scoring-works.md`, `results.md`.
5. Before/after on the GPU: ARC-Easy bfloat16 at `main` and at the branch, with the shift stated; two bfloat16 runs of the branch byte-equal; float32 byte-equal before and after.

---

## 5. How it was actually built

Not built yet.
