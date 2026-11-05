# Metrics

Every multiple-choice task reports the metrics of [OLMES](https://arxiv.org/abs/2406.08446) (sec. 3.3) and the bits per byte of [OLMo 3](https://arxiv.org/abs/2512.13961) (sec. 3.3.2). [`~eval.metrics.multiple_choice`] below lists each metric and how it scores a choice.

A gold-only task, such as math, code or LAMBADA, has one choice per doc, so it reports `bpb` and, when it asks for it, `acc_greedy`: whether greedy decoding would produce the gold text. A multiple-choice task refuses `acc_greedy`, since greedy match compares no choices.

`bpb` doesn't depend on the tokenizer, which makes it the fairest comparison between models with different vocabularies. Lower is better.

## Perplexity

A [`~eval.tasks.PerplexityTask`] reports three metrics, each a ratio of sums over all of the task's docs, not a mean over docs, so a long doc counts by its length. Lower is better for all three.

| Metric | Formula |
|---|---|
| `bits_per_byte` | the summed log-probability of all docs, negated and divided by their total UTF-8 bytes, in base 2 (Paloma, App. B) |
| `loss_per_token` | the summed log-probability of all docs, negated and divided by their total tokens, in nats: the token-averaged cross-entropy a trainer reports as its loss |
| `perplexity` | `exp(loss_per_token)`, or infinity if that overflows a float (above about 709.78 nats), which `results.json` writes as `null` |

Every token is predicted exactly once (see [Scoring behavior](scoring#scoring-a-whole-text)), so the token count is the docs' own token count, without the prefix token. `loss_per_token` and `perplexity` depend on the tokenizer, so they compare models only when the models share one. `bits_per_byte` doesn't. The task's `primary_metric` says which one to read first: `bits_per_byte` for Paloma, `perplexity` for `fineweb_edu` and the `fineweb` members. The `fineweb` suite still averages their `bits_per_byte`: see [Suites](tasks#suites). Multiple-choice `bpb` is a mean over docs, which is why it has a different name and never shares a suite with `bits_per_byte`.

## Functions

[[autodoc]] eval.metrics.multiple_choice

[[autodoc]] eval.metrics.accuracy

[[autodoc]] eval.metrics.bits_per_byte

[[autodoc]] eval.metrics.ChoiceScores
