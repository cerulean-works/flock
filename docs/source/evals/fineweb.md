# Scoring held-out web text in three languages

The `fineweb` suite scores a model on held-out web text in English, Traditional Chinese and Simplified Chinese, in windows of 2048 tokens. This page says what each member is, how to run it, how to read its numbers for a forecast, and which documents your training data must leave out.

## The members

| Task | Documents | Source | Dataset |
|---|---|---|---|
| `fineweb_en` | 5,000 | FineWeb-Edu, sampled from one file of its newest dump | `cerulean-works/fineweb-edu-dev`, split `validation` |
| `fineweb_zh_hant` | 3,383 | FineWeb-2's `cmn_Hani` `test` split, every Traditional document | `cerulean-works/fineweb-2-zh-dev`, configuration `zh_hant`, split `test` |
| `fineweb_zh_hans` | 5,000 | the same split, Simplified documents sampled with a fixed seed | `cerulean-works/fineweb-2-zh-dev`, configuration `zh_hans`, split `test` |

Each member is a [`~eval.tasks.PerplexityTask`]: every document is scored whole, in disjoint windows, as [Scoring the FineWeb-Edu dev set](fineweb-edu#what-the-number-is) describes. Each reports `bits_per_byte`, `loss_per_token` and `perplexity`, and `perplexity` is its primary metric.

FineWeb-2 keeps all Chinese under one language code, `cmn_Hani`. Its documents were split by script with the `hanzidentifier` library, applied to each whole text. A document that mixes characters unique to each script was left out of both members. `fineweb_zh_hant` is Traditional Chinese web text wherever it was written, not text from Taiwan. The English member is FineWeb-Edu, which keeps only pages an educational-quality classifier rates highly, while the Chinese members are FineWeb-2 with no such filter. Part of any gap between English and Chinese comes from that filter.

## Why a window of 2048

`fineweb_edu` uses windows of 8192 tokens, so a model with a shorter context can't run it. The `fineweb` members use 2048, which every model the project compares supports. A different window gives a different number, so `fineweb_en` is a task of its own rather than a setting of `fineweb_edu`, even though it reads the same documents. Compare `fineweb_en` with `fineweb_en`, never with `fineweb_edu`.

## Run the suite

```bash
uv run flock eval run \
  --suite fineweb \
  --model your-org/your-model@<revision> \
  --dtype float32 \
  --out results/fineweb
```

`cerulean-works/fineweb-2-zh-dev` is private to the `cerulean-works` organization on the Hugging Face Hub. Every process that loads it, including a training job, needs `HF_TOKEN` set to a token of a member.

The suite's score is the mean of the three members' `bits_per_byte`. Bits per byte divides by UTF-8 bytes rather than tokens, so it compares models with different tokenizers. It doesn't make the languages equal: a Han character is three UTF-8 bytes and most English characters one. Each member counts once, so Chinese carries two thirds of the suite's score. Use the score to rank models, not to compare languages, and read the members for each language.

## Read the members for a forecast

To forecast a larger run from small ones, read each member's `loss_per_token` (nats) in `results.json`. It is the token-averaged cross-entropy over all of the member's documents, the quantity a scaling fit uses (see [Metrics](metrics)). Read it per language: the three members differ in how much of each language a model saw, and their mean hides that. `loss_per_token` and `perplexity` compare only models that share a tokenizer. Across tokenizers, use `bits_per_byte`.

To log these numbers while training, prepare each member once and score it every N steps, as [Log `eval/loss` while training](fineweb-edu#log-evalloss-while-training) shows for `fineweb_edu`:

```python
from flock.eval.model import FlockCausalLM, ScoringModel
from flock.eval.run import prepare_task, score_prepared
from flock.eval.tasks import SUITES

# At startup, with `tokenizer` loaded as on the FineWeb-Edu page. One 2048-token window of padded
# tokens per forward pass keeps the logits small next to the training state.
scorer = ScoringModel(FlockCausalLM(model), tokenizer, name="my-run", batch_tokens=2048)
prepared = {task.name: prepare_task(scorer, task) for task in SUITES["fineweb"].tasks}

# Every N steps: one loss per language.
for name, prepared_task in prepared.items():
    result = score_prepared(scorer, prepared_task)
    run.log({f"eval/{name}/loss": result.metrics["loss_per_token"]}, step=step)
```

A prepared task keeps its documents' token ids in host memory for as long as you hold it. If memory is tight, prepare only the members you log.

## Leave the dev sets out of training

- **English.** Drop every FineWeb-Edu document whose `id` is in `dev-ids.txt` of `cerulean-works/fineweb-edu-dev`, the same list as for `fineweb_edu`.
- **Chinese.** Training data must not include FineWeb-2's `test` split, which its dataset card says "should not be trained on". FineWeb-2's `train` split doesn't contain it, but the copy in `s3://flock-datasets/fineweb-2` (version 1) holds both splits: keep only its rows whose `_hf_split` is `train`. The ids of the documents scored here are in `dev-ids-zh_hant.txt` and `dev-ids-zh_hans.txt` of `cerulean-works/fineweb-2-zh-dev`.

Dropping ids removes the documents themselves, not their near-duplicates elsewhere in the corpus. The dataset cards record each set's source revision and size, the seed of each sampled set, and the script classifier's version.
