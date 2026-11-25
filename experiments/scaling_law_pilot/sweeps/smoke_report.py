"""Summarize a smoke sweep: throughput, token accounting, loss, GPU memory/utilization, W&B."""

import ast
import csv
import datetime as dt
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

job = sys.argv[1]
root = Path("/work/u4982363/sweeps/llama200m-smoke/runs")
logs = Path("/work/u4982363/sweeps/logs")
PEAK = 2.5e15
NON_EMBED = 202_328_832 - 128256 * 768  # matmul params excluding the tied embedding...
MATMUL = NON_EMBED + 128256 * 768  # ...plus the lm_head, which reuses it as a matmul.
LAYERS, D = 12, 768

for run in sorted(root.iterdir()):
    text = (run / "train.log").read_text(errors="replace")
    records = []
    for line in text.splitlines():
        m = re.match(r"(\d\d-\d\d \d\d:\d\d:\d\d) INFO \S+ rank:0 (\{'global_step'.*\})$", line)
        if m:
            when = dt.datetime.strptime("2026-" + m.group(1), "%Y-%m-%d %H:%M:%S")
            records.append((when, ast.literal_eval(m.group(2))))
    config = json.loads((run / "config.json").read_text())
    bt = config["trainer"]["batch_tokens"]
    tokens_ok = all(r["train/total_tokens"] == bt * int(r["global_step"]) for _, r in records)
    steady = [(t, r) for t, r in records if r["global_step"] >= 50]
    if len(steady) >= 2:
        (t0, r0), (t1, r1) = steady[0], steady[-1]
        steps = r1["global_step"] - r0["global_step"]
        seconds = (t1 - t0).total_seconds()
        rate = steps * bt / seconds
        # Causal attention FLOPs depend on document lengths; 6N dominates here, so report
        # the matmul MFU (6 * matmul params per token) as a slight underestimate.
        mfu = 6 * MATMUL * rate / PEAK
        perf = f"{1000 * seconds / steps:.0f} ms/step, {rate / 1e3:.0f}k tok/s, MFU>={100 * mfu:.1f}%"
    else:
        perf = "not enough steps"
    loss = [round(r["train/loss_mean"], 2) for _, r in records[:: max(1, len(records) // 6)]]
    status = "done" if (run / "done.json").exists() else "failed" if (run / "failed.json").exists() else "running"
    wandb = re.findall(r"wandb: (?:🚀 View run|View run|Syncing run|ERROR|Network error)[^\n]*", text)
    ckpts = sorted(p.name for p in (run / "checkpoints").glob("checkpoint-*"))
    print(f"{run.name}\n  {status}; last step {records[-1][1]['global_step'] if records else '-'}; {perf}")
    print(f"  tokens == step*batch at every log: {tokens_ok}; loss {loss}")
    print(f"  checkpoints {ckpts}; wandb {wandb[:2] or 'offline/no lines'}")

usage = defaultdict(list)
for f in logs.glob(f"*-{job}-gpu-*.csv"):
    for row in csv.reader(f.open()):
        if len(row) == 4:
            usage[int(row[1])].append((int(row[2].split()[0]), int(row[3].split()[0])))
for gpu, samples in sorted(usage.items()):
    busy = [u for u, m in samples if m > 1000]
    print(
        f"GPU {gpu}: peak mem {max(m for _, m in samples) / 1024:.0f} GiB, "
        f"util while loaded mean {sum(busy) / max(len(busy), 1):.0f}% "
        f"(samples <50%: {sum(u < 50 for u in busy)}/{len(busy)})"
    )
