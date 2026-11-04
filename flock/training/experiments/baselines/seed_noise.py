import argparse
import json
import logging
import math
import os
import shlex
import socket
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import cast

import torch
from lance.torch.data import SafeLanceDataset
from torch.utils.data import Dataset

from flock.eval.provenance import flock_commit
from flock.eval.run import EvalConfig, evaluate
from flock.hub import models as hub_models
from flock.training import (
    AdamWConfig,
    CosineConfig,
    DatasetConfig,
    Trainer,
    TrainerConfig,
    get_dataset,
)
from flock.training.core.models import OLMO2_190M_CONFIG, Olmo2


class PackedDocs(Dataset):
    """Training rows of whole documents joined by the EOS token, the same rows whatever the seed.

    Row `i` holds the `docs_per_row` consecutive documents from position `i * stride`, so the rows spread over the whole corpus, cut to their first `max_chars` characters. With `max_chars` above the model's length in characters, the collator truncates every row to full length and no position is padding, as pretraining's concatenate-then-split does (OLMo 3, arXiv 2512.13961, sec. 3.6.3). The tail past the cut is never trained on.
    """

    def __init__(
        self,
        docs: SafeLanceDataset,
        *,
        rows: int,
        docs_per_row: int,
        max_chars: int,
        eos: str,
        column: str = "text",
    ):
        if rows < 1 or docs_per_row < 1 or max_chars < 1:
            raise ValueError("rows, docs_per_row and max_chars must be positive")
        self.stride = len(docs) // rows
        if self.stride < docs_per_row:
            raise ValueError(
                f"{rows} rows of {docs_per_row} documents need more than {len(docs)} documents"
            )
        self.docs = docs
        self.rows = rows
        self.docs_per_row = docs_per_row
        self.max_chars = max_chars
        self.eos = eos
        self.column = column

    def __len__(self) -> int:
        return self.rows

    def __getitem__(self, index: int) -> dict[str, str]:
        return self.__getitems__([index])[0]

    def __getitems__(self, indices: list[int]) -> list[dict[str, str]]:
        # One read for the whole batch: each row's documents, in the order of `indices`.
        positions = []
        for index in indices:
            if not 0 <= index < self.rows:
                raise IndexError(f"row {index} of {self.rows}")
            start = index * self.stride
            positions.extend(range(start, start + self.docs_per_row))
        texts = [doc[self.column] for doc in self.docs.__getitems__(positions)]

        rows = []
        for first in range(0, len(texts), self.docs_per_row):
            joined = self.eos.join(texts[first : first + self.docs_per_row])
            rows.append({self.column: joined[: self.max_chars]})
        return rows


class TrainerMetrics(logging.Handler):
    """Keeps the metric dicts the trainer logs every `logging_steps`."""

    def __init__(self):
        super().__init__()
        self.windows: list[dict[str, float]] = []

    def emit(self, record: logging.LogRecord) -> None:
        if isinstance(record.msg, dict):
            self.windows.append(record.msg)


