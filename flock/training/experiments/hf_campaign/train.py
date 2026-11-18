"""Train one proxy preset on one GPU from a provisional hub Lance mix."""

import argparse
import json
import time
from pathlib import Path

import torch
from torch.utils.data import DataLoader
from transformers import AutoTokenizer

from flock.training import DatasetConfig
from flock.training.experiments.proxy.olmo2_proxy import PROXY_SIZES

from .config import MODEL_REPO, MODEL_REVISION, proxy_config
from .provisional import ProvisionalMix
from .runtime import GpuExperiment


def main() -> None:
    """Parse the run's settings, train to the endpoint or `--max-updates`, then checkpoint and export."""
    started = time.time()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", choices=PROXY_SIZES, required=True)
    parser.add_argument("--dataset-uri", required=True, help="Lance mix with text and tokens.")
    parser.add_argument("--dataset-version", type=int, required=True)
    parser.add_argument("--seed", type=int, default=0, help="Initialization seed.")
    parser.add_argument(
        "--total-targets", type=int, help="Schedule endpoint; defaults to one pass over the mix."
    )
    parser.add_argument("--warmup-targets", type=int, required=True)
    parser.add_argument("--rows-per-update", type=int, required=True)
    parser.add_argument("--micro-rows", type=int, required=True, help="Rows per forward pass.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--checkpoint-every", type=int, help="Checkpoint interval in updates.")
    parser.add_argument("--max-updates", type=int, help="Stop at this update; the schedule stays.")
    parser.add_argument("--workers", type=int, default=4, help="Loader processes.")
    parser.add_argument(
        "--resume", action="store_true", help="Continue from the latest checkpoint."
    )
    args = parser.parse_args()

    tokenizer = AutoTokenizer.from_pretrained(MODEL_REPO, revision=MODEL_REVISION)
    assert tokenizer is not None  # The stubs allow None; loading raises instead.
    config = proxy_config(args.size)
    mix = ProvisionalMix(
        DatasetConfig(uri=args.dataset_uri, version=args.dataset_version),
        tokenizer,
        # 4096, OLMo 2's context (arXiv 2501.00656, Table 3), as the presets and 141 use.
        window=config.max_position_embeddings,
        rows_per_update=args.rows_per_update,
    )
    experiment = GpuExperiment(
        config,
        mix,
        peak_lr=PROXY_SIZES[args.size].peak_lr,
        seed=args.seed,
        total_targets=args.total_targets or len(mix) * mix.targets_per_update,
        warmup_targets=args.warmup_targets,
        micro_rows=args.micro_rows,
    )
    checkpoints = args.output_dir / "checkpoints"
    if args.resume:
        committed = [path for path in checkpoints.iterdir() if path.suffix != ".incomplete"]
        experiment.resume(max(committed))
    else:
        args.output_dir.mkdir(parents=True, exist_ok=False)
    run = {
        "provisional": True,
        "args": {key: str(value) for key, value in vars(args).items()},
        "tokenizer_revision": MODEL_REVISION,
        "contract": experiment.contract,
        "started": started,
        "resumed_at_update": experiment.updates,
    } | experiment.description
    (args.output_dir / f"run-{experiment.updates:06d}.json").write_text(
        json.dumps(run, indent=2, sort_keys=True)
    )

    stop = experiment.total_targets // mix.targets_per_update
    if args.max_updates is not None:
        stop = min(stop, args.max_updates)
    # Item u of the mix is update u, so the sampler starts at the committed update and spawned
    # workers can prefetch without any producer state to roll back.
    loader = DataLoader(
        mix,
        batch_size=None,
        sampler=range(experiment.updates, stop),
        num_workers=args.workers,
        multiprocessing_context="spawn" if args.workers else None,
        pin_memory=torch.cuda.is_available(),
        prefetch_factor=4 if args.workers else None,
    )
    with (args.output_dir / "log.jsonl").open("a") as log:
        for windows in loader:
            loss = experiment.update(windows)
            line = {
                "update": experiment.updates,
                "targets": experiment.targets,
                "loss": loss,
                "lr": experiment.optimizer.param_groups[0]["lr"],
                "time": time.time(),
            }
            log.write(json.dumps(line) + "\n")
            log.flush()
            if args.checkpoint_every and experiment.updates % args.checkpoint_every == 0:
                experiment.checkpoint(checkpoints / f"update-{experiment.updates:06d}")

    final = checkpoints / f"update-{experiment.updates:06d}"
    if not final.exists():
        experiment.checkpoint(final)
    experiment.export(args.output_dir / f"export-{experiment.updates:06d}", tokenizer)
    summary = {
        "provisional": True,
        "contract": experiment.contract,
        "updates": experiment.updates,
        "targets": experiment.targets,
        "started": started,
        "finished": time.time(),
    }
    if torch.cuda.is_available():
        summary["device"] = torch.cuda.get_device_name()
        summary["peak_allocated_bytes"] = torch.cuda.max_memory_allocated()
        summary["peak_reserved_bytes"] = torch.cuda.max_memory_reserved()
    (args.output_dir / f"summary-{experiment.updates:06d}.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True)
    )


if __name__ == "__main__":
    main()
