# 030: BF16 regression bank and refreshed FP32 banks

| | |
| --- | --- |
| Status | in review |
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

- **Runs.** Four jobs at `74836d6`, each on one H200 with `allenai/OLMo-2-0425-1B@a1847dff35000b4271fa70afc5db10fd29fedbdf`, batch tokens 32768, max length 4096, no BOS and the efficient kernel. The files are schema 8. BF16 Base Easy scored in 32 min on `25a-hgpn033` and again in 32 min on `25a-hgpn034`. FP32 Base Easy took 2 h 58 min and FP32 Paloma 45 min.
- **BF16 repeatability.** `flock eval compare` of the two BF16 runs: 109 tasks and 10 suites compared, 0 breaches, no provenance difference besides `command` and `created_at`. All 109 prediction files are byte-equal, so no task needs a maximum difference. Every metric, hash, count and truncation field is equal at full precision.
- **FP32 refresh.** Against the old Base Easy bank: 109 tasks, 10 suites, 0 breaches. Against the old Paloma bank: 309 tasks, 10 suites, 0 breaches. Each prints `flock_commit`, `sdpa_backend  None  ->  EFFICIENT_ATTENTION` and `schema_version  6  ->  8`, and the new files also add `add_bos` (False) and, for Base Easy, `model_digest` (None), which `compare` does not print. Every task field and suite is equal at full precision, and the prediction files of the runs that wrote the old banks equal the new ones byte for byte (109 and 309 files).
- **Banks.** The three `results.json` files are copied unchanged to `tests/eval/regression/`. The bank test now covers the BF16 bank, checks each bank's dtype and kernel, and loads each provenance as written: every bank records `sdpa_backend` and `add_bos`, so the defaults it filled for older banks are gone. Since every bank now records the kernel, a CPU run is refused against them (030.D3); `compare-regression.md` lists the BF16 bank and says so.
- **Kernel refusal.** `test_compare.py` already covers the refusal on two recorded kernels and the comparison with a file that lacks the field; this stage keeps both.

## 5. Known gaps

The repeatability check covers one model, GPU type and library stack. It does not establish cross-stack BF16 byte equality.
