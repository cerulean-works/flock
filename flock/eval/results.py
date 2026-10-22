"""The versioned results file of an eval run, and the per-doc predictions beside it."""

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flock.eval.metrics import ChoiceScores
from flock.eval.provenance import Provenance
from flock.eval.tasks.base import MCDoc, Truncation


@dataclass(frozen=True, slots=True)
class TaskResult:
    """
    One task's scores.

    Args:
        task (`str`):
            The task's registry name.
        task_hash (`str`):
            sha256 of exactly what was scored, as [Results files](results#the-task-hash) defines it. Equal hashes mean the same requests were scored.
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
    primary_metric: str
    metrics: dict[str, float]
    num_docs: int
    limit: int | None
    seconds: float
    truncation: Truncation

    @property
    def docs_per_second(self) -> float:
        """Scoring throughput."""
        return self.num_docs / self.seconds if self.seconds > 0 else 0.0


@dataclass(frozen=True, slots=True)
class Results:
    """
    Every task's scores from one run, and its provenance.

    Args:
        provenance (`Provenance`):
            What produced the run.
        tasks (`list[TaskResult]`):
            Each task's scores, in run order.
        schema_version (`int`, *optional*, defaults to 3):
            The version of the file's layout, bumped when a field changes.
    """

    provenance: Provenance
    tasks: list[TaskResult]
    schema_version: int = 3

    def to_dict(self) -> dict[str, Any]:
        """Returns the results as plain JSON-ready data, with each task's `docs_per_second` added."""
        data = dataclasses.asdict(self)
        for task, entry in zip(self.tasks, data["tasks"], strict=True):
            entry["docs_per_second"] = task.docs_per_second

        return data

    def write(self, out: Path) -> Path:
        """Writes `results.json` into `out`, creating it if needed, and returns its path."""
        out.mkdir(parents=True, exist_ok=True)
        path = out / "results.json"
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n")
        return path


def write_predictions(path: Path, docs: list[MCDoc], scores: ChoiceScores) -> None:
    """Writes `prediction_rows` to `path` as JSON lines, creating its directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w") as f:
        for row in prediction_rows(docs, scores):
            f.write(json.dumps(row) + "\n")


def prediction_rows(docs: list[MCDoc], scores: ChoiceScores) -> list[dict[str, Any]]:
    """Returns one row per doc with its id, gold index and each choice's raw scores, dropping the padding past each doc's last choice."""
    # One host copy per field, not per doc.
    logprob = scores.logprob.tolist()
    num_tokens = scores.num_tokens.tolist()
    logprob_uncond = scores.logprob_uncond.tolist() if scores.logprob_uncond is not None else None

    rows = []
    for i, doc in enumerate(docs):
        count = len(doc.choices)
        row: dict[str, Any] = {
            "id": doc.id,
            "gold": doc.gold,
            "logprob": logprob[i][:count],
            "num_tokens": num_tokens[i][:count],
        }
        if logprob_uncond is not None:
            row["logprob_uncond"] = logprob_uncond[i][:count]
        rows.append(row)

    return rows
