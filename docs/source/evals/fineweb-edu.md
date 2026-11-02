# Scoring the FineWeb-Edu dev set

The `fineweb_edu` task scores a model on a fixed set of 5,000 FineWeb-Edu documents and reports its perplexity. This page says exactly what that number is, how to compute it from an uploaded checkpoint or inside a training loop, and which documents your training data must leave out.

## What the number is

`fineweb_edu` is a [`~eval.tasks.PerplexityTask`] on the private dataset `cerulean-works/fineweb-edu-dev`, split `validation`. Each document is scored whole, as follows:

1. Tokenize the document's `text` with the model's tokenizer, with no special tokens.
2. Put one prefix token in front: BOS, or EOS if the tokenizer has no BOS. The prefix is input only, never a target.
3. Split the tokens into disjoint windows of 8192 predicted tokens. The first window predicts tokens 1 to 8192 from the prefix and the tokens before each one. Each later window predicts the next 8192 tokens (or what is left) from an input of exactly 8192 tokens, so it conditions on the 8192 tokens before its last target. Every token is predicted exactly once.
4. Add up the negative log-probability, in nats, of every predicted token of every document. Divide by the number of predicted tokens, which is the total token count of all documents.

That quotient is `loss_per_token`, the token-averaged cross-entropy. `perplexity` is `exp(loss_per_token)`, and it is the task's primary metric. The task also reports `bits_per_byte` over the documents' UTF-8 bytes. Documents are never packed together: no window holds tokens of two documents. See [Scoring behavior](scoring#scoring-a-whole-text) for how windows are batched.

The sum runs over all tokens before dividing, so a long document counts by its length. It is not a mean of per-document losses.

## Score an uploaded checkpoint

Score the model in FP32, which is the number to rank and report:

```bash
uv run flock eval run \
  --task fineweb_edu \
  --model your-org/your-model@<revision> \
  --dtype float32 \
  --out results/fineweb-edu
```

The run prints `perplexity`, and `results/fineweb-edu/results.json` holds all three metrics. The model needs a context of at least 8192 tokens: a model whose `max_length` is smaller is refused before scoring, with an error that names the task, the window and the model's `max_length`. If the full windows don't fit in memory, lower `--batch-tokens`; it changes how windows are batched, not which tokens are scored.

You need read access to `cerulean-works/fineweb-edu-dev` on the Hugging Face Hub.

## Log `eval/loss` while training

To score a checkpoint your training loop already holds, wrap the model and call [`~eval.run.score_task`], as [Evaluate a training checkpoint in memory](evaluate-checkpoint) shows:

```python
from flock.eval.model import FlockCausalLM, ScoringModel
from flock.eval.run import score_task
from flock.eval.tasks import TASKS

tokenizer = model.get_hf_tokenizer()
if tokenizer is None:
    raise ValueError("the model's config has no hf_repo, so it names no tokenizer")
scorer = ScoringModel(FlockCausalLM(model), tokenizer, name=f"my-run@step-{step}")
result = score_task(scorer, TASKS["fineweb_edu"])
run.log(
    {
        "eval/loss": result.metrics["loss_per_token"],
        "eval/perplexity": result.metrics["perplexity"],
    },
    step=step,
)
```

BF16 is fine for checks during training. The ranked number is the FP32 one.

If your trainer computes the loss itself, follow the four steps above. This function does it for one document with any model that maps a `[1, length]` tensor of ids to `[1, length, vocab]` logits:

```python
import torch


def document_loss(model, ids: list[int], prefix: int, window: int = 8192) -> tuple[float, int]:
    """The summed negative log-likelihood (nats) of one document's tokens, and their count."""
    total = 0.0
    for start in range(0, len(ids), window):
        end = min(start + window, len(ids))
        # The first window starts from the prefix token. A later one reads the `window`
        # tokens before its last target.
        inputs = [prefix, *ids[: end - 1]] if start == 0 else ids[end - window - 1 : end - 1]
        targets = torch.tensor(ids[start:end])
        logits = model(torch.tensor([inputs]))[0, -len(targets) :]
        logprobs = logits.float().log_softmax(-1)
        total -= logprobs.gather(-1, targets[:, None]).sum().item()
    return total, len(ids)
```

`loss_per_token` is the sum of the first values over all documents divided by the sum of the second.

## Leave the dev set out of training

The dev set's ids are its `id` column and `dev-ids.txt` in the dataset repo. Drop every FineWeb-Edu document whose `id` is in that list from your training data, so the set stays held out.

FineWeb-Edu has no validation split, so the set was drawn from the training corpus itself: 5,000 documents sampled uniformly with a fixed seed from one file of its newest dump, `CC-MAIN-2025-26`. FineWeb-Edu removes near-duplicates only within each dump, not across dumps. A dev document can therefore have a near-duplicate in another dump that dropping ids doesn't remove. Exclusion by id is what the set guarantees. The dataset card records the source revision, file, seed and size.
