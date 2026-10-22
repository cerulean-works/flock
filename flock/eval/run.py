"""Run tasks against a model: the one entry point the CLI and job runners call."""

import time
from dataclasses import dataclass
from pathlib import Path

import torch

from flock.eval.metrics import multiple_choice
from flock.eval.model import DEFAULT_BATCH_TOKENS, ScoringModel
from flock.eval.provenance import Provenance
from flock.eval.results import Results, TaskResult, write_predictions
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
        batch_tokens (`int`, *optional*):
            Passed to [`~eval.model.ScoringModel`].
        device (`str`, *optional*):
            Where to run. Defaults to CUDA when available.
        wandb_project (`str`, *optional*):
            Also log the summary to this tracker project through the `wandb` SDK.
        command (`str`, *optional*, defaults to `"python"`):
            The command line recorded in the provenance. The default marks a run started from the Python API. The CLI passes its own command line.

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
    Loads the model, scores every task and writes the results, then logs them to the tracker if `wandb_project` is set.

    Raises:
        `KeyError`: if a task name isn't in the registry.
        `FileExistsError`: if `config.out` exists and isn't an empty directory.
    """
    _check_tasks(config.tasks)
    _check_out(config.out)

    model = ScoringModel.from_pretrained(
        config.model, device=config.device, batch_tokens=config.batch_tokens
    )
    task_results = []
    for name in config.tasks:
        task_results.append(score_task(model, TASKS[name], config.limit, config.out))

    provenance = Provenance.collect(
        model=model.name,
        command=config.command,
        device=model.device,
        dtype=model.dtype,
        batch_tokens=model.batch_tokens,
        max_length=model.max_length,
        attention=model.attention,
    )
    results = Results(provenance, task_results)

    if config.out is not None:
        results.write(config.out)
    if config.wandb_project is not None:
        # Imported here so importing this module, as training loops do for `score_task`, doesn't
        # import wandb.
        from flock.eval.tracking import log_to_tracker

        log_to_tracker(results, config.wandb_project)

    return results


def _check_tasks(names: tuple[str, ...]) -> None:
    """Raises `KeyError` if a task isn't in the registry, before any model loads."""
    # Checked here, not in `EvalConfig`: a config is plain data that may be built where the
    # registry differs from the worker's.
    unknown = [name for name in names if name not in TASKS]
    if unknown:
        raise KeyError(f"unknown tasks {unknown}; known: {sorted(TASKS)}")


def _check_out(out: Path | None) -> None:
    """Raises `FileExistsError` if `out` exists and isn't an empty directory."""
    # Old predictions next to a new results.json would mix two runs.
    if out is not None and out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise FileExistsError(f"{out} is not an empty directory")


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
            Write per-doc scores to `out/predictions/<task>.jsonl`, replacing an existing file. No `results.json` is written: that is [`evaluate`]'s job.
    """
    docs, shots = task.load(limit)
    prepared = task.requests(docs, shots)

    start = time.perf_counter()
    request_scores = model.loglikelihood(prepared.requests)
    scores = prepared.choice_scores(request_scores)
    per_doc = multiple_choice(scores)
    # One host copy for every metric's mean. It also waits for the GPU, so `seconds` times the
    # scoring.
    means = torch.stack(list(per_doc.values())).mean(dim=1).tolist()
    seconds = time.perf_counter() - start
    # `means` follows the order of `per_doc`'s keys.
    metrics = dict(zip(per_doc, means, strict=True))
    truncation = prepared.truncation(request_scores)

    if out is not None:
        write_predictions(out / "predictions" / f"{task.name}.jsonl", prepared.docs, scores)

    return TaskResult(
        task=task.name,
        task_hash=prepared.hash,
        primary_metric=task.primary_metric,
        metrics=metrics,
        num_docs=len(prepared.docs),
        limit=limit,
        seconds=seconds,
        truncation=truncation,
    )
