"""Train Llama 3.2 1B from scratch for 200 tokens per parameter on a pinned Lance mix.

Every setting is fixed here; there is no command line. Launch one process per GPU with
`torchrun` on 16 GPUs (see `train_llama32.sbatch`). The run continues from the newest
checkpoint in `OUTPUT_DIR` when there is one, and writes `DONE` there once it finishes.
"""

import json
import logging
import math
from dataclasses import dataclass, replace
from pathlib import Path

import torch

from flock.training import (
    AdamWConfig,
    DatasetConfig,
    Llama32Config,
    LRSchedulerConfig,
    LRScheduleSegment,
    MuonConfig,
    Trainer,
    TrainerConfig,
    get_dataset,
)
from flock.training.core.checkpoint import CheckpointManager
from flock.training.core.distributed import DistributedRuntime, distributed_run
from flock.training.core.models import LLAMA32_1B_CONFIG, Llama32

RUN_NAME = "canary-11w4"
OUTPUT_DIR = Path("/work/u4982363/canary/11w4")
DONE_MARKER = "DONE"
# Read-only local Lance mix: ~455M documents, ~352B Llama 3 tokens, raw text in `text`.
DATASET = DatasetConfig(
    uri="/work/harrychang08/pilot-mixes/mix-llama400b-tc10-nohplt-x250-seed0-253", version=1
)

# Each document is truncated to 4096 tokens; 16 of them fill one packed row per GPU.
SEQUENCE_LENGTH = 4096
TOKENS_PER_DEVICE = 16 * SEQUENCE_LENGTH
WORLD_SIZE = 16
TOKENS_PER_STEP = TOKENS_PER_DEVICE * WORLD_SIZE
TOKENS_PER_PARAMETER = 200
# Warm up over one token per parameter, hold, then cool down over the last 10%.
WARMUP_TOKENS_PER_PARAMETER = 1
COOLDOWN_FRACTION = 0.1
FINAL_LR_RATIO = 0.01
SAVES_PER_RUN = 20  # every 5% of the run

# Cheap held-out perplexity in English and Traditional Chinese, the mix's two pools, as the
# proxy recipe scores them. The datasets are cached in HF_HOME for offline compute nodes.
VALIDATION_TASKS = ["fineweb_en", "fineweb_zh_hant"]

MODEL_CONFIG = replace(LLAMA32_1B_CONFIG, attn_implementation="flash_attention_4")

OPTIMIZER_CONFIG = MuonConfig(
    lr=6e-3,
    momentum=0.95,
    nesterov=True,
    weight_decay=0.1,
    adjust_lr_fn="original",
    # The embedding (tied with the output projection) and the norms use AdamW; the
    # embedding is not decayed.
    companion_optimizer_config=AdamWConfig(
        lr=3e-3, betas=(0.9, 0.99), eps=1e-8, weight_decay=0.1, no_decay_embeddings=True
    ),
)


@dataclass(frozen=True, kw_only=True)
class RunPlan:
    """Step counts derived from the model's parameter count.

    Args:
        parameters (`int`): Trainable parameters, a tied weight counted once.
        max_steps (`int`): Updates for `TOKENS_PER_PARAMETER` tokens per parameter.
        warmup_steps (`int`): Linear warmup updates, one token per parameter.
        cooldown_start (`int`): Update where the 1-sqrt cooldown starts.
        save_steps (`int`): Checkpoint and evaluation interval.
    """

    parameters: int
    max_steps: int
    warmup_steps: int
    cooldown_start: int
    save_steps: int


def count_parameters(config: Llama32Config) -> int:
    """Count trainable parameters on the meta device, a tied weight once."""
    with torch.device("meta"):
        model = Llama32(config)
    return sum(parameter.numel() for parameter in model.parameters())


def plan_run(parameters: int, tokens_per_step: int = TOKENS_PER_STEP) -> RunPlan:
    """Derive the run's step counts from its parameter count and global batch."""
    max_steps = round(TOKENS_PER_PARAMETER * parameters / tokens_per_step)
    return RunPlan(
        parameters=parameters,
        max_steps=max_steps,
        warmup_steps=round(WARMUP_TOKENS_PER_PARAMETER * parameters / tokens_per_step),
        # The scheduler resolves fractional times with floor.
        cooldown_start=math.floor((1 - COOLDOWN_FRACTION) * max_steps),
        save_steps=max_steps // SAVES_PER_RUN,
    )