def spikes(window_max: list[float], history: int = 10, factor: float = 1.5) -> list[int]:
    """Indexes of the logged windows whose largest step loss is above `factor` times the largest of the `history` windows before it.

    With the trainer's 10-step windows this is the rule "a loss above 1.5x the running maximum of the previous 100 steps". The first `history` windows have no full history and are never flagged.
    """
    return [
        index
        for index in range(history, len(window_max))
        if window_max[index] > factor * max(window_max[index - history : index])
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="One seed of the seed-noise baseline: OLMo 2 190M on DCLM, saved to the hub "
        "and scored on the bpb suites."
    )
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--tokens", type=float, default=15e9, help="Target tokens to train on.")
    parser.add_argument("--batch-size", type=int, required=True)
    parser.add_argument("--hub-name", help="org/name of the model on the hub.")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--train-only", action="store_true", help="Stop after training: no upload, no eval."
    )
    args = parser.parse_args()
    if args.hub_name is None and not args.train_only:
        parser.error("--hub-name is required unless --train-only")
    if args.out.exists() and any(args.out.iterdir()):
        parser.error(f"{args.out} is not empty")
    args.out.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    metrics = TrainerMetrics()
    logging.getLogger("flock.training.core.trainer").addHandler(metrics)

    # DataDecide trains its 150M (this shape) on 2048-token sequences (arXiv 2504.11393, Table 2).
    # The seed sets both the initialization and the data order (DataDecide sec. 2.1).
    config = replace(OLMO2_190M_CONFIG, max_position_embeddings=2048, init_seed=args.seed)
    model = Olmo2(config)
    tokenizer = model.get_hf_tokenizer()
    assert tokenizer is not None and tokenizer.eos_token is not None

    # A row trains on its first 2048 tokens: 2047 inputs, each with its next token as target.
    targets_per_row = config.max_position_embeddings - 1
    steps = math.ceil(args.tokens / (args.batch_size * targets_per_row))
    dataset_config = DatasetConfig(uri="s3://flock-datasets/dclm", version=1)
    # Eight DCLM documents average about 10,400 tokens. Cut at 12,288 characters, 510 of 512
    # sampled rows still fill 2048 tokens, so padding is about 0.1% of positions.
    dataset = PackedDocs(
        get_dataset(dataset_config),
        rows=steps * args.batch_size,
        docs_per_row=8,
        max_chars=12_288,
        eos=tokenizer.eos_token,
    )

    # The trainer's wandb.init takes no run name, and the default would be random.
    os.environ.setdefault("WANDB_NAME", f"seed-noise-190m-s{args.seed}")
    # The OLMo 2 ladder's 190M: peak LR 9.7e-4, warmup of 363 steps of 524,288 tokens (190M
    # tokens), cosine decay to 10% of the peak over the run (arXiv 2412.04403, sec. 2.1, Table 1).
    warmup_steps = round(363 * 524_288 / (args.batch_size * targets_per_row))
    trainer_config = TrainerConfig(
        input_column="text",
        batch_size=args.batch_size,
        optimizer_config=AdamWConfig(lr=9.7e-4, betas=(0.95, 0.99)),
        lr_scheduler_config=CosineConfig(warmup_steps=warmup_steps, min_lr_ratio=0.1),
        dataloader_num_workers=8,
        dataloader_prefetch_factor=2,
        wandb_project="training-baselines",
        seed=args.seed,
    )
    trainer = Trainer(
        config=trainer_config,
        model=model,
        tokenizer=tokenizer,
        # The trainer only needs a map-style dataset, as its own tests rely on.
        dataset=cast(SafeLanceDataset, dataset),
    )

    # Counted on the device as the trainer counts its loss tokens, read once at the end.
    targets = torch.zeros((), dtype=torch.int64, device=trainer.device)
    positions = 0

    def count_targets(module: torch.nn.Module, inputs: tuple) -> None:
        nonlocal positions
        labels = inputs[0]["labels"]
        targets.add_((labels != -100).sum())
        positions += labels.numel()

    hook = trainer.model.register_forward_pre_hook(count_targets)
    start = time.perf_counter()
    trainer.train()
    seconds = time.perf_counter() - start
    hook.remove()

    window_mean = [window["train/loss_mean"] for window in metrics.windows]
    window_max = [window["train/loss_window_max"] for window in metrics.windows]
    trained = int(targets)
    summary = {
        "seed": args.seed,
        "model_config": {**asdict(config), "hf_repo": config.hf_repo},
        "parameters": sum(p.numel() for p in model.parameters()),
        "dataset": asdict(dataset_config),
        "rows": len(dataset),
        "docs_per_row": dataset.docs_per_row,
        "max_chars": dataset.max_chars,
        "trainer_config": {
            **asdict(trainer_config),
            "model_dtype": str(trainer_config.model_dtype),
        },
        "steps": trainer.total_steps,
        "warmup_steps": warmup_steps,
        "target_tokens": trained,
        "positions": positions,
        "seconds": seconds,
        "tokens_per_second": trained / seconds,
        "peak_memory_bytes": torch.cuda.max_memory_allocated() if torch.cuda.is_available() else 0,
        "final_loss": window_mean[-1] if window_mean else None,
        "finite": all(math.isfinite(value) for value in window_mean + window_max),
        "spikes": spikes(window_max),
        "windows": metrics.windows,
        "wandb_run": trainer.run.path,
        "host": socket.gethostname(),
        "device": torch.cuda.get_device_name() if torch.cuda.is_available() else "cpu",
        "flock_commit": flock_commit(),
        "torch": torch.__version__,
        "command": shlex.join(sys.argv),
    }
    (args.out / "train.json").write_text(json.dumps(summary, indent=1))
    if args.train_only:
        trainer.run.finish()
        return 0

    version = hub_models.save_to_hub(
        model,
        args.hub_name,
        tags=[f"seed-{args.seed}"],
        metadata={key: summary[key] for key in ("seed", "steps", "target_tokens", "final_loss")},
        run=trainer.run,
        step=trainer.total_steps,
    )
    trainer.run.finish()
    summary["hub_version"] = str(version)
    (args.out / "train.json").write_text(json.dumps(summary, indent=1))

    # Scored as the banked baselines were (FP32, 16384 batch tokens), from the saved weights.
    del trainer, model
    torch.cuda.empty_cache()
    evaluate(
        EvalConfig(
            model=f"flock:{version}",
            suites=("base_easy:qa_bpb", "zh_tw:bpb", "paloma"),
            out=args.out / "eval",
            batch_tokens=16384,
            dtype="float32",
            wandb_project="model-evals",
            command=shlex.join(sys.argv),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
