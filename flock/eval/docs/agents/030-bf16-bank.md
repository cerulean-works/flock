# 030: BF16 regression bank and refreshed FP32 banks

| | |
| --- | --- |
| Status | building |
| Date | 2026-11-06 |
| Builds on | 013 score comparison, 018 refreshed bank, 021 changed suites, 023 SDPA pin |
| Keeps | Every task, request, metric, comparison tolerance and kernel refusal |

## 1. Goal

Bank OLMo 2 1B in BF16 on Base Easy with the efficient SDPA kernel. Refresh the Base Easy and Paloma FP32 banks so their provenance records the kernel and current schema.

**Out of scope:** changing comparison tolerances or the FP32 CPU against CUDA refusal.

## 2. Design decisions

### 030.D1. A BF16 bank uses the pinned efficient SDPA kernel

Stage 023 found repeatable BF16 scores on one GPU type and library stack. Measure this over all Base Easy tasks on two different H200 nodes. The acceptance rule is zero comparison breaches under the existing tolerances. Also report prediction byte equality per task and the maximum score difference for every task that differs. These are our regression checks, with no external harness precedent, as stage 013 records.

### 030.D2. FP32 banks are refreshed with the same scoring commit

Both FP32 banks predate kernel provenance and schema 8. Compare each old bank with its refresh before replacing it. Require zero breaches and unchanged task and suite hashes, and record provenance changes. Keep each bank's batch budget of 32768 and the pinned OLMo 2 revision. Store the complete results files unchanged.

### 030.D3. The kernel refusal remains unchanged

Stage 023 refuses different recorded kernels in every dtype. Whether FP32 CPU and CUDA should compare is left for a separate decision. This stage adds no scoring path or task.

## 3. Plan

1. Check kernel refusal tests and bank provenance.
2. Score BF16 Base Easy twice on different healthy H200 nodes.
3. Score FP32 Base Easy and Paloma at the same commit.
4. Compare, inspect prediction equality, refresh banks and run the CI gates.

## 4. How it was actually built

The branch starts at 8069e25. Kernel refusal and missing-kernel compatibility already have tests. The banks and measurement results are pending.

## 5. Known gaps

The repeatability check covers one model, GPU type and library stack. It does not establish cross-stack BF16 byte equality.
