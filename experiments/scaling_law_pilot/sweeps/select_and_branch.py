"""Pick the 3 best runs per batch size and write sweeps that branch them to 8B tokens.

Ranking uses final training loss, averaged over each run's last 1% of updates (every run in a
batch size saw the same data in the same order, so training loss compares fairly). Each branch
starts from its parent's 3B-token checkpoint, which lies in the constant-LR phase, and trains to
8B tokens under warmup 1% / constant to 90% / linear decay over the last 10%.

    python select_and_branch.py            # print the ranking and selection only
    python select_and_branch.py --write    # also write the two branch sweep files
    python select_and_branch.py --draft    # write draft-*.yaml from finished runs, to check them
    python select_and_branch.py --write --sizes 65k,131k   # only these batch sizes; each must
                                                          # have finished every run
"""

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path("/work/u4982363/sweeps")
CONFIGS = ROOT / "configs"
SIZES = {"65k": 1, "131k": 1, "262k": 2, "393k": 2}  # GPUs per run
BETA2 = ["0.95", "0.99"]
PER_SIZE = 3
LOG = re.compile(r"rank:0 (\{'global_step'.*\})")


def final_loss(run: Path) -> float | None:
    """Token-weighted training loss over the last 1% of updates; None unless the run is done."""
    if not (run / "done.json").exists():
        return None
    records = [ast.literal_eval(m.group(1)) for m in LOG.finditer((run / "train.log").read_text(errors="replace"))]
    last = int(records[-1]["global_step"])
    tail = [r for r in records if r["global_step"] > last * 0.99]
    return sum(r["train/loss_mean"] * r["train/tokens"] for r in tail) / sum(r["train/tokens"] for r in tail)


def checkpoint_at(run: Path, tokens: int) -> Path:
    entry = next(e for e in json.loads((run / "checkpoints/token_checkpoints.json").read_text()) if e["tokens"] == tokens)
    path = run / "checkpoints" / f"checkpoint-{entry['step']}"
    if not path.is_dir():
        raise FileNotFoundError(path)
    return path


chosen = list(SIZES)
if "--sizes" in sys.argv:
    chosen = sys.argv[sys.argv.index("--sizes") + 1].split(",")
    unknown = set(chosen) - set(SIZES)
    if unknown:
        sys.exit(f"Unknown batch sizes {sorted(unknown)}; use {list(SIZES)}")

selected: dict[int, list[dict]] = {1: [], 2: []}
groups: dict[int, list[str]] = {1: [], 2: []}
missing = []
for size, gpus in SIZES.items():
    if size not in chosen:
        continue
    groups[gpus].append(size)
    ranked = []
    for beta2 in BETA2:
        sweep = ROOT / f"llama200m-bs{size}-b2{beta2}" / "runs"
        runs = sorted(sweep.iterdir()) if sweep.exists() else []
        if len(runs) != 24:
            missing.append(f"{size} b2 {beta2}: {len(runs)} runs")
        for run in runs:
            loss = final_loss(run)
            if loss is None:
                missing.append(f"{run.name} not done")
                continue
            ranked.append((loss, run))
    ranked.sort()
    print(f"\n{size} ({len(ranked)} finished runs); top {PER_SIZE + 2}:")
    for i, (loss, run) in enumerate(ranked[: PER_SIZE + 2]):
        print(f"  {'*' if i < PER_SIZE else ' '} {loss:.4f}  {run.name}")
    for loss, run in ranked[:PER_SIZE]:
        config = json.loads((run / "config.json").read_text())
        selected[gpus].append({"parent": run, "config": config, "loss": loss})

if missing:
    print("\nNOT READY:", *missing[:10], sep="\n  ")
draft = "--draft" in sys.argv
if "--write" not in sys.argv and not draft:
    sys.exit(0)
if missing and not draft:
    sys.exit("Refusing to write branch sweeps until every run has finished.")

for gpus, picks in selected.items():
    if not picks:
        continue
    name = f"llama200m-8b-branch-{'-'.join(groups[gpus])}"
    first = picks[0]["config"]
    runs = []
    for pick in picks:
        c = pick["config"]
        runs.append(
            {
                "trainer.batch_tokens": c["trainer"]["batch_tokens"],
                "trainer.save_steps": c["trainer"]["save_steps"],
                "optimizer.betas": c["optimizer"]["betas"],
                "optimizer.muon_lr": c["optimizer"]["muon_lr"],
                "optimizer.lr": c["optimizer"]["lr"],
                "branch_from": str(checkpoint_at(pick["parent"], 3_000_000_000)),
            }
        )
    base = {key: first[key] for key in ("data", "model", "optimizer", "trainer")}
    base["schedule"] = [
        {"start": 0, "end": 0.01, "start_ratio": 0.0, "end_ratio": 1.0},
        {"start": 0.01, "end": 0.9, "start_ratio": 1.0, "end_ratio": 1.0},
        {"start": 0.9, "end": 1.0, "start_ratio": 1.0, "end_ratio": 0.0, "interpolation": "linear"},
    ]
    base["stop_tokens"] = "8B"
    base["save_tokens"] = ["4B", "5B", "6B", "7B"]
    sweep = {
        "sweep": {
            "name": name,
            "output_root": str(ROOT),
            "gpus_per_run": gpus,
            # batch_tokens is per GPU; spell out the GPUs so names show the global batch.
            "name_template": (
                f"llama200m-8b-bs{'' if gpus == 1 else f'{gpus}x'}{{trainer.batch_tokens}}"
                "-b2{optimizer.betas}-mlr{optimizer.muon_lr}-alr{optimizer.lr}"
            ),
            "runs": runs,
        },
        "base": base,
    }
    if not runs:
        continue
    path = CONFIGS / f"{'draft-' if draft else ''}{name}.yaml"
    path.write_text(json.dumps(sweep, indent=2) + "\n")  # JSON is valid YAML.
    print(f"\nwrote {path} ({len(runs)} runs on {gpus} GPU(s) each)")
