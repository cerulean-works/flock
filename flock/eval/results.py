"""The versioned results file of an eval run, with the provenance to reproduce it."""

import dataclasses
import json
import os
import platform
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import torch
import transformers

from flock.eval.metrics import ChoiceScores
from flock.eval.tasks.base import MCDoc

SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class Provenance:
    """
    What produced a results file.

    Args:
        model (`str`):
            The scored model as `repo@commit`.
        flock_commit (`str`):
            The flock commit that ran, from `FLOCK_COMMIT` or git. A `-dirty` suffix means the checkout had uncommitted changes.
        command (`str`):
            The command line, or `"python"` when [`~run.evaluate`] was called directly.
        device (`str`):
            The accelerator's name, or `"cpu"`.
        dtype (`str`):
            The weights' dtype.
        batch_tokens (`int`):
            The forward-pass budget in padded tokens.
        max_length (`int`):
            The longest input the model saw; longer ones lost tokens from the left.
        attention (`str`):
            The attention implementation (`sdpa`, `eager`, ...), which changes bfloat16 scores.
        torch (`str`), transformers (`str`), python (`str`):
            Library and interpreter versions.
        created_at (`str`):
            When the run finished, in ISO 8601 UTC.
    """

    model: str
    flock_commit: str
    command: str
    device: str
    dtype: str
    batch_tokens: int
    max_length: int
    attention: str
    torch: str
    transformers: str
    python: str
    created_at: str

    @classmethod
    def collect(
        cls,
        *,
        model: str,
        command: str,
        device: torch.device,
        dtype: torch.dtype,
        batch_tokens: int,
        max_length: int,
        attention: str,
    ) -> Provenance:
        """Provenance for a run on this machine, now."""
        return cls(
            model=model,
            flock_commit=flock_commit(),
            command=command,
            device=torch.cuda.get_device_name(device) if device.type == "cuda" else device.type,
            dtype=str(dtype).removeprefix("torch."),
            batch_tokens=batch_tokens,
            max_length=max_length,
            attention=attention,
            torch=torch.__version__,
            transformers=transformers.__version__,
            python=platform.python_version(),
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )


@dataclass(frozen=True, slots=True)
class TaskResult:
    """
    One task's scores.

    Args:
        task (`str`):
            The task's registry name.
        task_hash (`str`):
            sha256 of the task's definition and every scored string. Equal hashes mean the same requests were scored.
        primary_metric (`str`):
            The metric to read first.
        metrics (`dict[str, float]`):
            Each metric's mean over docs.
        num_docs (`int`):
            How many docs were scored.
        limit (`int`, *optional*):
            The `--limit` the run used, or `None` for the whole split.
        seconds (`float`):
            Wall time of scoring, excluding data and model loading.
    """

    task: str
    task_hash: str
    primary_metric: str
    metrics: dict[str, float]
    num_docs: int
    limit: int | None
    seconds: float

    @property
    def docs_per_second(self) -> float:
        """Scoring throughput."""
        return self.num_docs / self.seconds if self.seconds > 0 else 0.0


@dataclass(frozen=True, slots=True)
class Results:
    """Every task's scores from one run, and its provenance."""

    provenance: Provenance
    tasks: list[TaskResult]
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """The results as plain JSON-ready data."""
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


def flock_commit() -> str:
    """The flock commit being run: `FLOCK_COMMIT` if set (as job scripts do), else git's HEAD."""
    if commit := os.environ.get("FLOCK_COMMIT"):
        return commit
    root = Path(__file__).resolve().parent

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()

    try:
        head = git("rev-parse", "HEAD")
        dirty = git("status", "--porcelain", "--untracked-files=no")
    except OSError, subprocess.CalledProcessError:
        return "unknown"
    return f"{head}-dirty" if dirty else head


def write_predictions(path: Path, docs: list[MCDoc], scores: ChoiceScores) -> None:
    """Writes one JSON line per doc with its gold index and each choice's raw scores."""
    path.parent.mkdir(parents=True, exist_ok=True)
    logprob = scores.logprob.tolist()
    num_tokens = scores.num_tokens.tolist()
    uncond = scores.logprob_uncond.tolist() if scores.logprob_uncond is not None else None
    with path.open("w") as f:
        for row, doc in enumerate(docs):
            count = len(doc.choices)
            line: dict[str, Any] = {
                "id": doc.id,
                "gold": doc.gold,
                "logprob": logprob[row][:count],
                "num_tokens": num_tokens[row][:count],
            }
            if uncond is not None:
                line["logprob_uncond"] = uncond[row][:count]
            f.write(json.dumps(line) + "\n")
