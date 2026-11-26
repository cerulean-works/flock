"""What a task asks a model to score, and what the model answers."""

from dataclasses import dataclass

import torch


@dataclass(frozen=True, slots=True)
class Request:
    """
    One loglikelihood request: the log-probability of `continuation` given `context`.

    Args:
        context (`str`):
            The text the model conditions on. See [`~eval.model.ScoringModel.encode`] for how an empty one is scored.
        continuation (`str`):
            The text being scored, usually starting with a space (`" Paris"`).
    """

    context: str
    continuation: str


@dataclass(frozen=True, slots=True)
class TokenValues:
    """
    Per-token values of many token sequences, one after another: sequence `i` holds the next `lengths[i]` tokens of each array. Each value is of the model's distribution at the position that predicts the token, in FP32 as scoring computes it.

    Args:
        lengths (`tuple[int, ...]`):
            Each sequence's number of tokens, 0 for a sequence not asked for.
        token_ids (`torch.Tensor`):
            int64 `[sum(lengths)]` on the CPU, the tokens.
        logprob (`torch.Tensor`):
            FP32 `[sum(lengths)]`, each token's natural-log probability.
        rank (`torch.Tensor`):
            int64 `[sum(lengths)]`, 1 plus the number of tokens more likely than it, so the most likely token has rank 1.
        entropy (`torch.Tensor`):
            FP32 `[sum(lengths)]`, the entropy in nats of the distribution the token was drawn against. Its negative is that distribution's mean log-probability, Min-K%++'s mu (Zhang et al. 2024, arXiv 2404.02936).
        top1_logprob (`torch.Tensor`):
            FP32 `[sum(lengths)]`, the log-probability of the most likely token.
        logprob_std (`torch.Tensor`):
            FP32 `[sum(lengths)]`, the standard deviation of the log-probability over the distribution, Min-K%++'s sigma.
    """

    lengths: tuple[int, ...]
    token_ids: torch.Tensor
    logprob: torch.Tensor
    rank: torch.Tensor
    entropy: torch.Tensor
    top1_logprob: torch.Tensor
    logprob_std: torch.Tensor


@dataclass(frozen=True, slots=True)
class Scores:
    """
    The model's answers to a list of requests, one per request, in request order, on the model's device. [`~eval.model.ScoringModel.rolling_loglikelihood`] answers one per text, a text standing for the continuation.

    Args:
        logprob (`torch.Tensor`):
            float32 `[len(requests)]`, the summed natural-log probability of each continuation's tokens. FP64 from `rolling_loglikelihood`, whose sums run over long texts.
        num_tokens (`torch.Tensor`):
            int64 `[len(requests)]`, the number of tokens each continuation was scored over.
        tokens_cut (`torch.Tensor`):
            int64 `[len(requests)]`, the number of tokens cut from the left of each context to fit `max_length`, 0 if it fit.
        is_greedy (`torch.Tensor`):
            bool `[len(requests)]`, whether every continuation token is the model's most likely next token, so greedy decoding would produce the continuation.
        continuation_tokens (`TokenValues`, *optional*):
            Each continuation's per-token values, one sequence per request, if asked for.
    """

    logprob: torch.Tensor
    num_tokens: torch.Tensor
    tokens_cut: torch.Tensor
    is_greedy: torch.Tensor
    continuation_tokens: TokenValues | None = None
