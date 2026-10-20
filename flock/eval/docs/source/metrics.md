# Metrics

Every multiple-choice task reports these metrics, following [OLMES](https://arxiv.org/abs/2406.08446) (sec. 3.3) and [OLMo 3](https://arxiv.org/abs/2512.13961) (sec. 3.3.2). Each accuracy picks the choice with the best score, and ties go to the first choice.

| Metric | How a choice is scored |
|---|---|
| `acc_raw` | Its summed log-probability. |
| `acc_per_token` | Log-probability divided by its token count. |
| `acc_per_char` | Log-probability divided by its character count, including the leading space. |
| `acc_per_byte` | Fewest bits per UTF-8 byte wins. |
| `acc_uncond` | PMI: log-probability minus its log-probability after just `Answer:`. Skipped for tasks whose `unconditional_context` is `None`. |
| `bpb` | Not an accuracy: the bits per UTF-8 byte of the gold choice. Lower is better. |

Counts are at least 1, so an empty count never divides by zero. `bpb` doesn't depend on the tokenizer, which makes it the fairest comparison between models with different vocabularies.

## Functions

[[autodoc]] eval.metrics.multiple_choice

[[autodoc]] eval.metrics.accuracy

[[autodoc]] eval.metrics.bits_per_byte

[[autodoc]] eval.metrics.ChoiceScores
