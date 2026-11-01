# Compare with the regression bank

Rerun the banked model and check whether an evaluation change moved its scores.

## Before you start

Use an installed checkout and a GPU machine with enough memory for OLMo 2 1B in float32. This is a full benchmark run, not a short smoke test. The bank is `tests/eval/regression/olmo-2-0425-1b-float32.json`.

## Run the check

1. Score the same pinned model and suite into a new or empty directory:

   ```bash
   uv run flock eval run \
     --model allenai/OLMo-2-0425-1B@a1847dff35000b4271fa70afc5db10fd29fedbdf \
     --suite base_easy \
     --dtype float32 \
     --out results/regression
   ```

2. Compare the output with the bank:

   ```bash
   uv run flock eval compare \
     tests/eval/regression/olmo-2-0425-1b-float32.json \
     results/regression/results.json
   ```

3. Check the exit status and printed breaches. Exit `0` means the compared values are within tolerance; `1` means a score breach; `2` means the comparison was refused.

A refusal requires checking the task definitions, run settings or file validity before interpreting scores. See [Comparison rules](comparison) for the exact checks and tolerances. A task present in only one file is reported separately, so check the coverage as well as the exit status.

## Refresh the bank after an intentional change

1. Run the comparison above and explain every expected difference.
2. Copy the new `results.json` unchanged over the banked file:

   ```bash
   cp results/regression/results.json tests/eval/regression/olmo-2-0425-1b-float32.json
   ```

3. Include the comparison output and reasons in the change review. Keep a library-driven bank refresh in the same change as its lockfile update.

Do not loosen tolerances to conceal a changed task or different evaluation settings. For deliberately changed tasks, record and review a new baseline.
