"""Plan, prepare and launch a sweep of scaling-law runs inside one Slurm allocation.

One allocation holds several concurrent runs. `launch` splits the allocation's
GPUs into slots of `gpus_per_run` GPUs. Each slot claims the next unfinished
run, trains it as a Slurm job step, then claims another until none are left.
Rerunning `launch` in a new allocation resumes interrupted runs from their
checkpoints and skips finished ones.

```bash
python -m flock.training.experiments.scaling_law.sweep plan sweep.yaml --nodes 4 --gpus-per-node 4
python -m flock.training.experiments.scaling_law.sweep prepare sweep.yaml
python -m flock.training.experiments.scaling_law.sweep launch sweep.yaml
python -m flock.training.experiments.scaling_law.sweep status sweep.yaml
```
"""

import argparse
import json
import logging
import os
import shlex
import signal
import socket
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Any, Literal, Self

from .config import ExperimentConfig, Sweep, experiment_json

logger = logging.getLogger(__name__)

type RunState = Literal["done", "failed", "running", "interrupted", "pending"]


@dataclass(frozen=True)
class Allocation:
    """GPUs available to the sweep: `nodes` hosts with `gpus_per_node` GPUs each."""

    nodes: int
    gpus_per_node: int

    @property
    def gpus(self) -> int:
        """Return the allocation's GPU count."""
        return self.nodes * self.gpus_per_node

    @classmethod
    def from_slurm(cls, gpus_per_node: int | None = None) -> Self:
        """Read the current Slurm job's node and GPU counts from its environment.

        Raises:
            `RuntimeError`: If no Slurm job is running or the GPU count is unknown.
        """
        if "SLURM_JOB_ID" not in os.environ:
            raise RuntimeError("Not inside a Slurm job. Use --local, or launch from sbatch.")
        nodes = int(os.environ.get("SLURM_JOB_NUM_NODES") or os.environ["SLURM_NNODES"])
        if gpus_per_node is None:
            # SLURM_GPUS_ON_NODE is set when the job requests GPUs with --gpus-per-node or --gres.
            visible = os.environ.get("SLURM_GPUS_ON_NODE")
            if visible is None:
                raise RuntimeError("Cannot tell GPUs per node; pass --gpus-per-node")
            gpus_per_node = int(visible)
        return cls(nodes=nodes, gpus_per_node=gpus_per_node)


