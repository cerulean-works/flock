"""Run tasks against a model: the one entry point the CLI and job runners call."""

import time
from dataclasses import dataclass
from pathlib import Path

import torch

from flock.eval.metrics import multiple_choice
from flock.eval.model import DEFAULT_BATCH_TOKENS, ScoringModel
from flock.eval.results import Provenance, Results, TaskResult, write_predictions
from flock.eval.tasks import TASKS, MultipleChoiceTask


@dataclass(frozen=True, slots=True)
class EvalConfig:
    """
    One eval run. Plain data, so it pickles to a remote worker.

    Args:
        model (`str`):
            The model as `repo@revision`.
        tasks (`tuple[str, ...]`):
            Registry names of the tasks to run, in order.
        out (`Path`, *optional*):
            Where to write `results.json` and `predictions/<task>.jsonl`. Nothing is written if not set.
        limit (`int`, *optional*):
            Score only each task's first `limit` docs, for quick checks.
        batch_tokens (`int`, *optional*, defaults to 32768):
            The most padded tokens in one forward pass.
        device (`str`, *optional*):
            Where to run. Defaults to CUDA when available.
        wandb_project (`str`, *optional*):
            Also log the summary to this tracker project through the `wandb` SDK.
        command (`str`, *optional*, defaults to `"python"`):
            The command line recorded in the provenance.

    Raises:
        `ValueError`: if a task repeats, or `limit` or `batch_tokens` is less than 1, before any model loads.
    """

    model: str
    tasks: tuple[str, ...]
    out: Path | None = None
    limit: int | None = None
    batch_tokens: int = DEFAULT_BATCH_TOKENS
    device: str | None = None
    wandb_project: str | None = None
    command: str = "python"

    def __post_init__(self) -> None:
        if len(set(self.tasks)) != len(self.tasks):
            raise ValueError(f"tasks repeat: {self.tasks}")
        if self.limit is not None and self.limit < 1:
            raise ValueError(f"limit must be at least 1, got {self.limit}")
        if self.batch_tokens < 1:
            raise ValueError(f"batch_tokens must be at least 1, got {self.batch_tokens}")


def evaluate(config: EvalConfig) -> Results:
    """
    Loads the model, scores every task and writes the results.

    Raises:
        `KeyError`: if a task name isn't in the registry.
        `FileExistsError`: if `config.out` exists and isn't an empty directory.
    """
    unknown = [name for name in config.tasks if name not in TASKS]
    if unknown:
        raise KeyError(f"unknown tasks {unknown}; known: {sorted(TASKS)}")
    # Old predictions next to a new results.json would mix two runs.
    out = config.out
    if out is not None and out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise FileExistsError(f"{config.out} is not an empty directory")
    model = ScoringModel.from_pretrained(
        config.model, device=config.device, batch_tokens=config.batch_tokens
    )
    tasks = [score_task(model, TASKS[name], config.limit, config.out) for name in config.tasks]
    provenance = Provenance.collect(
        model=model.name,
        command=config.command,
        device=model.device,
        dtype=model.model.dtype,
        batch_tokens=model.batch_tokens,
        max_length=model.max_length,
        attention=model.model.config._attn_implementation,
    )
    results = Results(provenance, tasks)
    if config.out is not None:
        results.write(config.out)
    if config.wandb_project is not None:
        # Imported here so training loops that call `score_task` don't load wandb.
        from flock.eval.tracking import log_to_tracker

        log_to_tracker(results, config.wandb_project)
    return results


def score_task(
    model: ScoringModel, task: MultipleChoiceTask, limit: int | None = None, out: Path | None = None
) -> TaskResult:
    """
    Scores one task with an already loaded model, as training loops do between steps.

    Args:
        model (`ScoringModel`):
            The model to score.
        task (`MultipleChoiceTask`):
            The task.
        limit (`int`, *optional*):
            Score only the first `limit` docs.
        out (`Path`, *optional*):
            Write per-doc scores to `out/predictions/<task>.jsonl`.
    """
    requests = task.requests(*task.load(limit))
    start = time.perf_counter()
    scores = requests.choice_scores(model.loglikelihood(requests.requests))
    per_doc = multiple_choice(scores)
    means = torch.stack(list(per_doc.values())).mean(dim=1).tolist()
    seconds = time.perf_counter() - start
    if out is not None:
        write_predictions(out / "predictions" / f"{task.name}.jsonl", requests.docs, scores)
    return TaskResult(
        task=task.name,
        task_hash=requests.hash,
        primary_metric=task.primary_metric,
        metrics=dict(zip(per_doc, means, strict=True)),
        num_docs=len(requests.docs),
        limit=limit,
        seconds=seconds,
    )