def lr_schedule(plan: RunPlan) -> LRSchedulerConfig:
    """Linear warmup, constant rate, then a 1-sqrt cooldown to 1% over the last 10%."""
    return LRSchedulerConfig(
        schedule=[
            LRScheduleSegment(start=0, end=plan.warmup_steps, start_ratio=0.0, end_ratio=1.0),
            LRScheduleSegment(
                start=plan.warmup_steps,
                end=1 - COOLDOWN_FRACTION,
                start_ratio=1.0,
                end_ratio=1.0,
            ),
            LRScheduleSegment(
                start=1 - COOLDOWN_FRACTION,
                end=1.0,
                start_ratio=1.0,
                end_ratio=FINAL_LR_RATIO,
                interpolation="1-sqrt",
            ),
        ]
    )


def trainer_config(plan: RunPlan, *, output_dir: Path = OUTPUT_DIR, resume: bool) -> TrainerConfig:
    """Build the run's trainer configuration.

    Args:
        plan (`RunPlan`): Step counts.
        output_dir (`Path`, *optional*, defaults to `OUTPUT_DIR`): Checkpoint root.
        resume (`bool`): Whether training continues from a checkpoint. A fresh start may
            find the Slurm logs in the directory, so it allows a nonempty one.
    """
    return TrainerConfig(
        run_name=RUN_NAME,
        input_column="text",
        batch_size=1,  # one packed row per GPU
        max_steps=plan.max_steps,
        max_tokens_per_device=TOKENS_PER_DEVICE,
        optimizer_config=OPTIMIZER_CONFIG,
        lr_scheduler_config=lr_schedule(plan),
        dataloader_num_workers=8,
        dataloader_prefetch_factor=4,
        validation_tasks=VALIDATION_TASKS,
        validation_steps=plan.save_steps,
        validation_limit=500,
        validation_batch_tokens=16384,
        wandb_project="canary",
        wandb_entity="cerulean-labs",
        wandb_run_name=RUN_NAME,
        wandb_mode="online",
        output_dir=str(output_dir),
        save_steps=plan.save_steps,
        save_total_limit=None,
        # Project-scoped model `canary-11w4` in project `canary` of the default org
        # (cerulean-labs); each checkpoint uploads in the background.
        save_to_hub_repo="canary:canary-11w4",
        overwrite_output_dir=not resume,
        model_dtype=torch.bfloat16,  # autocast; master weights stay FP32
        torch_compile=True,
        max_norm=1.0,
        logging_steps=10,
        seed=42,
    )


def build_trainer(runtime: DistributedRuntime) -> tuple[Trainer, bool]:
    """Build the trainer, and whether it resumes from a checkpoint in `OUTPUT_DIR`."""
    if runtime.world_size != WORLD_SIZE:
        raise RuntimeError(f"This run needs {WORLD_SIZE} ranks, got {runtime.world_size}")
    plan = plan_run(count_parameters(MODEL_CONFIG))
    resume = bool(CheckpointManager.list_checkpoints(OUTPUT_DIR))
    model = Llama32(MODEL_CONFIG)
    tokenizer = model.get_hf_tokenizer()
    assert tokenizer is not None
    tokenizer.model_max_length = SEQUENCE_LENGTH
    trainer = Trainer(
        config=trainer_config(plan, resume=resume),
        model=model,
        tokenizer=tokenizer,
        dataset=get_dataset(DATASET),
        runtime=runtime,
    )
    return trainer, resume


def main() -> None:
    """Train to completion, continuing from the newest checkpoint, then mark the run done."""
    done = OUTPUT_DIR / DONE_MARKER
    if done.exists():
        logging.getLogger(__name__).info("%s exists; the run is complete", done)
        return
    with distributed_run() as runtime:
        trainer, resume = build_trainer(runtime)
        try:
            trainer.train(resume_from_checkpoint=resume)
            if runtime.is_master and int(trainer.state.step) == trainer.total_steps:
                done.write_text(json.dumps({"step": trainer.total_steps}) + "\n")
        finally:
            if trainer.run is not None:
                trainer.run.finish()


if __name__ == "__main__":
    main()