@dataclass(frozen=True)
class SlotLayout:
    """How runs share an allocation: `slots` concurrent runs of `nodes_per_run` nodes each."""

    slots: int
    nodes_per_run: int
    gpus_per_node_used: int
    gpus_per_run: int

    @classmethod
    def plan(cls, allocation: Allocation, gpus_per_run: int) -> Self:
        """Fit runs into the allocation.

        A run uses part of one node, or whole nodes. It never takes part of
        several nodes, since those GPUs would talk over the network.

        Raises:
            `ValueError`: If no run fits or a multi-node run needs a partial node.
        """
        per_node = allocation.gpus_per_node
        if gpus_per_run <= per_node:
            slots = allocation.nodes * (per_node // gpus_per_run)
            layout = cls(slots, 1, gpus_per_run, gpus_per_run)
        else:
            if gpus_per_run % per_node:
                raise ValueError(
                    f"gpus_per_run={gpus_per_run} spans nodes, so it must be a multiple of "
                    f"the {per_node} GPUs on each node"
                )
            nodes_per_run = gpus_per_run // per_node
            layout = cls(allocation.nodes // nodes_per_run, nodes_per_run, per_node, gpus_per_run)
        if layout.slots == 0:
            raise ValueError(
                f"A run needs {gpus_per_run} GPUs but the allocation has {allocation.gpus}"
            )
        return layout


def capacity_warnings(runs: int, allocation: Allocation, layout: SlotLayout) -> list[str]:
    """Explain where GPUs would sit idle for this many runs on this allocation."""
    warnings = []
    idle = allocation.gpus - layout.slots * layout.gpus_per_run
    if idle:
        warnings.append(
            f"{idle} of {allocation.gpus} GPUs never run anything: {layout.gpus_per_run} GPUs "
            f"per run does not divide the allocation evenly"
        )
    if runs < layout.slots:
        warnings.append(
            f"Only {runs} runs for {layout.slots} concurrent slots, so "
            f"{layout.slots - runs} slot{'s' if layout.slots - runs > 1 else ''} stay idle"
        )
    elif runs % layout.slots:
        last = runs % layout.slots
        idle_slots = layout.slots - last
        warnings.append(
            f"{runs} runs do not divide evenly into {layout.slots} concurrent slots. The last "
            f"wave runs {last} and leaves {idle_slots} slot{'s' if idle_slots > 1 else ''} "
            f"idle. Use a multiple of {layout.slots} runs to keep every GPU busy"
        )
    return warnings


class RunDirectory:
    """Files that track one run on shared storage.

    `config.json` holds the resolved run, `train.log` collects every attempt's
    output, `claim.json` marks the run as taken, and `done.json` or `failed.json`
    records how the last attempt ended.
    """

    def __init__(self, path: Path) -> None:
        """Point at the run's directory; nothing is created yet."""
        self.path = path

    @property
    def config(self) -> Path:
        """Return the resolved run config path."""
        return self.path / "config.json"

    @property
    def log(self) -> Path:
        """Return the shared training log path."""
        return self.path / "train.log"

    @property
    def claim(self) -> Path:
        """Return the claim marker path."""
        return self.path / "claim.json"

    @property
    def done(self) -> Path:
        """Return the success marker path."""
        return self.path / "done.json"

    @property
    def failed(self) -> Path:
        """Return the failure marker path."""
        return self.path / "failed.json"

    def checkpoints(self) -> list[Path]:
        """Return this run's saved checkpoints."""
        return sorted((self.path / "checkpoints").glob("checkpoint-*"))

    def read(self, path: Path) -> dict[str, Any] | None:
        """Return a marker's JSON, or `None` if it is absent or half written."""
        try:
            return json.loads(path.read_text())
        except FileNotFoundError, json.JSONDecodeError:
            return None

    def write(self, path: Path, record: dict[str, Any] | str) -> None:
        """Replace a file atomically, so readers never see a partial file."""
        text = record if isinstance(record, str) else json.dumps(record, indent=2) + "\n"
        staging = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
        staging.write_text(text)
        staging.replace(path)


def now() -> str:
    """Return the current UTC time for markers."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def current_owner(slot: int) -> dict[str, Any]:
    """Describe this launcher process for a claim marker."""
    host = socket.gethostname()
    job = os.environ.get("SLURM_JOB_ID") or f"local-{host}-{os.getpid()}"
    return {"job": job, "host": host, "pid": os.getpid(), "slot": slot, "claimed_at": now()}


def claim_is_live(claim: dict[str, Any] | None) -> bool:
    """Return whether the launcher that wrote a claim may still be running its run.

    Claims from this job are live. A local claim is stale when its process has
    exited on this host. A Slurm claim is stale when `squeue` no longer lists its
    job. When liveness cannot be checked, the claim counts as live, so a run is
    never trained twice at once.
    """
    if claim is None:
        # Half written by a launcher that is creating it right now.
        return True
    job = str(claim.get("job", ""))
    if job == (os.environ.get("SLURM_JOB_ID") or f"local-{socket.gethostname()}-{os.getpid()}"):
        return True
    if job.startswith("local-"):
        if claim.get("host") != socket.gethostname():
            return True
        try:
            os.kill(int(claim["pid"]), 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    try:
        result = subprocess.run(
            ["squeue", "--noheader", "--jobs", job, "--format=%T"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except OSError, subprocess.TimeoutExpired:
        logger.warning("Cannot run squeue to check claim by job %s; leaving it alone", job)
        return True
    if result.returncode != 0 and "Invalid job id" not in result.stderr:
        logger.warning("squeue failed for job %s; leaving its claim alone", job)
        return True
    return bool(result.stdout.strip())


def try_claim(
    run: RunDirectory,
    owner: dict[str, Any],
    is_live: Callable[[dict[str, Any] | None], bool] = claim_is_live,
) -> bool:
    """Atomically take a run, replacing a claim whose launcher is gone.

    Creating the marker with `O_EXCL` lets only one launcher win, even across
    hosts sharing the filesystem. A stale claim is first renamed aside, so two
    launchers cannot both replace it.
    """
    for _ in range(2):
        try:
            descriptor = os.open(run.claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            if is_live(run.read(run.claim)):
                return False
            try:
                run.claim.rename(run.path / f"claim.stale.{uuid.uuid4().hex}.json")
            except FileNotFoundError:
                return False
            continue
        with os.fdopen(descriptor, "w") as handle:
            json.dump(owner, handle, indent=2)
        return True
    return False


def run_state(run: RunDirectory) -> RunState:
    """Return a run's state from its markers."""
    if run.done.exists():
        return "done"
    if run.claim.exists():
        return "running" if claim_is_live(run.read(run.claim)) else "interrupted"
    if run.failed.exists():
        return "failed"
    return "interrupted" if run.checkpoints() else "pending"


def write_configs(sweep: Sweep, experiments: Sequence[ExperimentConfig]) -> list[RunDirectory]:
    """Write each resolved run config, refusing changes to runs that already trained.

    Raises:
        `ValueError`: If a run with checkpoints now resolves to a different config,
            since resuming would then mix two configurations.
    """
    runs = []
    for experiment in experiments:
        run = RunDirectory(sweep.sweep.directory / "runs" / experiment.name)
        run.path.mkdir(parents=True, exist_ok=True)
        text = experiment_json(experiment)
        if run.config.exists() and run.config.read_text() != text and run.checkpoints():
            raise ValueError(
                f"{experiment.name} already has checkpoints from a different config. "
                "Restore the sweep file, or rename the run so it starts fresh."
            )
        if not run.config.exists() or run.config.read_text() != text:
            run.write(run.config, text)
        runs.append(run)
    return runs


@dataclass(frozen=True)
class Slot:
    """One concurrent run position: its index and, for local launches, its GPUs."""

    index: int
    devices: tuple[int, ...] = ()


def train_command(
    run: RunDirectory,
    layout: SlotLayout,
    slot: Slot,
    *,
    mode: Literal["srun", "local"],
    master_port: int,
    srun_args: Sequence[str] = (),
    python: str = sys.executable,
) -> list[str]:
    """Return the command that trains one run in one slot.

    In Slurm mode, `srun` starts one task per node with `--exact`, so concurrent
    runs get separate GPUs and CPUs. Each task starts `torchrun` for its node;
    the first node of the step hosts the rendezvous. Local mode runs `torchrun`
    directly on the slot's GPUs.
    """
    entry = ["-m", "flock.training.experiments.scaling_law.train", str(run.config)]
    if mode == "local":
        # A fixed loopback address avoids hostname lookups; each slot gets its own port.
        return [
            python,
            "-m",
            "torch.distributed.run",
            "--nnodes=1",
            "--node-rank=0",
            f"--nproc-per-node={layout.gpus_per_run}",
            "--master-addr=127.0.0.1",
            f"--master-port={master_port + slot.index}",
            *entry,
        ]

    torchrun = [
        python,
        "-m",
        "torch.distributed.run",
        f"--nnodes={layout.nodes_per_run}",
        f"--nproc-per-node={layout.gpus_per_node_used}",
        f"--master-port={master_port + slot.index}",
        *entry,
    ]
    # SLURM_PROCID numbers this step's tasks from zero, one task per node.
    script = (
        'first=$(scontrol show hostnames "$SLURM_STEP_NODELIST" | head -n 1)\n'
        f'exec {shlex.join(torchrun)} --node-rank="$SLURM_PROCID" --master-addr="$first"'
    )
    resources = [f"--gpus-per-task={layout.gpus_per_node_used}"]
    gpus_per_node = int(os.environ.get("SLURM_GPUS_ON_NODE") or layout.gpus_per_node_used)
    share = layout.gpus_per_node_used / gpus_per_node
    # With --exact a step gets one CPU unless it asks for more, so take this run's share.
    cpus = os.environ.get("SLURM_CPUS_ON_NODE")
    if cpus:
        resources.append(f"--cpus-per-task={max(1, int(int(cpus) * share))}")
    memory = os.environ.get("SLURM_MEM_PER_NODE")
    if memory:
        resources.append(f"--mem={max(1, int(int(memory) * share))}M")
    return [
        "srun",
        "--exact",
        "--kill-on-bad-exit=1",
        f"--job-name={run.path.name}",
        f"--nodes={layout.nodes_per_run}",
        f"--ntasks={layout.nodes_per_run}",
        "--ntasks-per-node=1",
        *resources,
        *srun_args,
        "bash",
        "-c",
        script,
    ]


class Launcher:
    """Keep every slot busy until each run is done or has used its retries.

    Args:
        sweep (`Sweep`): Loaded sweep file.
        runs (`list[RunDirectory]`): Run directories, in launch order.
        layout (`SlotLayout`): Slot sizes for this allocation.
        mode (`str`): `"srun"` inside Slurm, or `"local"` on one machine.
        retries (`int`, *optional*, defaults to `0`): Extra attempts after a failure.
        dry_run (`bool`, *optional*, defaults to `False`): Print the first wave's commands only.
    """

    def __init__(
        self,
        sweep: Sweep,
        runs: list[RunDirectory],
        layout: SlotLayout,
        *,
        mode: Literal["srun", "local"],
        retries: int = 0,
        dry_run: bool = False,
    ) -> None:
        """Queue the runs that are not done."""
        self.sweep = sweep
        self.layout = layout
        self.mode = mode
        self.retries = retries
        self.dry_run = dry_run
        self.queue = [run for run in runs if not run.done.exists()]
        self.lock = threading.Lock()
        self.processes: dict[int, subprocess.Popen[bytes]] = {}
        self.stopping = threading.Event()
        self.results: dict[str, int] = {}

    def slots(self) -> list[Slot]:
        """Return each slot, giving local slots disjoint GPU indices."""
        if self.mode == "srun":
            return [Slot(index) for index in range(self.layout.slots)]
        size = self.layout.gpus_per_run
        return [
            Slot(index, tuple(range(index * size, (index + 1) * size)))
            for index in range(self.layout.slots)
        ]

    def next_runs(self) -> Iterator[RunDirectory]:
        """Yield runs to try, shared by every slot thread."""
        while not self.stopping.is_set():
            with self.lock:
                if not self.queue:
                    return
                run = self.queue.pop(0)
            yield run

    def command(self, run: RunDirectory, slot: Slot) -> list[str]:
        """Return the slot's command for a run."""
        return train_command(
            run,
            self.layout,
            slot,
            mode=self.mode,
            master_port=self.sweep.sweep.master_port,
            srun_args=self.sweep.sweep.srun_args,
        )

    def attempt(self, run: RunDirectory, slot: Slot) -> int:
        """Train a run once in a slot and return its exit code."""
        command = self.command(run, slot)
        env = dict(os.environ)
        if slot.devices:
            env["CUDA_VISIBLE_DEVICES"] = ",".join(map(str, slot.devices))
        with run.log.open("ab") as log:
            log.write(f"\n==> {now()} slot {slot.index}: {shlex.join(command)}\n".encode())
            log.flush()
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env)
            with self.lock:
                self.processes[slot.index] = process
            try:
                return process.wait()
            finally:
                with self.lock:
                    self.processes.pop(slot.index, None)

    def work(self, slot: Slot) -> None:
        """Claim and train runs in one slot until the queue is empty."""
        for run in self.next_runs():
            if not try_claim(run, current_owner(slot.index)):
                logger.info("Slot %d: %s is claimed elsewhere, skipping", slot.index, run.path.name)
                continue
            code = 1
            try:
                for attempt in range(self.retries + 1):
                    logger.info(
                        "Slot %d: training %s (attempt %d)", slot.index, run.path.name, attempt + 1
                    )
                    code = self.attempt(run, slot)
                    if code == 0 or self.stopping.is_set():
                        break
                if self.stopping.is_set():
                    # Keep the claim; once this job ends, the next launch resumes the run.
                    return
                record = {"job": current_owner(slot.index)["job"], "exit_code": code, "at": now()}
                run.failed.unlink(missing_ok=True)
                run.write(run.done if code == 0 else run.failed, record)
                self.results[run.path.name] = code
                logger.info("Slot %d: %s exited with %d", slot.index, run.path.name, code)
            finally:
                if not self.stopping.is_set():
                    run.claim.unlink(missing_ok=True)

    def stop(self, signum: int, frame: FrameType | None) -> None:
        """Stop claiming runs and end running ones, keeping their claims and checkpoints."""
        logger.warning("Received signal %d; stopping runs", signum)
        self.stopping.set()
        with self.lock:
            for process in self.processes.values():
                process.terminate()

    def launch(self) -> dict[str, int]:
        """Run every slot until the queue is empty, and return each run's exit code."""
        slots = self.slots()
        if self.dry_run:
            for slot, run in zip(slots, self.queue, strict=False):
                print(shlex.join(self.command(run, slot)))
            return {}
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        threads = [
            threading.Thread(target=self.work, args=(slot,), name=f"slot-{slot.index}")
            for slot in slots
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return self.results


def local_gpu_count() -> int:
    """Return the GPUs visible to this process."""
    import torch

    return torch.cuda.device_count()


def print_plan(experiments: Sequence[ExperimentConfig], sweep: Sweep) -> None:
    """Print each run's name and the settings that vary."""
    variants = sweep.sweep.variants()
    width = max(len(experiment.name) for experiment in experiments)
    for experiment, overrides in zip(experiments, variants, strict=True):
        settings = ", ".join(f"{key}={json.dumps(value)}" for key, value in overrides.items())
        print(f"{experiment.name:<{width}}  {settings}")


def check_tokens(experiments: Sequence[ExperimentConfig], world_size: int) -> int:
    """Print the update that reaches each run's token targets; return 1 if any cannot."""
    from flock.training import TokenChunkDataset

    from .trainer import step_target_counts, steps_for_tokens

    failures = 0
    for experiment in experiments:
        data = experiment.data
        if not Path(data.cache_dir).exists():
            print(f"{experiment.name}: cache {data.cache_dir} is not prepared yet")
            failures += 1
            continue
        dataset = TokenChunkDataset(cache_dir=data.cache_dir, chunk_length=data.chunk_length)
        counts = step_target_counts(experiment.trainer_config(), dataset, world_size)
        try:
            steps = steps_for_tokens(counts, experiment.token_targets())
        except ValueError as error:
            print(f"{experiment.name}: {error}")
            failures += 1
            continue
        targets = ", ".join(
            f"{tokens} tokens at update {step}"
            for tokens, step in zip(experiment.token_targets(), steps, strict=True)
        )
        print(f"{experiment.name}: {targets}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    """Run the `plan`, `prepare`, `launch` or `status` command."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="Resolve and check every run without training.")
    prepare = commands.add_parser("prepare", help="Build missing token caches.")
    launch = commands.add_parser("launch", help="Train runs in the current allocation.")
    status = commands.add_parser("status", help="Show each run's state.")
    for command in (plan, prepare, launch, status):
        command.add_argument("sweep", type=Path, help="Sweep YAML file.")
    plan.add_argument("--nodes", type=int, help="Nodes you will request.")
    plan.add_argument(
        "--check-tokens",
        action="store_true",
        help="Map each run's token targets to updates using its prepared cache.",
    )
    for command in (plan, launch):
        command.add_argument("--gpus-per-node", type=int, help="GPUs on each node.")
    launch.add_argument("--local", action="store_true", help="Use this machine's GPUs, no srun.")
    launch.add_argument("--retries", type=int, default=0, help="Extra attempts after a failure.")
    launch.add_argument("--dry-run", action="store_true", help="Print the first wave's commands.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    sweep = Sweep.load(args.sweep)
    experiments = sweep.resolve()

    if args.command == "plan":
        print_plan(experiments, sweep)
        print(f"\n{len(experiments)} runs, each on {sweep.sweep.gpus_per_run} GPU(s).")
        if args.nodes and args.gpus_per_node:
            allocation = Allocation(args.nodes, args.gpus_per_node)
            layout = SlotLayout.plan(allocation, sweep.sweep.gpus_per_run)
            waves = -(-len(experiments) // layout.slots)
            print(f"{layout.slots} at a time on {allocation.gpus} GPUs, in {waves} waves.")
            for warning in capacity_warnings(len(experiments), allocation, layout):
                print(f"WARNING: {warning}")
        if args.check_tokens:
            return check_tokens(experiments, sweep.sweep.gpus_per_run)
        return 0

    if args.command == "prepare":
        from .train import prepare_cache

        seen = set()
        for experiment in experiments:
            data = experiment.data
            if data.cache_dir in seen or Path(data.cache_dir).exists():
                seen.add(data.cache_dir)
                continue
            seen.add(data.cache_dir)
            logger.info("Preparing %s", data.cache_dir)
            prepare_cache(data, experiment.model)
        return 0

    runs = write_configs(sweep, experiments)
    if args.command == "status":
        for run in runs:
            print(f"{run_state(run):<12} {run.path.name}")
        return 0

    if args.local:
        allocation = Allocation(1, args.gpus_per_node or local_gpu_count())
    else:
        allocation = Allocation.from_slurm(args.gpus_per_node)
    layout = SlotLayout.plan(allocation, sweep.sweep.gpus_per_run)
    if args.local and layout.nodes_per_run > 1:
        raise ValueError("Local launches cannot span nodes; lower gpus_per_run")
    remaining = [run for run in runs if not run.done.exists()]
    logger.info(
        "%d of %d runs left, %d at a time on %d GPUs",
        len(remaining),
        len(runs),
        layout.slots,
        allocation.gpus,
    )
    for warning in capacity_warnings(len(remaining), allocation, layout):
        logger.warning(warning)
    launcher = Launcher(
        sweep,
        runs,
        layout,
        mode="local" if args.local else "srun",
        retries=args.retries,
        dry_run=args.dry_run,
    )
    results = launcher.launch()
    failed = sorted(name for name, code in results.items() if code != 0)
    if failed:
        logger.error("Failed runs: %s. See each run's train.log.", ", ".join(failed))
    return 1 if failed or launcher.stopping.is_set() else 0


if __name__ == "__main__":
    sys.exit(main())
