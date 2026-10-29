"""Run tasks against a model: the one entry point the CLI and job runners call."""

import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

import torch

from flock.eval.metrics import multiple_choice
from flock.eval.model import DEFAULT_BATCH_TOKENS, ScoringModel
from flock.eval.provenance import Provenance
from flock.eval.results import Results, SuiteResult, TaskResult, write_predictions
from flock.eval.tasks import SUITES, TASKS, MultipleChoiceTask
from flock.eval.tasks.suite import by_name

# Why `evaluate` and `score_task` refuse an output that is already there, and what to do.
_ONE_RUN = (
    "one directory holds one run, and a run that crashed leaves its predictions behind. "
    "Pass a new out directory or delete this one."
)

# The weights' dtypes `run` loads. Log-softmax always runs in float32.
Dtype = Literal["bfloat16", "float32"]


@dataclass(frozen=True, slots=True)
class EvalConfig:
    """
    One eval run. Plain data, so it pickles to a remote worker.

    Args:
        model (`str`):
            The model as `repo@revision`.
        tasks (`tuple[str, ...]`, *optional*):
            Registry names of the tasks to run, in order.
        out (`Path`, *optional*):
            Where to write `results.json` and `predictions/<task>.jsonl`. Nothing is written if not set.
        limit (`int`, *optional*):
            Score only each task's first `limit` docs, for quick checks.
        batch_tokens (`int`, *optional*):
            Passed to [`~eval.model.ScoringModel`].
        dtype (`Dtype`, *optional*, defaults to `"bfloat16"`):
            The weights' dtype, `"bfloat16"` or `"float32"`. Log-softmax always runs in float32.
        device (`str`, *optional*):
            Where to run. Defaults to CUDA when available.
        wandb_project (`str`, *optional*):
            Also log the summary to this tracker project through the `wandb` SDK.
        command (`str`, *optional*):
            The command line recorded in the provenance, or `None` if not given. The CLI passes its own command line.
        suites (`tuple[str, ...]`, *optional*):
            Registry names of the suites to run. Their tasks run after `tasks`, each task once, and each suite and the suites it holds are scored.

    Raises:
        `ValueError`: if no task or suite is given, a task or suite repeats, or `limit` or `batch_tokens` is less than 1, or `dtype` is not `"bfloat16"` or `"float32"`, before any model loads.
    """

    model: str
    tasks: tuple[str, ...] = ()
    out: Path | None = None
    limit: int | None = None
    batch_tokens: int = DEFAULT_BATCH_TOKENS
    dtype: Dtype = "bfloat16"
    device: str | None = None
    wandb_project: str | None = None
    command: str | None = None
    suites: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.tasks and not self.suites:
            raise ValueError("give at least one task or suite")
        if len(set(self.tasks)) != len(self.tasks):
            raise ValueError(f"tasks repeat: {self.tasks}")
        if len(set(self.suites)) != len(self.suites):
            raise ValueError(f"suites repeat: {self.suites}")
        # `MultipleChoiceTask.load` and `ScoringModel` check these too, but a config is plain data
        # that may be built where no task or model loads, such as before it goes to a worker.
        if self.limit is not None and self.limit < 1:
            raise ValueError(f"limit must be at least 1, got {self.limit}")
        if self.batch_tokens < 1:
            raise ValueError(f"batch_tokens must be at least 1, got {self.batch_tokens}")
        if self.dtype not in get_args(Dtype):
            raise ValueError(f"dtype must be bfloat16 or float32, got {self.dtype}")


