# Introduction

`flock.eval` scores language models on benchmark tasks with one fixed instrument: the same prompts, few-shot examples, tokenization and metrics for every model and checkpoint. Its numbers are comparable across our pretraining runs and the baselines we measure them against.

## What it does

- **Scores multiple-choice tasks by log-likelihood.** Each answer choice is a continuation of the question, and the model's log-probability ranks the choices. This is the cloze formulation of [OLMES](https://arxiv.org/abs/2406.08446), which OLMo 3 calls "rank choice" (RC).
- **Reports every OLMES normalization and bits per byte.** Accuracy with raw, per-token, per-character, per-byte and PMI scores, plus the bits per UTF-8 byte of the gold answer, which [OLMo 3](https://arxiv.org/abs/2512.13961) uses for its Base Easy suite. Bits per byte doesn't depend on the tokenizer, so models with different vocabularies compare fairly.
- **Pins everything it reads.** Models and datasets are loaded at a fixed revision, and every results file records the model commit, the flock commit and a hash of exactly what was scored.
- **Runs on the GPU in batches.** Requests are sorted by length and packed into forward passes of a fixed token budget, and scores stay on the device until the end of a task.

## Concepts

| Term | Meaning |
|---|---|
| task | A benchmark scored one way, such as `arc_easy`. A task is a short definition: a pinned dataset, a function from a dataset row to a question, and few-shot examples. |
| doc | One question of a task, with its answer choices and the index of the correct one. |
| request | One (context, continuation) pair whose log-probability the model computes. A doc with four choices makes four requests, and four more for PMI. |
| task hash | The sha256 of a task's definition and every string it scores. Two results with the same task hash scored the same requests. |

## Where to go next

- Score a model and add a task: [Quickstart](quickstart).
