"""Run the GPU tests on a Modal H100, which covers both FlashAttention-3 and -4.

uv run modal run tests/modal_gpu.py
uv run modal run tests/modal_gpu.py --pytest-args "-k swiglu"
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import modal

ROOT = Path(__file__).parent.parent
MEMBERS = ["flock/cli", "flock/eval", "flock/hub", "flock/training", "app/api", "tests"]
REPO = "/repo"

# Dependencies go in their own layer, keyed on the lockfile and the workspace's pyproject files,
# so a code-only change reuses the cached image.
image = (
    modal.Image.debian_slim(python_version="3.14")
    .pip_install("uv==0.12.17")
    .env({"UV_PROJECT_ENVIRONMENT": "/venv", "UV_LINK_MODE": "copy"})
    .workdir(REPO)
    .add_local_file(ROOT / "pyproject.toml", f"{REPO}/pyproject.toml", copy=True)
    .add_local_file(ROOT / "uv.lock", f"{REPO}/uv.lock", copy=True)
)
for member in MEMBERS:
    image = image.add_local_file(
        ROOT / member / "pyproject.toml", f"{REPO}/{member}/pyproject.toml", copy=True
    )
image = image.run_commands("uv sync --all-packages --frozen --no-install-workspace").add_local_dir(
    ROOT,
    REPO,
    ignore=["**/.git", "**/.venv", "**/node_modules", "**/__pycache__", "app/web"],
)

app = modal.App("flock-gpu-tests", image=image)


@app.function(gpu="H100", timeout=60 * 60)
def run_pytest(pytest_args: str) -> int:
    subprocess.run(["uv", "sync", "--all-packages", "--frozen"], check=True)
    command = ["uv", "run", "--no-sync", "python", "-m", "pytest", *shlex.split(pytest_args)]
    return subprocess.run(command, check=False).returncode


@app.local_entrypoint()
def main(pytest_args: str = "-q tests/training -m gpu -n 0") -> None:
    returncode = run_pytest.remote(pytest_args)
    if returncode:
        raise SystemExit(returncode)
