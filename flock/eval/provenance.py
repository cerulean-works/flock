"""What produced a results file: the model, the flock commit, the settings and the machine."""

import os
import platform
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import datasets
import tokenizers
import torch
import transformers


@dataclass(frozen=True, slots=True)
class Provenance:
    """
    What produced a results file.

    Args:
        model (`str`):
            The scored model as `repo@commit`, or as `flock:org/name@number` for a version in the Flock registry.
        model_digest (`str`, *optional*):
            The Flock version's digest, which names its exact files, or `None` for a Hugging Face model, whose commit in `model` already does. Results files written before it was added have no `model_digest`, and read as `None`.
        flock_commit (`str`):
            The flock commit that ran, from `FLOCK_COMMIT` or git, or `"unknown"`. A `-dirty` suffix means the checkout had uncommitted changes or new files under `flock/`.
        command (`str`, *optional*):
            The command line that produced the run, or `None` when the caller didn't say, as from the Python API.
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
        torch (`str`), transformers (`str`), tokenizers (`str`), datasets (`str`), python (`str`):
            Library and interpreter versions. `tokenizers` decides token boundaries, and `datasets` reads the rows.
        created_at (`str`):
            When `collect` ran, in ISO 8601 UTC. [`~eval.run.evaluate`] calls it once the model has loaded, just before scoring starts.
    """

    model: str
    # Keyword-only with a default, so it sits beside `model` in the file and older results that
    # lack it still load.
    model_digest: str | None = field(default=None, kw_only=True)
    flock_commit: str
    command: str | None
    device: str
    dtype: str
    batch_tokens: int
    max_length: int
    attention: str
    torch: str
    transformers: str
    tokenizers: str
    datasets: str
    python: str
    created_at: str

    @classmethod
    def collect(
        cls,
        *,
        model: str,
        command: str | None,
        device: torch.device,
        dtype: torch.dtype,
        batch_tokens: int,
        max_length: int,
        attention: str,
        model_digest: str | None = None,
    ) -> Provenance:
        """Collects provenance for a run on this machine, now."""
        return cls(
            model=model,
            model_digest=model_digest,
            flock_commit=flock_commit(),
            command=command,
            device=torch.cuda.get_device_name(device) if device.type == "cuda" else device.type,
            dtype=str(dtype).removeprefix("torch."),
            batch_tokens=batch_tokens,
            max_length=max_length,
            attention=attention,
            torch=torch.__version__,
            transformers=transformers.__version__,
            tokenizers=tokenizers.__version__,
            datasets=datasets.__version__,
            python=platform.python_version(),
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )


def flock_commit() -> str:
    """The flock commit being run: `FLOCK_COMMIT` if set (as job scripts do), else git's HEAD if this package is a flock checkout, else `"unknown"`."""
    if commit := os.environ.get("FLOCK_COMMIT"):
        return commit

    package = Path(__file__).resolve().parent
    try:
        root, head = _git(package, "rev-parse", "--show-toplevel", "HEAD").splitlines()
        # An installed copy inside some other repository (a virtualenv in a project) would report
        # that repository's HEAD.
        if package != Path(root) / "flock" / "eval":
            return "unknown"
        # Changes to tracked files count anywhere: the lockfile and `pyproject.toml` are at the
        # root. New files count only under `flock/`, where an uncommitted module may have run; a
        # scratch script or an output beside the code didn't. `:/` roots the path at the top.
        changed = _git(package, "status", "--porcelain", "--untracked-files=no")
        # `--no-empty-directory`: a directory with nothing new in it, such as the `__pycache__`
        # a deleted module leaves behind, is not a new file.
        new = _git(
            package,
            "ls-files",
            "--others",
            "--exclude-standard",
            "--directory",
            "--no-empty-directory",
            "--",
            ":/flock",
        )
    # Both errors are caught (PEP 758 syntax): git is missing, or this is not a checkout.
    except OSError, subprocess.CalledProcessError:
        return "unknown"

    return f"{head}-dirty" if changed or new else head


def _git(cwd: Path, *args: str) -> str:
    """Runs git in `cwd` and returns its stripped output."""
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout.strip()
