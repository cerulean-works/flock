"""Watch the sweep jobs in JOBS; print one line per event (completion, failure, stall, progress)."""

import re
import subprocess
import time
from pathlib import Path

JOBS = {"813396": "llama200m-8b-branch-65k-131k", "814405": "llama200m-8b-branch-393k"}
ROOT = Path("/work/u4982363/sweeps")
STALL = 600  # seconds without a new log line once a run has started training
SUMMARY = 1800
STEP = re.compile(rb"'global_step': (\d+)")
ERROR = re.compile(rb"Traceback|OutOfMemoryError|CUDA error|NCCL error|Killed|Segmentation fault")
TERMINAL = ("COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_ME", "NODE_FAIL", "PREEMPTED")

seen_exit: set[str] = set()
errors: dict[Path, int] = {}
stalled: set[Path] = set()
states: dict[str, str] = {}
last_summary = 0.0


def say(message: str) -> None:
    print(time.strftime("%H:%M ") + message, flush=True)


def last_step(log: Path) -> int | None:
    with log.open("rb") as handle:
        handle.seek(max(0, log.stat().st_size - 20000))
        steps = STEP.findall(handle.read())
    return int(steps[-1]) if steps else None


while True:
    now = time.time()
    try:
        out = subprocess.run(
            ["sacct", "-n", "-X", "-P", "-o", "JobID,State", "-j", ",".join(JOBS)],
            capture_output=True, text=True, timeout=60,
        ).stdout
        for line in out.split():
            job, state = line.split("|")
            if states.get(job) != state:
                states[job] = state
                say(f"job {job} {JOBS[job]}: {state}")
    except Exception as error:  # noqa: BLE001
        say(f"sacct failed: {error}")

    progress = {}
    for job, sweep in JOBS.items():
        launcher = ROOT / "logs" / f"{sweep}-{job}.out"
        if launcher.exists():
            for line in launcher.read_text(errors="replace").splitlines():
                if ("exited with" in line or "Failed runs" in line) and line not in seen_exit:
                    seen_exit.add(line)
                    if "exited with 0" not in line:
                        say(f"{sweep}: {line[20:200]}")
        steps = []
        for log in sorted((ROOT / sweep / "runs").glob("*/train.log")):
            run = log.parent
            data = log.read_bytes()
            count = len(ERROR.findall(data))
            if count > errors.get(log, 0):
                match = list(ERROR.finditer(data))[-1]
                snippet = data[match.start() : match.start() + 160].decode(errors="replace").splitlines()[0]
                say(f"ERROR {run.name}: {snippet}")
            errors[log] = count
            step = last_step(log)
            done = (run / "done.json").exists() or (run / "failed.json").exists()
            claim = run / "claim.json"
            if step is None and not done and claim.exists():
                # A run that never reaches its first logged step (hung startup, data, compile).
                waiting = now - claim.stat().st_mtime
                if waiting > 900 and log not in stalled:
                    stalled.add(log)
                    say(f"STALL {run.name}: claimed {waiting / 60:.0f} min ago, no step logged yet")
            if step is not None and not done:
                steps.append(step)
                quiet = now - log.stat().st_mtime
                if quiet > STALL and log not in stalled:
                    stalled.add(log)
                    say(f"STALL {run.name}: no log output for {quiet / 60:.0f} min at step {step}")
                elif quiet <= STALL and log in stalled:
                    stalled.discard(log)
                    say(f"recovered {run.name}: logging again at step {step}")
        finished = sum((r / "done.json").exists() for r in (ROOT / sweep / "runs").glob("*"))
        progress[sweep] = (min(steps, default=0), max(steps, default=0), len(steps), finished)

    if now - last_summary > SUMMARY:
        last_summary = now
        say("progress: " + "; ".join(
            f"{s.removeprefix('llama200m-')} steps {lo}-{hi} ({n} training, {d} done)"
            for s, (lo, hi, n, d) in progress.items()
        ))
    if states and all(s.startswith(TERMINAL) for s in states.values()):
        say("all jobs finished")
        break
    time.sleep(60)
