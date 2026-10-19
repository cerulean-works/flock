from collections.abc import Callable, Iterable
from typing import Any


def history_rows(n: int, start: int = 0, **metrics: Callable[[int], Any]) -> list[dict]:
    """``n`` wandb history rows from ``_step=start``, with ``metric=f(step)`` for each metric."""
    return [
        {"_step": s, "_runtime": s * 0.1, **{k: f(s) for k, f in metrics.items()}}
        for s in range(start, start + n)
    ]


def system_samples(n: int, metrics: dict[str, Callable[[int], Any]], start: int = 0) -> list[dict]:
    """``n`` wandb system metric samples (wandb-events.jsonl lines); see ``sample_runtimes``."""
    return [
        {
            "_runtime": sample_runtimes([i])[0],
            "_timestamp": 1.79e9 + 1.5 * i,
            "_wandb": True,
            **{k: f(i) for k, f in metrics.items()},
        }
        for i in range(start, start + n)
    ]


def sample_runtimes(indices: Iterable[int]) -> list[float]:
    """``_runtime`` of the ``system_samples`` with these indices: one sample every 1.5s."""
    return [0.25 + 1.5 * i for i in indices]
