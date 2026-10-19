"""What a task asks a model to score, and what the model answers."""

from dataclasses import dataclass

import torch


@dataclass(frozen=True, slots=True)
class Request:
    """
    One loglikelihood request: the log-probability of `continuation` given `context`.

    Args:
        context (`str`):
            The text the model conditions on. An empty context conditions on the tokenizer's BOS token, or its EOS token if it has no BOS.
        continuation (`str`):
            The text being scored, usually starting with a space (`" Paris"`).
    """

    context: str
    continuation: str


@dataclass(frozen=True, slots=True)
class Scores:
    """
    The model's answers to a list of requests, in request order, on the model's device.

    Args:
        logprob (`torch.Tensor`):
            float32 `[n]`, the summed natural-log probability of each continuation's tokens.
        num_tokens (`torch.Tensor`):
            int64 `[n]`, the number of tokens each continuation was scored over.
    """

    logprob: torch.Tensor
    num_tokens: torch.Tensor
