"""The versioned results file of an eval run, and the per-doc predictions beside it."""

import dataclasses
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from flock.eval.metrics import ChoiceScores
from flock.eval.provenance import Provenance
from flock.eval.tasks.base import Dataset, MCDoc, Truncation


@dataclass(frozen=True, slots=True)
class TaskResult:
    """
    One task's scores.

    Args:
        task (`str`):
            The task's registry name.
        task_hash (`str`):
            sha256 of exactly what was scored, as [Results files](results#the-task-hash) defines it. Equal hashes mean the same requests were scored.
        dataset (`Dataset`):
            The pinned dataset the docs came from. The hash covers it, and this field lets a reader see which one.
        primary_metric (`str`):
            The metric to read first.
        metrics (`dict[str, float]`):
            Each metric's mean over docs.
        num_docs (`int`):
            How many docs were scored.
        limit (`int`, *optional*):
            The doc limit the run used, or `None` for the whole split.
        seconds (`float`):
            Wall time of scoring, excluding data and model loading.
        truncation (`Truncation`):
            How many requests and docs had their contexts cut from the left to fit the model.
    """

    task: str
    task_hash: str
    dataset: Dataset
    primary_metric: str
    metrics: dict[str, float]
    num_docs: int
    limit: int | None
    seconds: float
    truncation: Truncation

    @property
    def docs_per_second(self) -> float | None:
        """Scoring throughput, or `None` if the clock measured no time."""
        return self.num_docs / self.seconds if self.seconds > 0 else None


@dataclass(frozen=True, slots=True)
class SuiteResult:
    """
    One suite's score.

    Args:
        suite (`str`):
            The suite's registry name.
        suite_hash (`str`):
            sha256 of the suite's name, `metric`, `scored` and each member's name and hash (a task's `task_hash`, a suite's `suite_hash`), as [Results files](results#suites) defines it. Equal hashes mean the same members scored the same requests.
        members (`list[str]`):
            Its members' names, tasks and suites, each with an entry of its own in the results.
        metric (`str`, *optional*):
            The metric read from each member task, or `None` for each task's primary metric.
        score (`float`, *optional*):
            The unweighted mean of the members' scores, or `None` for a suite that only groups others.
    """

    suite: str
    suite_hash: str
    members: list[str]
    metric: str | None
    score: float | None


@dataclass(frozen=True, slots=True)
class Results:
    """
    Every task's scores from one run, and its provenance.

    `schema_version`, 6, is the version of the file's layout, bumped when a field changes. It isn't an argument, so a file can't claim a layout it doesn't have.

    Args:
        provenance (`Provenance`):
            What produced the run.
        tasks (`list[TaskResult]`):
            Each task's scores, in run order.
        suites (`list[SuiteResult]`, *optional*):
            Each suite's score, every suite after the suites it holds. Empty for a run of tasks only.
    """

    provenance: Provenance
    tasks: list[TaskResult]
    suites: list[SuiteResult] = field(default_factory=list)
    schema_version: int = field(default=6, init=False)

    def to_dict(self) -> dict[str, Any]:
        """Returns the results as plain JSON-ready data, with each task's `docs_per_second` added."""
        data = dataclasses.asdict(self)
        for task, entry in zip(self.tasks, data["tasks"], strict=True):
            entry["docs_per_second"] = task.docs_per_second

        return data

    def write(self, out: Path) -> Path:
        """
        Writes `results.json` into `out`, creating it if needed, and returns its path.

        Raises:
            `FileExistsError`: if `out` already holds a `results.json`, so two runs never write one file.
        """
        # Serialized before the file is created, so a failure leaves no empty file to block a rerun.
        text = json.dumps(self.to_dict(), indent=2) + "\n"
        out.mkdir(parents=True, exist_ok=True)
        path = out / "results.json"
        with path.open("x") as f:
            f.write(text)
        return path


def write_predictions(path: Path, docs: Sequence[MCDoc], scores: ChoiceScores) -> None:
    """
    Writes `prediction_rows` to `path` as JSON lines, creating its directory if needed.

    Raises:
        `FileExistsError`: if `path` exists, so two runs never write one file.
    """
    # Built before the file is created, so a failed check leaves no empty file to block a rerun.
    rows = prediction_rows(docs, scores)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("x") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def prediction_rows(docs: Sequence[MCDoc], scores: ChoiceScores) -> list[dict[str, Any]]:
    """
    Returns one row per doc with its id, gold index and each choice's scores and lengths, enough to recompute every metric and the truncation counts, dropping the padding past each doc's last choice.

    Raises:
        `ValueError`: if `scores` doesn't hold one row per doc.
    """
    # Rows are read by doc position, so extra score rows would be dropped without a word.
    if len(docs) != len(scores.gold):
        raise ValueError(f"{len(docs)} docs but scores for {len(scores.gold)}")

    # One host copy per field, not per doc.
    fields = {
        "logprob": scores.logprob,
        "num_tokens": scores.num_tokens,
        "num_chars": scores.num_chars,
        "num_bytes": scores.num_bytes,
        "is_greedy": scores.is_greedy,
        "tokens_cut": scores.tokens_cut,
        "logprob_uncond": scores.logprob_uncond,
        "tokens_cut_uncond": scores.tokens_cut_uncond,
    }
    values = {name: tensor.tolist() for name, tensor in fields.items() if tensor is not None}

    rows = []
    for i, doc in enumerate(docs):
        row: dict[str, Any] = {"id": doc.id, "gold": doc.gold}
        for name, per_doc in values.items():
            row[name] = per_doc[i][: len(doc.choices)]
        rows.append(row)

    return rows
