"""Metrics over scored answer choices, computed as tensor operations on the scores' device."""

import math
from dataclasses import dataclass

import torch

# Every name `multiple_choice` can return, kept next to it so the two change together.
# Tasks check their `primary_metric` against this. `acc_uncond` is only reported by tasks
# that score pointwise mutual information (see `multiple_choice`).
MULTIPLE_CHOICE_METRICS = (
    "acc_raw",
    "acc_per_token",
    "acc_per_char",
    "acc_per_byte",
    "acc_uncond",
    "bpb",
)


@dataclass(frozen=True, slots=True)
class ChoiceScores:
    """
    Every doc's answer choices, padded to `[docs, max_choices]`.

    Args:
        logprob (`torch.Tensor`):
            float32, each choice's summed log-probability given the doc's context.
        num_tokens (`torch.Tensor`):
            Each choice's token count.
        num_chars (`torch.Tensor`):
            Each choice's character count, including its leading space.
        num_bytes (`torch.Tensor`):
            Each choice's UTF-8 byte count, including its leading space.
        valid (`torch.Tensor`):
            bool, False where a doc has fewer choices than the widest doc.
        gold (`torch.Tensor`):
            int64 `[docs]`, the index of each doc's correct choice.
        logprob_uncond (`torch.Tensor`, *optional*):
            float32 `[docs, max_choices]`, each choice's summed log-probability after the unconditional context `"Answer:"` instead of the question. Used for pointwise mutual information (PMI).
    """

    logprob: torch.Tensor
    num_tokens: torch.Tensor
    num_chars: torch.Tensor
    num_bytes: torch.Tensor
    valid: torch.Tensor
    gold: torch.Tensor
    logprob_uncond: torch.Tensor | None = None


def bits_per_byte(logprob: torch.Tensor, num_bytes: torch.Tensor) -> torch.Tensor:
    """
    Negative log-likelihood in bits per UTF-8 byte, counting at least one byte.

    This is the metric of OLMo 3's Base Easy suite (arXiv 2512.13961, sec. 3.3.2). Dividing by bytes instead of tokens makes it independent of the tokenizer.
    """
    nats_per_byte = -logprob / num_bytes.clamp(min=1)

    return nats_per_byte / math.log(2)


def accuracy(score: torch.Tensor, valid: torch.Tensor, gold: torch.Tensor) -> torch.Tensor:
    """
    1.0 where the highest-scoring valid choice is gold, else 0.0, per doc.

    Ties go to the first choice, as in OLMES.
    """
    predicted = score.masked_fill(~valid, -math.inf).argmax(dim=-1)

    return (predicted == gold).float()


def multiple_choice(scores: ChoiceScores) -> dict[str, torch.Tensor]:
    """
    Per-doc OLMES multiple-choice metrics (Gu et al. 2024, sec. 3.3).

    Each metric picks the choice with the highest score, and they differ in how a choice is scored. Lengths count the choice's leading space and are at least 1.

    - `acc_raw`: the summed log-probability.
    - `acc_per_token`: the log-probability divided by the token count.
    - `acc_per_char`: the log-probability divided by the character count.
    - `acc_per_byte`: the lowest bits per byte.
    - `acc_uncond`: pointwise mutual information (PMI), the log-probability minus the log-probability after the unconditional context. This rewards choices the question makes more likely, not choices that are likely anyway. Only set when `logprob_uncond` is.

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
        "acc_per_byte": accuracy(-bpb, scores.valid, scores.gold),
    }

    if scores.logprob_uncond is not None:
        pmi = scores.logprob - scores.logprob_uncond
        metrics["acc_uncond"] = accuracy(pmi, scores.valid, scores.gold)

    metrics["bpb"] = bpb.gather(-1, scores.gold[:, None]).squeeze(-1)

    return metrics