def evaluate(config: EvalConfig) -> Results:
    """
    Loads the model, collects the provenance, scores every task and suite and writes the results, then logs them to the tracker if `wandb_project` is set.

    Raises:
        `KeyError`: if a task or suite name isn't in its registry.
        `ValueError`: if two different tasks, or two different suites, to run share a name, a task and a suite share a name, or a task is named `suites`.
        `FileExistsError`: if `config.out` exists and isn't an empty directory.
    """
    _check_names("tasks", config.tasks, TASKS)
    _check_names("suites", config.suites, SUITES)
    _check_out(config.out)

    # The named tasks run first, then each suite's tasks not yet listed, each task once. Every
    # suite reached is scored, each after its members. Both plans are checked before the model
    # loads, so a name clash costs no GPU time.
    suites = [SUITES[name] for name in config.suites]
    tasks = [TASKS[name] for name in config.tasks]
    for suite in suites:
        tasks.extend(suite.tasks)
    tasks = list(by_name(tasks).values())
    held = []
    for suite in suites:
        held.extend(suite.suites)
    held = list(by_name(held).values())
    # The tracker logs suite scores as `suites/<suite>` beside `<task>/<metric>`.
    if any(task.name == "suites" for task in tasks):
        raise ValueError("a task can't be named suites: suite scores are logged under that name")
    # Results and suite hashes look members up by name, so a suite can't share one with a task.
    clashes = sorted({task.name for task in tasks} & {suite.name for suite in held})
    if clashes:
        raise ValueError(f"a task and a suite share the names {clashes}")

    model = ScoringModel.from_pretrained(
        config.model,
        device=config.device,
        dtype=getattr(torch, config.dtype),
        batch_tokens=config.batch_tokens,
    )
    # Collected before scoring, so the commit's dirty flag describes the tree as scoring starts
    # and `created_at` is when scoring started, as lm-evaluation-harness records `start_date`.
    provenance = Provenance.collect(
        model=model.name,
        command=config.command,
        device=model.device,
        dtype=model.dtype,
        batch_tokens=model.batch_tokens,
        max_length=model.max_length,
        attention=model.attention,
    )
    task_results = [score_task(model, task, config.limit, config.out) for task in tasks]

    # Suite scores are means of the per-task means already on the host. A suite's hash covers
    # each member's hash, and `held` puts every suite after its members, so equal hashes mean
    # the same members scored the same requests all the way down.
    metrics = {result.task: result.metrics for result in task_results}
    hashes = {result.task: result.task_hash for result in task_results}
    suite_results = []
    for suite in held:
        score = suite.score(metrics) if suite.scored else None
        members = [member.name for member in suite.members]
        definition = {
            "suite": suite.name,
            "metric": suite.metric,
            "scored": suite.scored,
            "members": [[name, hashes[name]] for name in members],
        }
        hashes[suite.name] = hashlib.sha256(
            json.dumps(definition, sort_keys=True).encode()
        ).hexdigest()
        suite_results.append(
            SuiteResult(suite.name, hashes[suite.name], members, suite.metric, score)
        )
    results = Results(provenance, task_results, suite_results)

    if config.out is not None:
        results.write(config.out)
    if config.wandb_project is not None:
        # Imported here so importing this module, as training loops do for `score_task`, doesn't
        # import wandb.
        from flock.eval.tracking import log_to_tracker

        log_to_tracker(results, config.wandb_project)

    return results


def _check_names(kind: str, names: tuple[str, ...], registry: Mapping[str, object]) -> None:
    """Raises `KeyError` if a name isn't in its registry, before any model loads."""
    # Checked here, not in `EvalConfig`: a config is plain data that may be built where the
    # registry differs from the worker's.
    unknown = [name for name in names if name not in registry]
    if unknown:
        raise KeyError(f"unknown {kind} {unknown}; known: {sorted(registry)}")


def _check_out(out: Path | None) -> None:
    """Raises `FileExistsError` if `out` exists and isn't an empty directory."""
    # Old predictions next to a new results.json would mix two runs.
    if out is not None and out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise FileExistsError(f"{out} is not an empty directory: {_ONE_RUN}")


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
            Write per-doc scores to `out/predictions/<task>.jsonl`. A training loop gives each step its own `out`. No `results.json` is written: that is [`evaluate`]'s job.

    Raises:
        `FileExistsError`: if the predictions file already exists, before anything is scored.
    """
    predictions = out / "predictions" / f"{task.name}.jsonl" if out is not None else None
    if predictions is not None and predictions.exists():
        raise FileExistsError(f"{predictions} exists: {_ONE_RUN}")

    docs, shots = task.load(limit)
    prepared = task.requests(docs, shots)

    start = time.perf_counter()
    request_scores = model.loglikelihood(prepared.requests)
    scores = prepared.choice_scores(request_scores)
    reported = task.reported_metrics
    per_doc = {name: values for name, values in multiple_choice(scores).items() if name in reported}
    # One host copy for every metric's mean. It also waits for the GPU, so `seconds` times the
    # scoring.
    means = torch.stack(list(per_doc.values())).mean(dim=1).tolist()
    seconds = time.perf_counter() - start
    # `means` follows the order of `per_doc`'s keys.
    metrics = dict(zip(per_doc, means, strict=True))
    truncation = prepared.truncation(request_scores)

    if predictions is not None:
        write_predictions(predictions, prepared.docs, scores)

    return TaskResult(
        task=task.name,
        task_hash=prepared.hash,
        dataset=task.dataset,
        primary_metric=task.primary_metric,
        metrics=metrics,
        num_docs=len(prepared.docs),
        limit=limit,
        seconds=seconds,
        truncation=truncation,
    )
