from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pytest

from flock.training.core.lr_scheduler import lr_multiplier, normalize_training_schedule
from flock.training.core.optimizers import MuonConfig
from flock.training.experiments.canary import train_llama32 as recipe

if TYPE_CHECKING:
    from pathlib import Path


def test_run_plan_follows_the_parameter_count() -> None:
    # Llama 3.2 1B with tied embeddings, the tied weight counted once.
    parameters = recipe.count_parameters(recipe.MODEL_CONFIG)
    assert parameters == 1_235_814_400
    assert recipe.TOKENS_PER_STEP == 16 * 16 * 4096 == 1_048_576
    plan = recipe.plan_run(parameters)
    assert plan == recipe.RunPlan(
        parameters=parameters,
        max_steps=235_713,
        warmup_steps=1_179,
        cooldown_start=212_141,
        save_steps=11_785,
    )
    assert plan.max_steps == round(200 * parameters / 1_048_576)


def test_schedule_warms_up_holds_and_cools_down_with_1_sqrt(tmp_path: Path) -> None:
    plan = recipe.plan_run(1_235_814_400)
    config = recipe.trainer_config(plan, output_dir=tmp_path, resume=False)
    schedule = config.lr_scheduler_config
    resolved = normalize_training_schedule(schedule, plan.max_steps)
    assert [(start, end, segment.interpolation) for start, end, segment in resolved] == [
        (0, 1_179, "linear"),
        (1_179, 212_141, "linear"),
        (212_141, 235_713, "1-sqrt"),
    ]
    assert lr_multiplier(0, schedule, plan.max_steps) == 0.0
    assert lr_multiplier(1_179, schedule, plan.max_steps) == 1.0
    assert lr_multiplier(212_141, schedule, plan.max_steps) == 1.0
    halfway = 212_141 + (235_713 - 212_141) // 2
    expected = 0.01 + 0.99 * (1 - math.sqrt((halfway - 212_141) / (235_713 - 212_141)))
    assert lr_multiplier(halfway, schedule, plan.max_steps) == pytest.approx(expected)
    assert lr_multiplier(235_713, schedule, plan.max_steps) == pytest.approx(0.01)


def test_trainer_config_matches_the_run(tmp_path: Path) -> None:
    plan = recipe.plan_run(1_235_814_400)
    config = recipe.trainer_config(plan, output_dir=tmp_path, resume=True)
    assert config.max_steps == plan.max_steps
    assert config.max_tokens_per_device == 65_536 and config.batch_size == 1
    assert config.save_steps == config.validation_steps == plan.save_steps
    assert config.save_total_limit is None
    assert config.save_to_hub_repo == "canary:canary-11w4"
    assert (config.wandb_project, config.wandb_run_name) == ("canary", "canary-11w4")
    assert config.torch_compile and config.max_norm == 1.0 and config.seed == 42
    assert not config.overwrite_output_dir
    optimizer = config.optimizer_config
    assert isinstance(optimizer, MuonConfig)
    assert (optimizer.lr, optimizer.momentum, optimizer.weight_decay) == (6e-3, 0.95, 0.1)
    assert optimizer.nesterov and optimizer.adjust_lr_fn == "original"
    companion = optimizer.companion_optimizer_config
    assert (companion.lr, companion.betas, companion.eps) == (3e-3, (0.9, 0.99), 1e-8)
    assert companion.weight_decay == 0.1 and companion.no_decay_embeddings
    assert recipe.MODEL_CONFIG.attn_implementation == "flash_attention_4"
    assert recipe.MODEL_CONFIG.tie_word_embeddings
