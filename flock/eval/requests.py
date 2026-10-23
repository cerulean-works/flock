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
class Scores:
    """
    The model's answers to a list of requests, one per request, in request order, on the model's device.

    Args:
        logprob (`torch.Tensor`):
            float32 `[len(requests)]`, the summed natural-log probability of each continuation's tokens.
        num_tokens (`torch.Tensor`):
            int64 `[len(requests)]`, the number of tokens each continuation was scored over.
        tokens_cut (`torch.Tensor`):
            int64 `[len(requests)]`, the number of tokens cut from the left of each context to fit `max_length`, 0 if it fit.
    """

    logprob: torch.Tensor
    num_tokens: torch.Tensor
    tokens_cut: torch.Tensor
