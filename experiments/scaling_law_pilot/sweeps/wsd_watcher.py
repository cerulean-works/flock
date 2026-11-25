"""Submit the WSD decay-branch jobs when their parents are ready. Not started automatically.

    nohup ~/experiments/.venvs/slp-wsd-h200/bin/python /work/u4982363/sweeps/configs/wsd_watcher.py \
      >> /work/u4982363/sweeps/logs/wsd-watcher.log 2>&1 &

Decay job 1 (26 branches, 2 nodes): once every parent checkpoint it needs is saved and both 200M
seeds have saved T = 120 tok/param (24B tokens). Decay job 2 (14 branches, 6 nodes): once all
four main runs are done. Each job is submitted once (state in wsd-watcher-state.json), only
while fewer than 3 of the account's H200 jobs are queued or running, and never while a job
with its name exists. sbatch output that is not a clean submission is re-checked in squeue
before any retry, since the controller can time out and still submit.
"""

import json
import re
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path("/work/u4982363/sweeps")
CONFIGS = ROOT / "configs"
LOGS = ROOT / "logs"
STATE = LOGS / "wsd-watcher-state.json"
POLL = 600
H200_CAP = 3
SBATCH = ["sbatch", "-A", "ACD115198", "-p", "h200_256gpu", "--gpus-per-node=8",
          "--cpus-per-task=104", "--mem=1800000M", "--exclude=25a-hgpn[166-167]"]  # 166-167: NCCL bootstrap "No route to host" on own IPoIB (11-25)
MAIN_RUNS = [ROOT / f"wsd-{size}" / "runs" / f"wsd{size}-s{seed}"
             for size in ("100m", "200m") for seed in (0, 1)]
T120_200M = [ROOT / "wsd-200m" / "runs" / f"wsd200m-s{seed}" / "checkpoints" / "checkpoint-61035"
             for seed in (0, 1)]
JOBS = {
    "wsd-decay1": {"nodes": 2, "time": "8:00:00",
                   "sweeps": [f"{CONFIGS}/wsd-decay1-100m.yaml:1", f"{CONFIGS}/wsd-decay1-200m.yaml:1"],
                   "ready": "parents and 200M T=120"},
    "wsd-decay2": {"nodes": 6, "time": "12:00:00",
                   "sweeps": [f"{CONFIGS}/wsd-decay2-100m.yaml:3", f"{CONFIGS}/wsd-decay2-200m.yaml:3"],
                   "ready": "main runs done"},
}


def say(message: str) -> None:
    taiwan = datetime.now(UTC) + timedelta(hours=8)
    print(f"{taiwan:%m-%d %H:%M} TW {message}", flush=True)


def saved(checkpoint: Path) -> bool:
    """A published checkpoint has its flock.json and weights (staging dirs are renamed in)."""
    return (checkpoint / "flock.json").exists() and (checkpoint / "model.safetensors").exists()


def parents(job: str) -> list[Path]:
    paths = []
    for spec in JOBS[job]["sweeps"]:
        sweep = yaml.safe_load(Path(spec.rsplit(":", 1)[0]).read_text())
        paths += [Path(run["branch_from"]) for run in sweep["sweep"]["runs"]]
    return paths


def ready(job: str) -> tuple[bool, str]:
    missing = [p for p in parents(job) if not saved(p)]
    if job == "wsd-decay1":
        missing += [p for p in T120_200M if not saved(p)]
    else:
        missing += [run / "done.json" for run in MAIN_RUNS if not (run / "done.json").exists()]
    return not missing, f"{len(missing)} missing, e.g. {missing[0]}" if missing else "ready"


def my_h200_jobs() -> list[tuple[str, str]] | None:
    """Return (job id, name) of the account user's H200 jobs, or None if squeue failed."""
    try:
        out = subprocess.run(["squeue", "-a", "-h", "-u", "u4982363", "-p", "h200_256gpu",
                              "-o", "%i|%j"], capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return None
    if out.returncode != 0:
        return None
    return [tuple(line.split("|", 1)) for line in out.stdout.split()]


def submit(job: str, state: dict) -> None:
    jobs = my_h200_jobs()
    if jobs is None:
        say(f"{job}: squeue failed; will retry")
        return
    existing = [jid for jid, name in jobs if name == job]
    if existing:
        state[job] = existing[0]
        say(f"{job}: already queued as {existing[0]}; recording it")
        return
    if len(jobs) >= H200_CAP:
        say(f"{job}: ready, but {len(jobs)} H200 jobs are queued or running (cap {H200_CAP})")
        return
    spec = JOBS[job]
    command = [*SBATCH, "-N", str(spec["nodes"]), "-J", job, "-t", spec["time"],
               str(CONFIGS / "wsd_launch.sbatch"), *spec["sweeps"]]
    try:
        out = subprocess.run(command, capture_output=True, text=True, timeout=300, cwd=CONFIGS)
        text = (out.stdout + out.stderr).strip()
    except subprocess.TimeoutExpired:
        text = "sbatch timed out"
    match = re.search(r"Submitted batch job (\d+)", text)
    if match is None:
        time.sleep(60)  # A timed-out sbatch may still have submitted; look before retrying.
        jobs = my_h200_jobs() or []
        found = [jid for jid, name in jobs if name == job]
        if not found:
            say(f"{job}: sbatch failed ({text!r}); will retry")
            return
        job_id = found[0]
    else:
        job_id = match.group(1)
    state[job] = job_id
    STATE.write_text(json.dumps(state, indent=2) + "\n")
    line = (f"--- {(datetime.now(UTC) + timedelta(hours=8)):%H:%M} TW: {job} {job_id} "
            f"(H200, {spec['nodes']} nodes, {spec['time']}) by wsd_watcher: {' '.join(command)}")
    log = LOGS / f"jobs-{(datetime.now(UTC) + timedelta(hours=8)):%Y-%m-%d}.txt"
    with log.open("a") as handle:
        handle.write(line + "\n")
    say(f"{job}: submitted {job_id}")


def main() -> None:
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    say(f"watching; already submitted: {state or 'none'}")
    while any(job not in state for job in JOBS):
        for job in JOBS:
            if job in state:
                continue
            ok, why = ready(job)
            if ok:
                submit(job, state)
                STATE.write_text(json.dumps(state, indent=2) + "\n")
            else:
                say(f"{job}: waiting for {JOBS[job]['ready']}: {why}")
        if any(job not in state for job in JOBS):
            time.sleep(POLL)
    say("both decay jobs submitted; exiting")


if __name__ == "__main__":
    main()
