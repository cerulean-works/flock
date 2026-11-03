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
    """Wall time, non-padding input tokens and peak memory of each optimizer step of a trainer.

    It hooks the model's forward and the optimizer's step, so the trainer itself is unchanged. Steps after `warmup` form the measured window.
    """

    def __init__(self, trainer: Trainer, warmup: int):
        self.device = trainer.device
        self.warmup = warmup
        self.ends: list[float] = []
        self.tokens: list[torch.Tensor] = []
        self.shapes: list[tuple[int, int]] = []
        self.peak_bytes: list[int] = []
        trainer.model.register_forward_pre_hook(self._count_tokens)
        trainer.optimizer.register_step_post_hook(self._end_step)

    def _count_tokens(self, module: torch.nn.Module, args: tuple[Any, ...]) -> None:
        # The model's only argument is the collated batch; its mask marks real input tokens.
        mask = args[0]["attention_mask"]
        self.tokens.append(mask.sum())
        self.shapes.append((mask.shape[0], mask.shape[1]))

    def _end_step(self, optimizer: torch.optim.Optimizer, args: Any, kwargs: Any) -> None:
        if self.device.type == "cuda":
            # Kernels run asynchronously: wait for this step's work before reading the clock.
            torch.cuda.synchronize(self.device)
            self.peak_bytes.append(torch.cuda.max_memory_allocated(self.device))
        self.ends.append(time.perf_counter())

    @property
    def step_seconds(self) -> list[float]:
        """Durations of the measured steps, each from the previous step's end to its own."""
        return [
            end - start
            for start, end in zip(self.ends[self.warmup - 1 :], self.ends[self.warmup :])
        ]

    @property
    def window_tokens(self) -> int:
        return int(torch.stack(self.tokens[self.warmup :]).sum())

    @property
    def window_padded_tokens(self) -> int:
        return sum(rows * length for rows, length in self.shapes[self.warmup :])

    @property
    def tokens_per_second(self) -> float:
        return self.window_tokens / sum(self.step_seconds)

    @property
    def padded_tokens_per_second(self) -> float:
        return self.window_padded_tokens / sum(self.step_seconds)


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

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    model = Olmo2(OLMO2_1B_CONFIG)
    tokenizer = model.get_hf_tokenizer()
    assert tokenizer is not None
    rows = args.steps * args.batch_size

    # The trainer runs one pass over its dataset, so the dataset is cut to `steps` batches.
    if args.data == "lance":
        finepdfs = get_dataset(DatasetConfig(uri="s3://flock-datasets/finepdfs", version=1))
        # Rows are spread over the whole dataset, so reads touch as many fragments as a real run.
        dataset = Subset(finepdfs, range(0, len(finepdfs), len(finepdfs) // rows)[:rows])
    else:
        # Eight distinct batches, cycled: the scaffold's sequence length with the network out.
        packed = memory_rows(
            tokenizer, OLMO2_1B_CONFIG.max_position_embeddings, 8 * args.batch_size
        )
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
        "device": torch.cuda.get_device_name()
        if torch.cuda.is_available()
        else str(trainer.device),
        "flock_commit": os.environ.get("FLOCK_COMMIT"),
        "job_id": os.environ.get("SLURM_JOB_ID"),
        "command": " ".join(sys.argv),
    }
    path = args.out / f"throughput-{args.data}-bs{args.batch_size}.json"
    args.out.mkdir(parents=True, exist_ok=True)

    try:
        trainer.train()
    except torch.OutOfMemoryError as error:
        result |= {"oom": True, "error": str(error).splitlines()[0], "steps_done": len(clock.ends)}
        path.write_text(json.dumps(result, indent=2) + "\n")
        trainer.run.finish(exit_code=1)
        return 3

    seconds = clock.step_seconds
    losses = {window["global_step"]: window["train/loss_mean"] for window in metrics.windows}
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
        # PaLM (Chowdhery et al. 2022, App. B): 6N FLOPs per token over the hardware's peak.
        # 989e12 is the H200's dense BF16 peak from NVIDIA's datasheet.
        "mfu_total": 6 * n_total * clock.tokens_per_second / 989e12,
        "mfu_nonembed": 6 * n_nonembed * clock.tokens_per_second / 989e12,
        "peak_memory_bytes": max(clock.peak_bytes, default=None),
        "loss_at_warmup": losses.get(args.warmup),
        "loss_at_end": losses.get(args.steps),
        # A window longer than twice the shortest step marks the run unstable.
        "stable": max(seconds) <= 2 * min(seconds)
        and all(math.isfinite(loss) for loss in losses.values()),
        "per_step_seconds": [b - a for a, b in zip(clock.ends, clock.ends[1:])],
        "per_step_tokens": torch.stack(clock.tokens).tolist(),
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
