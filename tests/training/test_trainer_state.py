from typing import Any

import pytest
import torch

from flock.training.core.trainer import TrainerState


def update(state: TrainerState, tokens: list[int], **timing: Any) -> None:
    one = torch.tensor(1.0)
    state.update(1, one.expand(len(tokens)), one, torch.tensor(tokens), **timing)


def perf(metrics: dict[str, int | float]) -> dict[str, int | float]:
    return {key: value for key, value in metrics.items() if key.startswith("perf/")}


def test_window_reports_throughput_per_device() -> None:
    state = TrainerState(world_size=2, peak_flops=1e3)
    # Two 4-second updates, each waiting 1 second for its batch: 2 × 4 then 2 × 8 padded tokens.
    update(state, [6, 4], seconds=4, data_wait=1, padded_tokens=8, flops=40 * 8)
    update(state, [6, 4], seconds=4, data_wait=1, padded_tokens=16, flops=80 * 16)

    flops_per_second = (40 * 8 + 80 * 16) / 8
    assert perf(state.get_logging_metrics()) == pytest.approx(
        {
            "perf/step_seconds": 4.0,
            "perf/data_wait_percent": 25.0,
            # Valid targets are summed over the two ranks.
            "perf/tokens_per_second": 20 / 2 / 8,
            "perf/padded_tokens_per_second": (8 + 16) / 8,
            "perf/tflops": flops_per_second / 1e12,
            "perf/mfu_percent": 100 * flops_per_second / 1e3,
        }
    )

    state.reset_window()
    update(state, [4], seconds=2)
    assert state.get_logging_metrics() == pytest.approx(
        {
            "global_step": 3,
            "train/loss_mean": 1.0,
            "train/loss_window_max": 1.0,
            "train/grad_norm_mean": 1.0,
            "train/grad_norm_window_max": 1.0,
            "train/tokens": 4,
            "train/total_tokens": 24,
            "perf/step_seconds": 2.0,
            "perf/data_wait_percent": 0.0,
            "perf/tokens_per_second": 1.0,
            "perf/padded_tokens_per_second": 0.0,
            "perf/tflops": 0.0,
            "perf/mfu_percent": 0.0,
        }
    )


def test_untimed_window_logs_no_throughput() -> None:
    state = TrainerState()
    update(state, [3])
    assert perf(state.get_logging_metrics()) == {}


def test_window_throughput_survives_a_checkpoint() -> None:
    state = TrainerState()
    update(state, [6], seconds=3, data_wait=1, padded_tokens=8, flops=80)
    restored = TrainerState()
    restored.load_state_dict(state.state_dict())
    update(restored, [2], seconds=1, padded_tokens=8, flops=80)
    assert perf(restored.get_logging_metrics()) == pytest.approx(
        {
            "perf/step_seconds": 2.0,
            "perf/data_wait_percent": 25.0,
            "perf/tokens_per_second": 2.0,
            "perf/padded_tokens_per_second": 4.0,
            "perf/tflops": 40 / 1e12,
        }
    )
