import argparse
import json
import logging
import math
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any, cast

import pyarrow.parquet as pq
import torch
from huggingface_hub import hf_hub_download
from lance.torch.data import SafeLanceDataset
from torch.utils.data import Subset
from transformers import TokenizersBackend

from flock.training import (
    AdamWConfig,
    DatasetConfig,
    Trainer,
    TrainerConfig,
    WSDConfig,
    get_dataset,
)
from flock.training.core.models import OLMO2_1B_CONFIG, Olmo2


class StepClock:
    """Duration, non-padding input tokens and peak memory of each optimizer step of a trainer.

    It hooks the model's forward and the optimizer's step, so the trainer itself is unchanged. On CUDA each step boundary is a recorded event, so the clock adds no synchronization the trainer does not already have. Steps after `warmup` form the measured window.
    """

    def __init__(self, trainer: Trainer, warmup: int):
        self.device = trainer.device
        self.warmup = warmup
        # The start of the first step, then the end of every step.
        self.events: list[torch.cuda.Event] = []
        self.wall: list[float] = []
        self.tokens: list[torch.Tensor] = []
        self.shapes: list[tuple[int, int]] = []
        self.peak_bytes: list[int] = []
        trainer.model.register_forward_pre_hook(self._start_step)
        trainer.optimizer.register_step_post_hook(self._end_step)

    def _start_step(self, module: torch.nn.Module, args: tuple[Any, ...]) -> None:
        # The model's only argument is the collated batch; its mask marks real input tokens.
        mask = args[0]["attention_mask"]
        self.tokens.append(mask.sum())
        self.shapes.append((mask.shape[0], mask.shape[1]))
        if len(self.tokens) == 1:
            self._mark()

    def _end_step(self, optimizer: torch.optim.Optimizer, args: Any, kwargs: Any) -> None:
        self._mark()
        if self.device.type == "cuda":
            self.peak_bytes.append(torch.cuda.max_memory_allocated(self.device))
            torch.cuda.reset_peak_memory_stats(self.device)

    def _mark(self) -> None:
        if self.device.type == "cuda":
            event = torch.cuda.Event(enable_timing=True)
            event.record()
            self.events.append(event)
        else:
            self.wall.append(time.perf_counter())

    @property
    def step_seconds(self) -> list[float]:
        """Durations of every step, from the previous step's end (the first from its forward)."""
        if self.events:
            self.events[-1].synchronize()
            return [
                start.elapsed_time(end) / 1000 for start, end in zip(self.events, self.events[1:])
            ]
        return [end - start for start, end in zip(self.wall, self.wall[1:])]

    @property
    def window_seconds(self) -> list[float]:
        return self.step_seconds[self.warmup :]

    @property
    def window_tokens(self) -> int:
        return int(torch.stack(self.tokens[self.warmup :]).sum())

    @property
    def window_padded_tokens(self) -> int:
        return sum(rows * length for rows, length in self.shapes[self.warmup :])

    @property
    def tokens_per_second(self) -> float:
        return self.window_tokens / sum(self.window_seconds)

    @property
    def padded_tokens_per_second(self) -> float:
        return self.window_padded_tokens / sum(self.window_seconds)


class TrainerMetrics(logging.Handler):
    """Keeps the metric dicts the trainer logs every `logging_steps`."""

    def __init__(self):
        super().__init__()
        self.windows: list[dict[str, float]] = []

    def emit(self, record: logging.LogRecord) -> None:
        if isinstance(record.msg, dict):
            self.windows.append(record.msg)


