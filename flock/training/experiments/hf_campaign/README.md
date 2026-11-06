# Standalone HF campaign CPU checks

This experiment uses Transformers OLMo 2, Datasets and Accelerate independently of the shared trainer. The selected full architecture is freshly initialized OLMo 2 1B: 1,484,916,736 parameters including both untied matrices. The CPU harness accepts only tiny synthetic models. The full architecture is instantiated on the meta device in tests.

From the flock checkout, create an isolated environment from the existing lockfile:

```sh
uv sync --all-extras --all-packages --dev --no-group gpu --group cpu-tests --frozen
CUDA_VISIBLE_DEVICES= RAYON_NUM_THREADS=4 OMP_NUM_THREADS=4 TOKENIZERS_PARALLELISM=false uv run --no-sync pytest -q tests/training/test_hf_campaign.py -n 0
```

The lock pins Transformers 5.20.1, Datasets 5.1.0, Accelerate 1.15.0 and torch 2.14.1. No shared environment or dependency file is changed. No model weights or remote dataset are downloaded by these tests.

## Input contract

Supply finite HF Dataset pools in immutable membership order, plus tokenizer, membership and exclusion fingerprints. Each admitted row supplies id, family_id, text, text_sha256, source_pin, split=train, admitted=true, rights_receipt and exclusion_receipt. Converted pools additionally require original_text, original_sha256 and conversion_receipt. The upstream producer must resolve source rights, reviewed conversion/protected spans, train-only eligibility, cross-script matching and canonical duplicate families. A receipt is a provenance reference, not an automatic legal or classification judgment.

The consumer rejects missing receipts, text/hash mismatches, duplicate families and configured held-out spans before encoding. A match rejects the whole document. The local forbidden-span hook is a synthetic exclusion oracle; it does not implement the full approved held-out normalization/matching pipeline. There is no production admission command. Long documents beyond the one-million-character/token fixture bound fail admission or stop; production segmentation and durable dedup indexes require the separate corpus gate.

Encode without automatic specials, then append one EOS per nonempty tokenization. Literal EOS tokens are retained. The initial stream token is context-only. Each next target has a stable pool/document/offset identity, including inserted EOS. A block contains aligned inputs and labels and carries its final token into the next block as context. Counts follow target source categories, separately tracking native/converted TC and EOS. Source categories do not assert actual token language.

Explicit finite quotas control source allocation. Equal TC/EN allocations are supplied per optimizer update; source selection uses exact rational exposure ratios at block boundaries. There is no implicit repetition, source substitution or runtime shuffle buffer. The supplied immutable order is responsible for shuffling. Exhaustion stops. Source weights and repetition limits for real data remain unresolved. The in-memory duplicate-family ledger is intended for bounded synthetic fixtures, not a production-scale dedup index.

## Loss, restart and export

The CPU runtime requests HF logits without labels and computes external aligned summed CE divided by the update's total targets. This avoids HF's automatic label shift. Token-based warmup/cosine scheduling uses committed update-start targets. Updates cannot overshoot the declared endpoint. A terminal partial allocation preserves exact equal category totals.

Only completed optimizer updates replace the committed data snapshot. Prefetched producer positions are discarded before the next update. Accelerate checkpoints model, optimizer and RNG; a registered custom object saves committed pack buffers, membership positions, source/EOS counts, context and schedule counters. New checkpoint directories are checksummed and fsynced before atomic rename; an existing incomplete directory is a writer lock requiring inspection. Config/tokenizer/stream/schedule mismatch or corruption fails before resume. After a failed optimizer update, terminate the attempt and restore a previously committed checkpoint; the harness does not promise in-memory model rollback.

HF export saves model/config/tokenizer. Tests reload locally, compare the identical attention backend and execute a synthetic request through unchanged flock.eval ScoringModel. This verifies the adapter boundary, not parity or scores on frozen evaluation datasets. Full evaluation remains a separate process with approved instrument pins and settings.

## Next gate

No production launcher, corpus materializer, distributed worker plan or FA3 dispatch is released here. These remain separate validation work: full-model memory, BF16/FA3 backward and causal checks, same-node throughput, distributed global-loss normalization, crash/durability tests on cluster storage and exact rank ownership. Pin the actual FA3 implementation and environment before GPU validation. Do not transplant historical throughput or budgets into this HF stack. The full 200B campaign needs its own data, metric, storage and compute release.
