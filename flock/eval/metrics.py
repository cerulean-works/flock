"""Metrics over scored answer choices, computed as tensor operations on the scores' device."""

import logging
import math
from dataclasses import dataclass

import torch

logger = logging.getLogger(__name__)

# Every name `multiple_choice` can return, in its order. Add a name here whenever it gains a
# metric: tasks check their `primary_metric` against this. `acc_uncond` is only reported by
# tasks that score pointwise mutual information, and `acc_greedy` only by tasks scored on their
# gold text alone (see `multiple_choice`).
MULTIPLE_CHOICE_METRICS = (
    "acc_raw",
    "acc_per_token",
    "acc_per_char",
    "acc_per_byte",
    "acc_uncond",
    "acc_greedy",
    "bpb",
)


def scale(metric: str) -> str:
    """Returns the scale a metric's scores average on: `accuracy` for every `acc_*` metric, otherwise the metric's own name, such as `bpb`. A mean over two scales, an accuracy (higher is better) and a `bpb` (lower is better), has no meaning."""
    return "accuracy" if metric.startswith("acc_") else metric


@dataclass(frozen=True, slots=True)
class ChoiceScores:
    """
    Every doc's answer-choice scores, padded to `[docs, max_choices]`.

    [`~eval.tasks.MCRequests.choice_scores`] builds it from the per-request [`~eval.requests.Scores`]. Docs with fewer choices than the widest doc have padded cells past their last choice. Those cells hold request 0's values, so every metric must mask them out with `valid`.

    Args:
        logprob (`torch.Tensor`):
            float32 `[docs, max_choices]`, each choice's summed natural-log probability given the doc's context.
        num_tokens (`torch.Tensor`):
            int64 `[docs, max_choices]`, each choice's token count.
        num_chars (`torch.Tensor`):
            int64 `[docs, max_choices]`, each choice's character count, including its leading space.
        num_bytes (`torch.Tensor`):
            int64 `[docs, max_choices]`, each choice's UTF-8 byte count, including its leading space.
        valid (`torch.Tensor`):
            bool `[docs, max_choices]`, False in the padded cells past a doc's last choice.
        is_greedy (`torch.Tensor`):
            bool `[docs, max_choices]`, whether greedy decoding would produce each choice's continuation.
        gold (`torch.Tensor`):
            int64 `[docs]`, the index of each doc's correct choice.
        tokens_cut (`torch.Tensor`):
            int64 `[docs, max_choices]`, the tokens cut from the left of each choice's context to fit the model, 0 if it fit.
        logprob_uncond (`torch.Tensor`, *optional*):
            float32 `[docs, max_choices]`, each choice's summed natural-log probability after the task's unconditional context instead of the question. Used for pointwise mutual information (PMI).
        tokens_cut_uncond (`torch.Tensor`, *optional*):
            int64 `[docs, max_choices]`, `tokens_cut` for the unconditional requests. Set with `logprob_uncond`.
    """

    logprob: torch.Tensor
    num_tokens: torch.Tensor
    num_chars: torch.Tensor
    num_bytes: torch.Tensor
    valid: torch.Tensor
    is_greedy: torch.Tensor
    gold: torch.Tensor
    tokens_cut: torch.Tensor
    logprob_uncond: torch.Tensor | None = None
    tokens_cut_uncond: torch.Tensor | None = None


def bits_per_byte(logprob: torch.Tensor, num_bytes: torch.Tensor) -> torch.Tensor:
    """
    Converts a summed natural-log probability to bits per UTF-8 byte: its negation, divided by the byte count, in base 2.

    This is the metric of OLMo 3's Base Easy suite (arXiv 2512.13961, sec. 3.3.2). Dividing by bytes instead of tokens makes it independent of the tokenizer. The paper doesn't name the log base. Bits follow the code of the Open Language Model Evaluation Standard (OLMES, arXiv 2406.08446), which multiplies nats per byte by `LOG_2_OF_E` (`oe_eval/metrics/metric.py`).

    Args:
        logprob (`torch.Tensor`):
            Summed natural-log probabilities, any shape.
        num_bytes (`torch.Tensor`):
            UTF-8 byte counts, the same shape. A count below 1 is treated as 1, with a warning.

    Returns:
        `torch.Tensor`: bits per byte, the same shape.
    """
    # One host copy per call. Every current caller passes non-empty continuations, so this only
    # catches a future caller that doesn't check its byte counts.
    below_one = (num_bytes < 1).sum().item()
    if below_one:
        logger.warning("%d byte counts are below 1; bits per byte treats them as 1", below_one)

    nats_per_byte = -logprob / num_bytes.clamp(min=1)

    return nats_per_byte / math.log(2)


