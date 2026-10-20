"""What produced a results file: the model, the flock commit, the settings and the machine."""

import os
import platform
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import torch
import transformers


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
            The command line that produced the run.
        device (`str`):
            The accelerator's name, or `"cpu"`.
        dtype (`str`):
            The weights' dtype.
        batch_tokens (`int`):
            The forward-pass budget in padded tokens.
        max_length (`int`):
            The model's input limit in tokens. Longer inputs lost tokens from the left.
        attention (`str`):
            The attention implementation (`sdpa`, `eager`, ...), which changes bfloat16 scores.
        torch (`str`), transformers (`str`), python (`str`):
            Library and interpreter versions.
        created_at (`str`):
            When `collect` ran, in ISO 8601 UTC.
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
        """Collects provenance for a run on this machine, now."""
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


def flock_commit() -> str:
    """The flock commit being run: `FLOCK_COMMIT` if set (as job scripts do), else git's HEAD."""
    if commit := os.environ.get("FLOCK_COMMIT"):
        return commit

    try:
        head = _git("rev-parse", "HEAD")
        dirty = _git("status", "--porcelain", "--untracked-files=no")
    # Both errors are caught (PEP 758 syntax): git is missing, or this is not a checkout.
    except OSError, subprocess.CalledProcessError:
        return "unknown"

    return f"{head}-dirty" if dirty else head


def _git(*args: str) -> str:
    """Runs git in this package's checkout and returns its stripped output."""
    root = Path(__file__).resolve().parent
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
    return result.stdout.strip()
