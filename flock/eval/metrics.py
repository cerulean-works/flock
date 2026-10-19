"""Metrics over scored answer choices: pure functions of tensors, computed on their device.

Names and definitions follow OLMES (Gu et al. 2024, sec. 3.3) and its reference code: a choice's score is its summed log-probability, raw or divided by its token, character or UTF-8 byte count (each count at least 1, and counted with the leading space), or minus its log-probability after the unconditional context `"Answer:"` (PMI). Bits per byte is OLMo 3's Base Easy metric (2512.13961, sec. 3.3.2): the gold answer's negative log-likelihood over its UTF-8 bytes, in bits.
"""

import math
from dataclasses import dataclass

import torch

LOG2_E = 1 / math.log(2)
# The names [`multiple_choice`] reports; `acc_uncond` only for tasks that score PMI.
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
            Each choice's character count.
        num_bytes (`torch.Tensor`):
            Each choice's UTF-8 byte count.
        valid (`torch.Tensor`):
            bool, False where a doc has fewer choices than the widest doc.
        gold (`torch.Tensor`):
            int64 `[docs]`, the index of each doc's correct choice.
        logprob_uncond (`torch.Tensor`, *optional*):
            Each choice's log-probability after the unconditional context, for PMI.
    """

    logprob: torch.Tensor
    num_tokens: torch.Tensor
    num_chars: torch.Tensor
    num_bytes: torch.Tensor
    valid: torch.Tensor
    gold: torch.Tensor
    logprob_uncond: torch.Tensor | None = None


def bits_per_byte(logprob: torch.Tensor, num_bytes: torch.Tensor) -> torch.Tensor:
    """Negative log-likelihood in bits per UTF-8 byte, counting at least one byte."""
    return -logprob * LOG2_E / num_bytes.clamp(min=1)


def accuracy(score: torch.Tensor, valid: torch.Tensor, gold: torch.Tensor) -> torch.Tensor:
    """
    1.0 where the highest-scoring valid choice is gold, else 0.0, per doc.

    Ties go to the first choice, as in OLMES.
    """
    predicted = score.masked_fill(~valid, -math.inf).argmax(dim=-1)
    return (predicted == gold).float()


def multiple_choice(scores: ChoiceScores) -> dict[str, torch.Tensor]:
    """
    Per-doc OLMES multiple-choice metrics.

    Returns:
        `dict[str, torch.Tensor]`: float32 `[docs]` tensors named `acc_raw`, `acc_per_token`, `acc_per_char`, `acc_per_byte` (lowest bits per byte wins), `acc_uncond` (only when `logprob_uncond` is set) and `bpb` (of the gold choice).
    """
    s = scores
    bpb = bits_per_byte(s.logprob, s.num_bytes)
    metrics = {
        "acc_raw": accuracy(s.logprob, s.valid, s.gold),
        "acc_per_token": accuracy(s.logprob / s.num_tokens.clamp(min=1), s.valid, s.gold),
        "acc_per_char": accuracy(s.logprob / s.num_chars.clamp(min=1), s.valid, s.gold),
        "acc_per_byte": accuracy(-bpb, s.valid, s.gold),
    }
    if s.logprob_uncond is not None:
        metrics["acc_uncond"] = accuracy(s.logprob - s.logprob_uncond, s.valid, s.gold)
    metrics["bpb"] = bpb.gather(-1, s.gold[:, None]).squeeze(-1)
    return metrics