def memory_rows(tokenizer: TokenizersBackend, seq_len: int, count: int) -> list[dict[str, str]]:
    """`count` text rows from fineweb-edu-dev, each joined from whole documents until it fills `seq_len` tokens.

    Every row is then truncated to full length by the trainer's collator, so no batch has padding. Rows repeat once the 5,000 documents run out.
    """
    path = hf_hub_download(
        "cerulean-works/fineweb-edu-dev",
        "data/validation-00000-of-00001.parquet",
        repo_type="dataset",
        revision="2484afe000a6fdf442ba2396391a3bec12644903",
    )
    texts = pq.read_table(path, columns=["text"]).column("text").to_pylist()
    lengths = [len(ids) for ids in tokenizer(texts)["input_ids"]]

    # Joining with a blank line can merge tokens at a seam, so each row overshoots a little.
    rows: list[dict[str, str]] = []
    parts: list[str] = []
    filled = 0
    for text, length in zip(texts, lengths):
        parts.append(text)
        filled += length
        if filled >= seq_len + 64:
            rows.append({"text": "\n\n".join(parts)})
            parts, filled = [], 0
    return [rows[index % len(rows)] for index in range(count)]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Training throughput and MFU of a fresh OLMo 2 1B on one GPU."
    )
    parser.add_argument("--batch-size", type=int, required=True)
    parser.add_argument("--steps", type=int, default=300)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--data", choices=("lance", "memory"), required=True)
    parser.add_argument("--out", type=Path, required=True, help="Directory for the result JSON.")
    args = parser.parse_args()
    if not 0 < args.warmup < args.steps:
        parser.error("--warmup must be positive and below --steps")
    # MFU below divides by the H200's peak, so another device would get a wrong number.
    if "H200" not in (torch.cuda.get_device_name() if torch.cuda.is_available() else ""):
        parser.error("this baseline measures an NVIDIA H200")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = OLMO2_1B_CONFIG
    model = Olmo2(config)
    tokenizer = model.get_hf_tokenizer()
    assert tokenizer is not None
    rows = args.steps * args.batch_size

    # The trainer runs one pass over its dataset, so the dataset is cut to `steps` batches.
    if args.data == "lance":
        finepdfs = get_dataset(DatasetConfig(uri="s3://flock-datasets/finepdfs", version=1))
        if rows > len(finepdfs):
            parser.error(f"--steps x --batch-size exceeds the dataset's {len(finepdfs)} rows")
        # Rows are spread over the whole dataset, so reads touch as many fragments as a real run.
        dataset = Subset(finepdfs, range(0, len(finepdfs), len(finepdfs) // rows)[:rows])
    else:
        # Eight batches' worth of distinct full-length rows, repeated; the loader shuffles them.
        packed = memory_rows(tokenizer, config.max_position_embeddings, 8 * args.batch_size)
        dataset = [packed[index % len(packed)] for index in range(rows)]

    # The trainer's wandb.init takes no run name, and the default would be random.
    os.environ.setdefault("WANDB_NAME", f"throughput-{args.data}-bs{args.batch_size}")
    trainer = Trainer(
        config=TrainerConfig(
            input_column="text",
            batch_size=args.batch_size,
            optimizer_config=AdamWConfig(lr=1e-3, betas=(0.95, 0.99)),
            lr_scheduler_config=WSDConfig(warmup_steps=100, decay_steps=100),
            dataloader_num_workers=2,
            dataloader_prefetch_factor=1,
            wandb_project="training-baselines",
            wandb_mode="online",
        ),
        model=model,
        tokenizer=tokenizer,
        # The trainer only needs a map-style dataset, as its own tests rely on.
        dataset=cast(SafeLanceDataset, dataset),
    )
    clock = StepClock(trainer, args.warmup)
    metrics = TrainerMetrics()
    logging.getLogger("flock.training.core.trainer").addHandler(metrics)

    n_total = sum(p.numel() for p in model.parameters())
    # Kaplan et al. 2020 (sec. 2.1) leave the input embedding and the untied output layer out of N.
    n_nonembed = n_total - model.model.embed_tokens.weight.numel() - model.lm_head.weight.numel()
    result: dict[str, Any] = {
        "data": args.data,
        "batch_size": args.batch_size,
        "steps": args.steps,
        "warmup": args.warmup,
        "n_total": n_total,
        "n_nonembed": n_nonembed,
        "device": torch.cuda.get_device_name(),
        "flock_commit": os.environ.get("FLOCK_COMMIT"),
        "job_id": os.environ.get("SLURM_JOB_ID"),
        "command": " ".join(sys.argv),
    }
    path = args.out / f"throughput-{args.data}-bs{args.batch_size}.json"
    args.out.mkdir(parents=True, exist_ok=True)

    try:
        trainer.train()
    except torch.OutOfMemoryError as error:
        result |= {
            "oom": True,
            "error": str(error).splitlines()[0],
            "steps_done": len(clock.peak_bytes),
        }
        path.write_text(json.dumps(result, indent=2) + "\n")
        trainer.run.finish(exit_code=1)
        return 3

    seconds = clock.window_seconds
    per_step_tokens = torch.stack(clock.tokens).tolist()
    losses = {str(window["global_step"]): window["train/loss_mean"] for window in metrics.windows}
    # PaLM (Chowdhery et al. 2022, App. B): MFU is the token rate times the model FLOPs per
    # token over the hardware's peak, here the H200's dense BF16 989 TFLOP/s (NVIDIA datasheet).
    peak_flops = 989e12
    # PaLM's attention term, 12 L H Q T per token (H Q is the hidden size), at each step's T.
    attention_flops = sum(
        12 * config.num_hidden_layers * config.hidden_size * length * tokens
        for tokens, (_, length) in zip(per_step_tokens[args.warmup :], clock.shapes[args.warmup :])
    )
    # An embedding lookup is not a matmul, so PaLM's matmul count leaves the input table out.
    n_matmul = n_total - model.model.embed_tokens.weight.numel()
    result |= {
        "oom": False,
        "sequence_length_max": max(length for _, length in clock.shapes),
        "sequence_length_mean": statistics.fmean(length for _, length in clock.shapes),
        "step_seconds_mean": statistics.fmean(seconds),
        "step_seconds_median": statistics.median(seconds),
        "step_seconds_min": min(seconds),
        "step_seconds_max": max(seconds),
        "window_tokens": clock.window_tokens,
        "window_padded_tokens": clock.window_padded_tokens,
        "tokens_per_second": clock.tokens_per_second,
        "padded_tokens_per_second": clock.padded_tokens_per_second,
        "mfu_total": 6 * n_total * clock.tokens_per_second / peak_flops,
        "mfu_nonembed": 6 * n_nonembed * clock.tokens_per_second / peak_flops,
        "mfu_matmul_with_attention": (6 * n_matmul * clock.window_tokens + attention_flops)
        / sum(seconds)
        / peak_flops,
        "peak_memory_bytes": max(clock.peak_bytes),
        # Each logged loss is the token-weighted mean over the 10 steps ending at its key.
        "loss_windows": losses,
        # A step more than twice as long as the shortest one marks the run unstable.
        "stable": max(seconds) <= 2 * min(seconds)
        and all(math.isfinite(loss) for loss in losses.values()),
        "per_step_seconds": clock.step_seconds,
        "per_step_tokens": per_step_tokens,
        "per_step_peak_bytes": clock.peak_bytes,
    }
    path.write_text(json.dumps(result, indent=2) + "\n")
    trainer.run.summary.update(
        {key: value for key, value in result.items() if not key.startswith("per_step")}
    )
    trainer.run.finish()
    return 0


if __name__ == "__main__":
    sys.exit(main())
