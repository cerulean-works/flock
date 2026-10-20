# How scoring works

This page explains how a request becomes a score, and which settings can move a score. It follows the reference code of [OLMES](https://github.com/allenai/olmes), so numbers can be checked against it.

## Tokenizing a request

A request is a context and a continuation, and its score is the summed log-probability of the continuation's tokens. The boundary between them is drawn the way OLMES draws it:

1. Whitespace at the end of the context moves to the start of the continuation.
2. The context and the continuation are tokenized together. The continuation's tokens are the ones after the context's own tokenization, so a token that spans the boundary counts as continuation.
3. No special tokens are added. An empty context becomes the BOS token, or EOS if the tokenizer has no BOS.
4. An input longer than the model's `max_length` loses tokens from the left, so the context is cut and the continuation is scored in full.

A continuation with no tokens raises an error, since scoring it as 0 would rank it above every real choice. So does a continuation longer than `max_length` on its own.

## Batching and precision

Requests are sorted longest first and packed into batches of at most `batch_tokens` padded tokens (32768 by default). Rows are left-padded, so every continuation ends at the last position. The model then computes logits only for the continuation positions, never for the few-shot context, which keeps memory at about `batch_tokens` times the vocabulary size.

Log-softmax runs in float32, whatever the weights' dtype. In float32, a batched score matches the same request scored alone within 2e-4 nats. In bfloat16, the default for [`~eval.model.ScoringModel.from_pretrained`], a score depends slightly on which requests share its batch: about 0.05 nats on average for OLMo 2 1B on ARC-Easy, and 0.49 at most.

So compare runs only when they used the same:
- `batch_tokens` and `limit`, which decide the batches
- dtype and attention implementation, which change bfloat16 arithmetic
- `max_length`, which decides where long inputs are cut

`results.json` records all of them in its provenance. See [Results files](results#provenance).

## Metrics on the device

Scores stay on the GPU while a task runs. The metrics are tensor operations over every doc's choices at once, and nothing is copied to the host until the task's means are computed. [Metrics](metrics) defines each one.
