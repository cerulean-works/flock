"""Keep the 750M phase-1 sweep (and its 7.5B early-decay branches) submitted until every run is done.

Runs as the systemd user unit llama750m-watcher (LimitNOFILE=131072); log in
logs/llama750m-watcher.log, state in logs/llama750m-watcher-state.json.

Every 10 minutes, for each job below that is not queued or running (by name, in squeue):
  * resubmit it if one of its main (ungated) sweeps still has a run that is not done, not
    failed and not claimed by a live job (a resubmitted job resumes from the latest checkpoint);
  * if all main runs are done but branch runs remain whose parent 6B checkpoint exists and that
    no live job holds, and no llama750m job is queued or running, submit one right-sized
    leftover job (llama750m-branches, gb200_72gpu) for them.
Each job name is submitted at most MAX_SUBMITS times by the watcher, so a run that fails
deterministically cannot loop. Every submission is appended to logs/jobs-<date>.txt.
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
STATE = LOGS / "llama750m-watcher-state.json"
POLL = 600
MAX_SUBMITS = 4
LAUNCHER = str(CONFIGS / "multi_launch_gated.sbatch")
R = {"72": ["-p", "gb200_72gpu", "--gpus-per-node=4", "--cpus-per-task=136", "--mem=900000M"],
     "r1": ["-p", "gb200-r1", "--gpus-per-node=4", "--cpus-per-task=48", "--mem=800000M"],
     "r2": ["-p", "gb200-r2", "--gpus-per-node=4", "--cpus-per-task=48", "--mem=800000M"]}


def y(name: str) -> str:
    return str(CONFIGS / f"llama750m-{name}.yaml")


# Same packing as the initial submission (see logs/jobs-2026-11-25.txt).
JOBS = {
    "llama750m-r2": {"part": "r2", "nodes": 12, "time": "10:00:00",
                     "specs": [f"{y('bs2m')}:1", f"{y('bs1m')}:1", f"{y('bs2m-d7p5b')}:1:gated",
                               f"{y('bs1m-d7p5b')}:1:gated", f"{y('bs524k-d7p5b')}:2:gated"]},
    "llama750m-r1": {"part": "r1", "nodes": 8, "time": "8:00:00",
                     "specs": [f"{y('bs1m')}:2", f"{y('bs1m-d7p5b')}:2:gated"]},
    "llama750m-524k": {"part": "72", "nodes": 6, "time": "10:00:00",
                       "specs": [f"{y('bs524k')}:3"]},
}
BRANCH_SWEEPS = [y("bs524k-d7p5b"), y("bs1m-d7p5b"), y("bs2m-d7p5b")]


def say(message: str) -> None:
    print(f"{datetime.now(UTC) + timedelta(hours=8):%m-%d %H:%M} TW {message}", flush=True)


def my_jobs() -> dict[str, str] | None:
    """Return {job id: name} for this user's queued or running jobs, or None if squeue failed."""
    try:
        out = subprocess.run(["squeue", "-a", "-h", "-u", "u4982363", "-o", "%i|%j"],
                             capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return None
    if out.returncode != 0:
        return None
    return dict(line.split("|", 1) for line in out.stdout.split())


def sweep_runs(path: str) -> list[tuple[Path, dict, int]]:
    doc = yaml.safe_load(Path(path).read_text())
    sweep = doc["sweep"]
    return [(Path(sweep["output_root"]) / sweep["name"] / "runs" / name, run, sweep["gpus_per_run"])
            for name, run in zip(run_names(sweep), sweep["runs"], strict=True)]


def run_names(sweep: dict) -> list[str]:
    names = []
    for run in sweep["runs"]:
        name = sweep["name_template"]
        for key, value in run.items():
            text = "_" + "_".join(map(str, value)) + "_" if isinstance(value, list) else str(value)
            name = name.replace("{" + key + "}", text)
        names.append(name)
    return names


def available(run: Path, jobs: dict[str, str]) -> bool:
    """Not done, not failed, and not claimed by a job that is still queued or running."""
    if (run / "done.json").exists() or (run / "failed.json").exists():
        return False
    try:
        claim = json.loads((run / "claim.json").read_text())
    except FileNotFoundError:
        return True
    except json.JSONDecodeError:
        return False
    return str(claim.get("job")) not in jobs


def submit(name: str, part: str, nodes: int, limit: str, specs: list[str], state: dict) -> None:
    command = ["sbatch", "-A", "ACD115198", *R[part], "-N", str(nodes), "-J", name, "-t", limit,
               LAUNCHER, *specs]
    try:
        out = subprocess.run(command, capture_output=True, text=True, timeout=300, cwd=CONFIGS)
        text = (out.stdout + out.stderr).strip()
    except subprocess.TimeoutExpired:
        text = "sbatch timed out"
    match = re.search(r"Submitted batch job (\d+)", text)
    if match is None:
        time.sleep(60)  # A timed-out sbatch may still have submitted; look before retrying.
        found = [jid for jid, n in (my_jobs() or {}).items() if n == name]
        if not found:
            say(f"{name}: sbatch failed ({text!r}); will retry")
            return
        job_id = found[0]
    else:
        job_id = match.group(1)
    state.setdefault(name, []).append(job_id)
    STATE.write_text(json.dumps(state, indent=2) + "\n")
    tw = datetime.now(UTC) + timedelta(hours=8)
    with (LOGS / f"jobs-{tw:%Y-%m-%d}.txt").open("a") as handle:
        handle.write(f"--- {tw:%H:%M} TW {tw:%m-%d}: {name} {job_id} ({part}, {nodes} nodes/{4 * nodes} GPU, "
                     f"{limit}) resubmitted by llama750m_watcher: {' '.join(command)}\n")
    say(f"{name}: submitted {job_id}")


def main() -> None:
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    say(f"watching; submissions so far: {state or 'none'}")
    while True:
        jobs = my_jobs()
        if jobs is None:
            say("squeue failed; retrying next poll")
            time.sleep(POLL)
            continue
        names = set(jobs.values())
        main_left = 0
        for name, spec in JOBS.items():
            mains = [s.rsplit(":", 1)[0] for s in spec["specs"] if not s.endswith(":gated")]
            todo = [r for m in mains for r, _, _ in sweep_runs(m) if available(r, jobs)]
            main_left += sum(not (r / "done.json").exists() for m in mains for r, _, _ in sweep_runs(m))
            if not todo or name in names:
                continue
            if len(state.get(name, [])) >= MAX_SUBMITS:
                say(f"{name}: {len(todo)} runs left but already submitted {MAX_SUBMITS} times; not resubmitting")
                continue
            submit(name, spec["part"], spec["nodes"], spec["time"], spec["specs"], state)
            names.add(name)
        branches_left = [(r, run, g, b) for b in BRANCH_SWEEPS for r, run, g in sweep_runs(b)
                         if not (r / "done.json").exists()]
        ready = [(r, g, b) for r, run, g, b in branches_left
                 if available(r, jobs) and Path(run["branch_from"]).is_dir()]
        if ready and main_left == 0 and not any(n.startswith("llama750m") for n in names):
            name = "llama750m-branches"
            if len(state.get(name, [])) < MAX_SUBMITS:
                nodes = max(g for _, g, _ in ready) // 4
                sweeps = sorted({b for _, _, b in ready})
                submit(name, "72", nodes, "4:00:00", [f"{b}:1:gated" for b in sweeps], state)
        failed = [r.name for s in [*BRANCH_SWEEPS, y("bs524k"), y("bs1m"), y("bs2m")]
                  for r, _, _ in sweep_runs(s) if (r / "failed.json").exists() and not (r / "done.json").exists()]
        if failed:
            say(f"failed runs (not resubmitted for): {failed}")
        if main_left == 0 and not branches_left:
            say("all 18 runs done; exiting")
            return
        if not any(n.startswith("llama750m") for n in names) and not ready and main_left == 0:
            say(f"nothing runnable: {len(branches_left)} branch runs left, none ready; waiting")
        time.sleep(POLL)


if __name__ == "__main__":
    main()
