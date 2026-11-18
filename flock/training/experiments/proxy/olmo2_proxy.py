"""Train a small OLMo 2 shaped proxy model on one pinned Lance mix."""

import argparse
import os
from dataclasses import dataclass, replace
from typing import Literal

from flock.training import (
    AdamWConfig,
    DatasetConfig,
    LRSchedulerConfig,
    LRScheduleSegment,
    Olmo2Config,
    Trainer,
    TrainerConfig,
    get_dataset,
)
from flock.training.core.models import Olmo2


@dataclass(frozen=True, kw_only=True)
class ProxySize:
    """One proxy size: its OLMo 2 architecture and the peak learning rate it trains at.

    Args:
        model (`Olmo2Config`): Architecture, context length and tokenizer repository.
        peak_lr (`float`): Learning rate at the end of warmup.
    """

    model: Olmo2Config
    peak_lr: float


# Both sizes keep OLMo 2 1B's vocabulary, tokenizer and 4096-token context (arXiv 2501.00656,
# Table 3). Counted without the input embedding, as the OLMo ladder counts N (arXiv 2412.04403,
# Table 1), the sizes are 76,308,864 and 190,354,176 parameters.
PROXY_SIZES = {
    # DataDecide's 60M width, depth and heads (arXiv 2504.11393, Table 2), with the 190M's MLP
    # rule. Its peak LR extends the ladder's log-log line through its 190M and 370M points down
    # to this size, where no published value exists.
    "76m": ProxySize(
        model=Olmo2Config(
            vocab_size=100352,
            hidden_size=384,
            intermediate_size=1536,
            num_hidden_layers=16,
            num_attention_heads=12,
            num_key_value_heads=12,
            hf_repo="allenai/OLMo-2-0425-1B",
        ),
        peak_lr=1.31e-3,
    ),
    # The OLMo ladder's smallest model and its peak LR (arXiv 2412.04403, Table 1).
    "190m": ProxySize(
        model=Olmo2Config(
            vocab_size=100352,
            hidden_size=768,
            intermediate_size=3072,
            num_hidden_layers=12,
            num_attention_heads=12,
            num_key_value_heads=12,
            hf_repo="allenai/OLMo-2-0425-1B",
        ),
        peak_lr=9.7e-4,
    ),
}


def build_trainer(
    proxy: ProxySize,
    dataset_config: DatasetConfig,
    *,
    run_name: str,
    batch_size: int,
    warmup_steps: int,
    output_dir: str,
    max_steps: int | None = None,
    validation_steps: int | None = None,
    seed: int = 0,
    num_workers: int = 8,
    wandb_mode: Literal["online", "offline", "disabled", "shared"] = "online",
) -> Trainer:
    """Build a trainer for one proxy run on a pinned dataset, from fresh weights.

    Args:
        proxy (`ProxySize`): Architecture and peak learning rate, usually from `PROXY_SIZES`.
        dataset_config (`DatasetConfig`): Lance dataset with a `text` column, pinned to a version.
        run_name (`str`): Checkpoint and evaluation label.
        batch_size (`int`): Dataset rows per optimizer update.
        warmup_steps (`int`): Positive number of linear warmup updates before the cosine decay.
        output_dir (`str`): Checkpoint root.
        max_steps (`int`, *optional*): Cap on updates. Without it the run is one pass.
        validation_steps (`int`, *optional*): Evaluation interval. Without it nothing is scored.
        seed (`int`, *optional*, defaults to `0`): Initialization and data-order seed.
        num_workers (`int`, *optional*, defaults to `8`): Positive number of loader processes.
        wandb_mode (`str`, *optional*, defaults to `"online"`): Tracking mode.

    Returns:
        `Trainer`: Ready to `train()`, with the W&B run already started.
    """
    # The seed sets both the initialization and the data order, as DataDecide's seeds do
    # (arXiv 2504.11393, sec. 2.1).
    model = Olmo2(replace(proxy.model, init_seed=seed))
    tokenizer = model.get_hf_tokenizer()
    assert tokenizer is not None
    config = TrainerConfig(
        run_name=run_name,
        input_column="text",
        batch_size=batch_size,
        max_steps=max_steps,
        # OLMo 2 states no betas; these are the scaffold's. Weight decay stays PyTorch's 0.01 on
        # every parameter, since AdamWConfig has no field for OLMo 2's 0.1 without embeddings.
        optimizer_config=AdamWConfig(lr=proxy.peak_lr, betas=(0.95, 0.99)),
        # OLMo 2 decays "to reach 10% of the peak learning rate after a specified max tokens"
        # (p.6), and the ladder fits that horizon to each run's data (arXiv 2412.04403, sec. 2.1).
        lr_scheduler_config=LRSchedulerConfig(
            schedule=[
                LRScheduleSegment(start=0, end=warmup_steps, start_ratio=0.0, end_ratio=1.0),
                LRScheduleSegment(
                    start=warmup_steps,
                    end=1.0,
                    start_ratio=1.0,
                    end_ratio=0.1,
                    interpolation="cosine",
                ),
            ]
        ),
        dataloader_num_workers=num_workers,
        dataloader_prefetch_factor=2,
        validation_tasks=["fineweb_en", "fineweb_zh_hant"],
        validation_steps=validation_steps,
        validation_limit=500,
        # The batch the BF16 bank was scored with; scoring pins one SDPA kernel by itself.
        validation_batch_tokens=16384,
        wandb_project="proxy-recipe",
        wandb_mode=wandb_mode,
        output_dir=output_dir,
        save_steps=1000,
        seed=seed,
    )
    return Trainer(
        config=config, model=model, tokenizer=tokenizer, dataset=get_dataset(dataset_config)
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train one OLMo 2 proxy on a pinned Lance mix.")
    parser.add_argument("--size", choices=PROXY_SIZES, required=True)
    parser.add_argument("--dataset-uri", required=True, help="Lance URI with a text column.")
    parser.add_argument("--dataset-version", type=int, required=True)
    parser.add_argument("--batch-size", type=int, required=True, help="Rows per update.")
    parser.add_argument("--warmup-steps", type=int, required=True)
    parser.add_argument("--max-steps", type=int, help="Cap on updates; one pass without it.")
    parser.add_argument("--validation-steps", type=int, help="Evaluation interval in updates.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--resume", action="store_true", help="Continue from the latest checkpoint in --output-dir."
    )
    args = parser.parse_args()

    run_name = f"proxy-{args.size}-s{args.seed}"
    # The trainer's wandb.init takes no run name, and the default would be random.
    os.environ.setdefault("WANDB_NAME", run_name)
    trainer = build_trainer(
        PROXY_SIZES[args.size],
        DatasetConfig(uri=args.dataset_uri, version=args.dataset_version),
        run_name=run_name,
        batch_size=args.batch_size,
        warmup_steps=args.warmup_steps,
        output_dir=args.output_dir,
        max_steps=args.max_steps,
        validation_steps=args.validation_steps,
        seed=args.seed,
    )
    try:
        trainer.train(resume_from_checkpoint=args.resume)
    finally:
        if trainer.run is not None:
            trainer.run.finish()
