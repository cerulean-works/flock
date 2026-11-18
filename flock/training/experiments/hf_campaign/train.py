"""Train one proxy preset on one GPU from a provisional hub Lance mix."""

import argparse
import json
import time
from pathlib import Path

from torch import cuda
from torch.utils.data import DataLoader
from transformers import AutoTokenizer, PreTrainedTokenizerFast

from flock.training import DatasetConfig
from flock.training.experiments.proxy.olmo2_proxy import PROXY_SIZES

from .config import MODEL_REPO, MODEL_REVISION, proxy_config
from .provisional import ProvisionalMix
from .runtime import GpuExperiment


def main() -> None:
    """Train to the endpoint or `--max-updates`, then checkpoint and export."""
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
    # The fingerprint in the mix contract needs the fast backend, which the pinned repo ships.
    assert isinstance(tokenizer, PreTrainedTokenizerFast)
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
        total_targets=(
            len(mix) * mix.targets_per_update if args.total_targets is None else args.total_targets
        ),
        warmup_targets=args.warmup_targets,
        micro_rows=args.micro_rows,
    )
    checkpoints = args.output_dir / "checkpoints"
    if args.resume:
        # A save or export killed partway leaves its lock; the run would die on reaching it.
        incomplete = sorted(
            [*checkpoints.glob("*.incomplete"), *args.output_dir.glob("*.incomplete")]
        )
        if incomplete:
            raise FileExistsError(f"inspect and remove the interrupted save first: {incomplete}")
        # By number: the six-digit name is a minimum width, so names stop sorting past 999,999.
        latest = max(
            checkpoints.glob("update-*"),
            key=lambda path: int(path.name.removeprefix("update-")),
            default=None,
        )
        if latest is None:
            raise FileNotFoundError(f"no committed checkpoint in {checkpoints}")
        experiment.resume(latest)
    else:
        args.output_dir.mkdir(parents=True, exist_ok=False)
    # Every attempt gets its own record; two resumed from one checkpoint share a segment.
    attempt = len(list(args.output_dir.glob("run-*.json")))
    run = {
        "provisional": True,
        "attempt": attempt,
        "args": {key: str(value) for key, value in vars(args).items()},
        "tokenizer_revision": MODEL_REVISION,
        "contract": experiment.contract,
        "started": started,
        "resumed_at_update": experiment.updates,
        "device": str(experiment.accelerator.device),
    } | experiment.description
    with (args.output_dir / f"run-{attempt:03d}.json").open("x") as record:
        record.write(json.dumps(run, indent=2, sort_keys=True))

    # A resumed segment logs the update it started from, so a replayed update is told apart.
    segment = experiment.updates
    segment_start_targets = experiment.targets
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
        pin_memory=True,
        prefetch_factor=4 if args.workers else None,
    )
    with (args.output_dir / "log.jsonl").open("a") as log:
        for windows in loader:
            loss = experiment.update(windows)
            line = {
                "attempt": attempt,
                "segment": segment,
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

    # A resume at the stop point finds all three written and leaves them as they are.
    final = checkpoints / f"update-{experiment.updates:06d}"
    if not final.exists():
        experiment.checkpoint(final)
    export = args.output_dir / f"export-{experiment.updates:06d}"
    if not export.exists():
        experiment.export(export, tokenizer)
    summary = {
        "provisional": True,
        "contract": experiment.contract,
        "updates": experiment.updates,
        "targets": experiment.targets,
        "attempt": attempt,
        "segment": segment,
        "segment_targets": experiment.targets - segment_start_targets,
        "started": started,
        "finished": time.time(),
        "device": str(experiment.accelerator.device),
    }
    if experiment.accelerator.device.type == "cuda":
        summary["device_name"] = cuda.get_device_name()
        summary["peak_allocated_bytes"] = cuda.max_memory_allocated()
        summary["peak_reserved_bytes"] = cuda.max_memory_reserved()
    summary_path = args.output_dir / f"summary-{experiment.updates:06d}.json"
    if not summary_path.exists():
        summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
