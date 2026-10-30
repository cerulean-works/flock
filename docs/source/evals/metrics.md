# Metrics

Every multiple-choice task reports the metrics of [OLMES](https://arxiv.org/abs/2406.08446) (sec. 3.3) and the bits per byte of [OLMo 3](https://arxiv.org/abs/2512.13961) (sec. 3.3.2). [`~eval.metrics.multiple_choice`] below lists each metric and how it scores a choice.

A gold-only task, such as math, code or LAMBADA, has one choice per doc, so it reports `bpb` and, when it asks for it, `acc_greedy`: whether greedy decoding would produce the gold text. A multiple-choice task refuses `acc_greedy`, since greedy match compares no choices.

`bpb` doesn't depend on the tokenizer, which makes it the fairest comparison between models with different vocabularies. Lower is better.

## Functions

[[autodoc]] eval.metrics.multiple_choice

[[autodoc]] eval.metrics.accuracy

[[autodoc]] eval.metrics.bits_per_byte

[[autodoc]] eval.metrics.ChoiceScores
