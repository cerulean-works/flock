"""Submit the 400M production WSD decay job once its last parent checkpoint is saved.

Runs as a systemd user unit (restart-safe: state in wsd-prod-watcher-state.json):

    XDG_RUNTIME_DIR=/run/user/$(id -u) systemd-run --user --unit=wsd-prod-watcher \
      -p Restart=on-failure -p RestartSec=60 \
      /home/u4982363/experiments/.venvs/slp-wsd-h200/bin/python \
      /work/u4982363/sweeps/configs/wsd_prod_watcher.py
    (log: /work/u4982363/sweeps/logs/wsd-prod-watcher.log)

Decay job (wsd-prod-decay-400m.yaml, 6 branches, 8 GB200 nodes = 32 GPUs, 2 branches at a time)
is submitted once every branch parent is saved, the last being checkpoint-82397 (43.2B tokens),
and only while the account's GB200 jobs (all users, running or pending) leave room for 32 GPUs
under the 72-GPU cap. Submitted once, and never while a job with its name exists. sbatch output
that is not a clean submission is re-checked in squeue before any retry, since the controller
can time out and still submit.
"""

import json
import re
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path("/work/u4982363/sweeps")
CONFIGS = ROOT / "configs"
LOGS = ROOT / "logs"
STATE = LOGS / "wsd-prod-watcher-state.json"
LOG = LOGS / "wsd-prod-watcher.log"
POLL = 600
ACCOUNT = "ACD115198"
PARTITION = "gb200_72gpu"
GPU_CAP = 72
JOB = "wsd-prod-decay"
NODES = 8
GPUS = NODES * 4
TIME = "4:00:00"
SWEEP = CONFIGS / "wsd-prod-decay-400m.yaml"
SBATCH = ["sbatch", "-A", ACCOUNT, "-p", PARTITION, "-N", str(NODES), "--gpus-per-node=4",
          "--cpus-per-task=136", "--mem=900000M", "-J", JOB, "-t", TIME,
          str(CONFIGS / "wsd_prod_launch.sbatch"), f"{SWEEP}:2"]


def taiwan() -> datetime:
    return datetime.now(UTC) + timedelta(hours=8)


def say(message: str) -> None:
    with LOG.open("a") as handle:
        handle.write(f"{taiwan():%m-%d %H:%M} TW {message}\n")


def saved(checkpoint: Path) -> bool:
    """A published checkpoint has its flock.json and weights (staging dirs are renamed in)."""
    return (checkpoint / "flock.json").exists() and (checkpoint / "model.safetensors").exists()


def parents() -> list[Path]:
    sweep = yaml.safe_load(SWEEP.read_text())
    return [Path(run["branch_from"]) for run in sweep["sweep"]["runs"]]


def account_jobs() -> list[dict] | None:
    """Return the account's jobs on the partition (any user, running or pending), or None."""
    try:
        out = subprocess.run(["squeue", "-a", "-h", "-p", PARTITION, "-A", ACCOUNT,
                              "-o", "%i|%j|%u|%T|%D|%b"],
                             capture_output=True, text=True, timeout=120, check=False)
    except subprocess.TimeoutExpired:
        return None
    if out.returncode != 0:
        return None
    jobs = []
    for line in out.stdout.splitlines():
        job_id, name, user, state, nodes, tres = line.split("|")
        match = re.search(r"gpu(?::[^:,]+)?:(\d+)", tres)
        per_node = int(match.group(1)) if match else 0
        jobs.append({"id": job_id, "name": name, "user": user, "state": state,
                     "gpus": per_node * int(nodes)})
    return jobs


def submit(state: dict) -> None:
    jobs = account_jobs()
    if jobs is None:
        say(f"{JOB}: squeue failed; will retry")
        return
    existing = [job["id"] for job in jobs if job["name"] == JOB]
    if existing:
        state[JOB] = existing[0]
        say(f"{JOB}: already queued as {existing[0]}; recording it")
        return
    used = sum(job["gpus"] for job in jobs)
    if used + GPUS > GPU_CAP:
        by_user: dict[str, int] = {}
        for job in jobs:
            by_user[job["user"]] = by_user.get(job["user"], 0) + job["gpus"]
        say(f"{JOB}: ready, but the account holds {used} GB200 GPUs {by_user}; "
            f"{GPUS} more would exceed {GPU_CAP}; will retry")
        return
    try:
        out = subprocess.run(SBATCH, capture_output=True, text=True, timeout=300, cwd=CONFIGS,
                             check=False)
        text = (out.stdout + out.stderr).strip()
    except subprocess.TimeoutExpired:
        text = "sbatch timed out"
    match = re.search(r"Submitted batch job (\d+)", text)
    if match is None:
        time.sleep(60)  # A timed-out sbatch may still have submitted; look before retrying.
        found = [job["id"] for job in account_jobs() or [] if job["name"] == JOB]
        if not found:
            say(f"{JOB}: sbatch failed ({text!r}); will retry")
            return
        job_id = found[0]
    else:
        job_id = match.group(1)
    state[JOB] = job_id
    STATE.write_text(json.dumps(state, indent=2) + "\n")
    line = (f"--- {taiwan():%H:%M} TW: {JOB} {job_id} (GB200, {NODES} nodes/{GPUS} GPUs, {TIME}; "
            f"account then held {used} GPUs) by wsd_prod_watcher: {' '.join(SBATCH)}")
    with (LOGS / f"jobs-{taiwan():%Y-%m-%d}.txt").open("a") as handle:
        handle.write(line + "\n")
    say(f"{JOB}: submitted {job_id}")


def main() -> int:
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    say(f"watching; already submitted: {state or 'none'}")
    while JOB not in state:
        missing = [p for p in parents() if not saved(p)]
        if missing:
            say(f"{JOB}: waiting for parents: {len(missing)} missing, e.g. {missing[0]}")
        else:
            submit(state)
            STATE.write_text(json.dumps(state, indent=2) + "\n")
        if JOB not in state:
            time.sleep(POLL)
    say(f"{JOB} submitted ({state[JOB]}); exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