def accuracy(score: torch.Tensor, valid: torch.Tensor, gold: torch.Tensor) -> torch.Tensor:
    """
    Checks, per doc, whether the highest-scoring valid choice is gold.

    Ties go to the first choice, as OLMES's `MCAccuracy` does with `list.index(max(...))` (`oe_eval/metrics/metric.py`).

    Args:
        score (`torch.Tensor`):
            `[docs, max_choices]`, higher is better.
        valid (`torch.Tensor`):
            bool `[docs, max_choices]`, False in padded cells, which are never picked.
        gold (`torch.Tensor`):
            int64 `[docs]`, the index of each doc's correct choice.

    Returns:
        `torch.Tensor`: float32 `[docs]`, 1.0 where the pick is gold, else 0.0.
    """
    predicted = score.masked_fill(~valid, -math.inf).argmax(dim=-1)

    return (predicted == gold).float()


def multiple_choice(scores: ChoiceScores) -> dict[str, torch.Tensor]:
    """
    Computes OLMES's multiple-choice metrics per doc (OLMES, sec. 3.3).

    Each metric picks the choice with the highest score, and they differ in how a choice is scored. Lengths count the choice's leading space (OLMES, sec. 3.5) and are at least 1, as OLMES's code clamps them with `max(..., 1)`.

    - `acc_raw`: the summed log-probability.
    - `acc_per_token`: the log-probability divided by the token count.
    - `acc_per_char`: the log-probability divided by the character count.
    - `acc_per_byte`: the log-probability divided by the byte count, which ranks choices as the lowest bits per byte does.
    - `acc_uncond`: pointwise mutual information (PMI), the log-probability minus the log-probability after the unconditional context. This rewards choices the question makes more likely, not choices that are likely anyway. Only set when `logprob_uncond` is.

    `acc_greedy` is 1 where greedy decoding would produce the gold continuation, OLMES's `greedy_acc` (`GreedyAccuracyMetric` in `oe_eval/metrics/metric.py`). It compares no choices, so it is only reported by tasks scored on their gold text alone, such as LAMBADA.

    `bpb` is the gold choice's bits per byte (see [`bits_per_byte`]), not an accuracy.

    Returns:
        `dict[str, torch.Tensor]`: float32 `[docs]` tensors, named as in `MULTIPLE_CHOICE_METRICS`.
    """
    bpb = bits_per_byte(scores.logprob, scores.num_bytes)
    per_token = scores.logprob / scores.num_tokens.clamp(min=1)
    per_char = scores.logprob / scores.num_chars.clamp(min=1)

    metrics = {
        "acc_raw": accuracy(scores.logprob, scores.valid, scores.gold),
        "acc_per_token": accuracy(per_token, scores.valid, scores.gold),
        "acc_per_char": accuracy(per_char, scores.valid, scores.gold),
        # Negated: `accuracy` picks the highest score, and lower bits per byte is better.
        "acc_per_byte": accuracy(-bpb, scores.valid, scores.gold),
    }

    if scores.logprob_uncond is not None:
        pmi = scores.logprob - scores.logprob_uncond
        metrics["acc_uncond"] = accuracy(pmi, scores.valid, scores.gold)

    # Each doc's gold column. Added last so the keys keep `MULTIPLE_CHOICE_METRICS`'s order,
    # which results.json follows.
    gold = scores.gold[:, None]
    metrics["acc_greedy"] = scores.is_greedy.gather(-1, gold).squeeze(-1).float()
    metrics["bpb"] = bpb.gather(-1, gold).squeeze(-1)

    return metrics
