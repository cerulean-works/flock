"""Train one proxy preset on one node from a provisional hub Lance mix."""

import argparse
import json
import os
import time
from contextlib import ExitStack
from pathlib import Path

import torch
from accelerate.utils import broadcast_object_list
from torch import cuda
from torch.utils.data import DataLoader
from transformers import AutoTokenizer, PreTrainedTokenizerFast

from flock.training import DatasetConfig
from flock.training.experiments.proxy.olmo2_proxy import PROXY_SIZES

from .config import MODEL_REPO, MODEL_REVISION, proxy_config
from .provisional import ProvisionalMix
from .runtime import GpuExperiment, staged


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
    parser.add_argument("--workers", type=int, default=2, help="Loader processes per rank.")
    parser.add_argument(
        "--resume", action="store_true", help="Continue from the latest checkpoint."
    )
    args = parser.parse_args()
    # Every update number is a multiple of a negative interval: it would save at every update.
    if args.checkpoint_every is not None and args.checkpoint_every < 1:
        parser.error("--checkpoint-every must be positive")
    # Checked here, not by the loader, which would refuse only after the run record exists.
    if args.workers < 0:
        parser.error("--workers must not be negative")
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
        rank=int(os.environ.get("RANK", "0")),
        world_size=int(os.environ.get("WORLD_SIZE", "1")),
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
    accelerator = experiment.accelerator
    checkpoints = args.output_dir / "checkpoints"
    if args.resume:
        selected: list[str | None] = [None, None]
        if accelerator.is_main_process:
            try:
                selected[0] = str(latest_checkpoint(checkpoints))
            except Exception as error:
                if accelerator.num_processes == 1:
                    raise
                # Only plain strings cross ranks; exception objects may not be picklable.
                selected[1] = f"{type(error).__name__}: {error}"
        broadcast_object_list(selected)
        if selected[1] is not None:
            raise RuntimeError(selected[1])
        assert selected[0] is not None
        experiment.resume(Path(selected[0]))
        # No attempt marker may appear while another rank checks for interrupted saves.
        accelerator.wait_for_everyone()
    stop = experiment.total_targets // mix.targets_per_update
    if args.max_updates is not None:
        stop = min(stop, args.max_updates)
    if stop < experiment.updates:
        raise ValueError(
            f"stop at update {stop} precedes the committed update {experiment.updates}"
        )
    attempt = 0
    with experiment.synchronized_phase():
        if accelerator.is_main_process:
            if not args.resume:
                args.output_dir.mkdir(parents=True, exist_ok=False)
            attempt = record_attempt(args, experiment, started)
    log_path = args.output_dir / "log.jsonl"

    # A resumed segment logs the update it started from, so a replayed update is told apart.
    segment = experiment.updates
    segment_start_targets = experiment.targets
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
        # The loader seeds its workers from this generator, not from the global RNG a resume
        # has just restored, so a resumed run's checkpoints match an uninterrupted run's.
        generator=torch.Generator(),
    )
    with ExitStack() as stack:
        log = None
        with experiment.synchronized_phase():
            if accelerator.is_main_process:
                if log_path.stat().st_size:
                    # A kill mid-write can leave a partial line past the committed update.
                    os.truncate(log_path, log_path.read_bytes().rfind(b"\n") + 1)
                log = stack.enter_context(log_path.open("a"))
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
            save = bool(args.checkpoint_every and experiment.updates % args.checkpoint_every == 0)
            with experiment.synchronized_phase():
                if log is not None:
                    log.write(json.dumps(line) + "\n")
                    log.flush()
                    if save:
                        # Committed log lines must outlive a node crash with the checkpoint.
                        os.fsync(log.fileno())
            if save:
                experiment.checkpoint(checkpoints / f"update-{experiment.updates:06d}")
        with experiment.synchronized_phase():
            if log is not None:
                os.fsync(log.fileno())
            stack.close()

    # A resume at the stop point finds all three written and leaves them as they are.
    final = checkpoints / f"update-{experiment.updates:06d}"
    if not final.exists():
        experiment.checkpoint(final)
    export = args.output_dir / f"export-{experiment.updates:06d}"
    with experiment.synchronized_phase():
        if accelerator.is_main_process and not export.exists():
            experiment.export(export, tokenizer)
    write_summary(args.output_dir, experiment, attempt, segment, segment_start_targets, started)


def latest_checkpoint(checkpoints: Path) -> Path:
    """The committed checkpoint with the highest update, once no save is left half written."""
    # A save or export killed partway leaves its lock; the run would die on reaching it.
    incomplete = sorted(
        [*checkpoints.glob("*.incomplete"), *checkpoints.parent.glob("*.incomplete")]
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
    return latest


def record_attempt(args: argparse.Namespace, experiment: GpuExperiment, started: float) -> int:
    """Write this attempt's run record, with the log and checkpoint directory; return its number."""
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
    # Renamed into place, so every record counted as an attempt is a complete one.
    with staged(args.output_dir / f"run-{attempt:03d}.json") as temporary:
        temporary.write_text(json.dumps(run, indent=2, sort_keys=True))
        # The log and checkpoint directory exist first, so syncing the run directory on publishing
        # also keeps their entries: a checkpoint never outlives the record that explains its log.
        (args.output_dir / "log.jsonl").touch()
        (args.output_dir / "checkpoints").mkdir(exist_ok=True)
    return attempt


def write_summary(
    output_dir: Path,
    experiment: GpuExperiment,
    attempt: int,
    segment: int,
    segment_start_targets: int,
    started: float,
) -> None:
    """Write the summary of the run's final update, unless an earlier attempt already did."""
    accelerator = experiment.accelerator
    memory = torch.zeros((1, 2), dtype=torch.int64, device=accelerator.device)
    if accelerator.device.type == "cuda":
        memory[0, 0] = cuda.max_memory_allocated(accelerator.device)
        memory[0, 1] = cuda.max_memory_reserved(accelerator.device)
    memory = accelerator.gather(memory).tolist()
    if not accelerator.is_main_process:
        return
    # Renamed into place, so an existing summary is a complete one.
    summary_path = output_dir / f"summary-{experiment.updates:06d}.json"
    if summary_path.exists():
        return
    summary = {
        "provisional": True,
        "contract": experiment.contract,
        "updates": experiment.updates,
        "targets": experiment.targets,
        "attempt": attempt,
        "segment": segment,
        "segment_targets": experiment.targets - segment_start_targets,
        "world_size": accelerator.num_processes,
        "rank_peak_memory_bytes": memory,
        "started": started,
        "finished": time.time(),
        "device": str(experiment.accelerator.device),
    }
    if experiment.accelerator.device.type == "cuda":
        device = experiment.accelerator.device
        summary["device_name"] = cuda.get_device_name(device)
        summary["peak_allocated_bytes"] = cuda.max_memory_allocated(device)
        summary["peak_reserved_bytes"] = cuda.max_memory_reserved(device)
    with staged(summary_path) as temporary:
        temporary.write_text(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
