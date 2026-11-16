# 037: preserve long-document training targets

| | |
| --- | --- |
| Status | implemented and smoke-tested |
| Builds on | the modular Trainer and its resumable batch sampler |
| Keeps | existing text-dataset behavior, model interfaces and shifted labels |

## Goal

Keep the text beyond the context length as additional training items. Exclude packing,
changes to the source mix, and changes to evaluation scoring.

## Design decisions

### 037.D1. Cache document tokenization before sampling chunks

A local Lance cache preserves source metadata and tokens, pinned to version 1.
The chunk index maps each sampler item to a document and token offset. Batch size counts
chunks, so long documents cannot silently enlarge a batch. Preparation never overwrites
an existing cache. The source URI, version and tokenizer rules identify the tokenization.
The context length identifies the sampling layout in checkpoints.

### 037.D2. One context token overlaps adjacent chunks

An item contains at most 4096 IDs, with arithmetic valid for any length of at least two.
The last ID becomes the next chunk's context. Every original target appears once; tails
with one target survive. No special tokens are inserted between chunks. Short documents
are not packed. Document exclusions and deduplication happen before caching.

OLMo 2 (2501.00656, Table 3) uses context 4096; its loader slices a token stream into
blocks. DCLM (2406.11794, Appendix F) packs documents with EOS at 2048. Those are different
from isolated-document overlapping chunks. The overlap policy is a local design choice.

## Plan

Build the cache and index, adapt collation, record layout identity in checkpoints, and
check exact targets, bounded batches and resumed weights before a GPU smoke.

## How it was actually built

The existing sampler and optimizer loop are reused unchanged. The collator pads cached
IDs without re-tokenizing or truncating. Tests compare every adjacent input-target pair
with each original document and compare interrupted training with uninterrupted training.
A full source replay retained every target at both tested context lengths; the GPU smoke
matched its independently counted targets. Review added special-token splitting to the
cache fingerprint so a changed tokenizer policy cannot silently reuse a checkpoint.

## Known gaps

The cache consumes additional local disk and must be prepared before training. It is
private data and is not uploaded. A different context changes the number of steps and
requires a new schedule and fresh run. Stage and decision numbers are provisional until
merge order is known.
