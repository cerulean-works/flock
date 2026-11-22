"""Run the GPU tests on a Modal H100, which covers both FlashAttention-3 and -4.

uv run modal run tests/modal_gpu.py
uv run modal run tests/modal_gpu.py --pytest-args "-q tests/training -m gpu -k swiglu" --workers 4
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tarfile
import time
import tomllib
from pathlib import Path

import modal

ROOT = Path(__file__).parent.parent
REPO = "/repo"
# Compiled kernels persist between runs as one archive in a Volume, since reading thousands of
# small files from a Volume is slow; each run unpacks it to local disk and saves it again.
VOLUME_DIR = Path("/compile-cache")
ARCHIVE = VOLUME_DIR / "kernels.tar"
CACHE_DIR = Path("/tmp/compile-cache")
CUDA_PACKAGES = re.compile(r"(torch|triton|nvidia-|cuda-|flash-attn)")


def cuda_pins() -> dict[str, list[str]]:
    """The locked CUDA wheels by index. Inside a container the image is already built."""
    lock = ROOT / "uv.lock"
    pins: dict[str, list[str]] = {}
    if not lock.exists():
        return pins
    for package in tomllib.loads(lock.read_text())["package"]:
        index = package.get("source", {}).get("registry")
        if index and CUDA_PACKAGES.match(package["name"]) and not index.endswith("/cpu"):
            pins.setdefault(index, []).append(f"{package['name']}=={package['version']}")
    return pins


def members() -> list[str]:
    """The workspace members, whose pyproject files `uv sync` needs next to the lockfile. Inside a
    container the image is already built."""
    pyproject = ROOT / "pyproject.toml"
    if not pyproject.exists():
        return []
    return tomllib.loads(pyproject.read_text())["tool"]["uv"]["workspace"]["members"]


# The CUDA wheels are several GB, so they get their own layers keyed only on their pinned
# versions: a lockfile change that leaves them alone reuses those layers, and `uv sync` keeps
# the installed wheels because they match the lock. The remaining dependencies are keyed on
# the lockfile and the workspace's pyproject files, so a code-only change reuses everything.
image = (
    modal.Image.debian_slim(python_version="3.14")
    .pip_install("uv==0.12.17")
    .env({"UV_PROJECT_ENVIRONMENT": "/venv", "UV_LINK_MODE": "copy"})
    .run_commands("uv venv /venv")
)
for index, pins in cuda_pins().items():
    image = image.run_commands(
        f"uv pip install --python /venv/bin/python --no-deps --index-url {index} {' '.join(pins)}"
    )
image = (
    image.workdir(REPO)
    .add_local_file(ROOT / "pyproject.toml", f"{REPO}/pyproject.toml", copy=True)
    .add_local_file(ROOT / "uv.lock", f"{REPO}/uv.lock", copy=True)
)
for member in members():
    image = image.add_local_file(
        ROOT / member / "pyproject.toml", f"{REPO}/{member}/pyproject.toml", copy=True
    )
image = image.run_commands("uv sync --all-packages --frozen --no-install-workspace").add_local_dir(
    ROOT,
    REPO,
    ignore=["**/.git", "**/.venv", "**/node_modules", "**/__pycache__", "app/web"],
)

app = modal.App("flock-gpu-tests", image=image)
compile_cache = modal.Volume.from_name("flock-gpu-compile-cache", create_if_missing=True, version=2)


def without_locks(member: tarfile.TarInfo) -> tarfile.TarInfo | None:
    return None if member.name.endswith(".lock") else member


# The test models are tiny, so one H100 runs several pytest workers at once. Each worker
# needs a CPU for Python and kernel compilation, so the container reserves one per worker.
@app.function(
    gpu="H100", cpu=8, memory=32 * 1024, timeout=60 * 60, volumes={str(VOLUME_DIR): compile_cache}
)
def run_pytest(pytest_args: str, workers: int) -> int:
    start = time.monotonic()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if ARCHIVE.exists():
        with tarfile.open(ARCHIVE) as archive:
            archive.extractall(CACHE_DIR, filter="data")
    print(f"Restored the compile cache in {time.monotonic() - start:.1f}s", flush=True)
    env = os.environ | {
        "TORCHINDUCTOR_CACHE_DIR": str(CACHE_DIR / "inductor"),
        "TRITON_CACHE_DIR": str(CACHE_DIR / "triton"),
        "FLASH_ATTENTION_CUTE_DSL_CACHE_ENABLED": "1",
        "FLASH_ATTENTION_CUTE_DSL_CACHE_DIR": str(CACHE_DIR / "cute"),
    }
    subprocess.run(["uv", "sync", "--all-packages", "--frozen"], check=True)
    # Work stealing keeps the few slow compile tests from queueing behind each other.
    command = ["uv", "run", "--no-sync", "python", "-m", "pytest", "-n", str(workers)]
    command += ["--dist=worksteal", *shlex.split(pytest_args)]
    returncode = subprocess.run(command, env=env, check=False).returncode
    # Concurrent runs each save the cache they started from plus their own kernels; the last
    # one to finish wins, which at worst drops another run's new kernels.
    start = time.monotonic()
    with tarfile.open("/tmp/kernels.tar", "w") as archive:
        archive.add(CACHE_DIR, arcname=".", filter=without_locks)
    partial = ARCHIVE.with_suffix(".partial")
    shutil.copyfile("/tmp/kernels.tar", partial)
    partial.replace(ARCHIVE)
    compile_cache.commit()
    print(f"Saved the compile cache in {time.monotonic() - start:.1f}s", flush=True)
    return returncode


@app.local_entrypoint()
def main(pytest_args: str = "-q tests/training -m gpu", workers: int = 8) -> None:
    returncode = run_pytest.remote(pytest_args, workers)
    if returncode:
        raise SystemExit(returncode)
