from collections.abc import Iterator

import pytest
import torch

from flock.training.core.data_utils import Batch
from flock.training.core.optimizers import AdamWConfig, MuonConfig, OptimizerInterface
from flock.training.core.trainer import TrainerState


class Clock:
    """Stands in for `perf_counter`, moved forward by the test."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr("flock.training.core.trainer.perf_counter", clock)
    return clock


def batch(rows: int, width: int) -> Batch:
    ids = torch.zeros((rows, width), dtype=torch.long)
    return {"input_ids": ids, "attention_mask": ids.bool(), "labels": ids, "position_ids": ids}


def update(state: TrainerState, tokens: list[int]) -> None:
    one = torch.tensor(1.0)
    state.update(1, one.expand(len(tokens)), one, torch.tensor(tokens))


def test_window_reports_throughput_per_device(clock: Clock) -> None:
    optimizer = torch.optim.SGD([torch.zeros(1, requires_grad=True)], lr=0.2)
    state = TrainerState(
        flops_per_token=lambda width: 10 * width,
        peak_flops=None,
        world_size=2,
        optimizer=optimizer,
    )

    def loader() -> Iterator[Batch]:
        for item in (batch(2, 4), batch(2, 8)):
            clock.now += 1  # waiting for the batch
            yield item

    for _ in state.batches(loader()):
        clock.now += 3  # the update
        update(state, [6, 4])

    # 8 s have passed, 2 of them waiting for batches.
    metrics = state.get_logging_metrics()
    assert {key: value for key, value in metrics.items() if key.startswith("perf/")} == (
        pytest.approx(
            {
                "perf/step_seconds": 4.0,
                "perf/data_wait_percent": 25.0,
                # Valid targets are summed over the two ranks.
                "perf/tokens_per_second": 20 / 2 / 8,
                "perf/padded_tokens_per_second": (8 + 16) / 8,
                "perf/tflops": (40 * 8 + 80 * 16) / 8 / 1e12,
            }
        )
    )
    assert metrics["train/lr_adamw"] == 0.2

    state.reset_window()
    optimizer.param_groups[0]["lr"] = 0.3
    clock.now += 2
    update(state, [4])
    assert state.get_logging_metrics() == pytest.approx(
        {
            "global_step": 3,
            "train/loss_mean": 1.0,
            "train/loss_window_max": 1.0,
            "train/grad_norm_mean": 1.0,
            "train/grad_norm_window_max": 1.0,
            "train/tokens": 4,
            "train/total_tokens": 24,
            "train/lr_adamw": 0.3,
            "perf/step_seconds": 2.0,
            "perf/data_wait_percent": 0.0,
            "perf/tokens_per_second": 1.0,
            "perf/padded_tokens_per_second": 0.0,
            "perf/tflops": 0.0,
        }
    )


def test_muon_rates_are_named_by_optimizer() -> None:
    config = MuonConfig(
        lr=0.02,
        momentum=0.95,
        weight_decay=0.0,
        companion_optimizer_config=AdamWConfig(lr=3e-4, betas=(0.9, 0.95)),
    )
    model = torch.nn.Linear(3, 2)
    optimizer = OptimizerInterface.get_interface(MuonConfig)(config, model.parameters())
    state = TrainerState(
        flops_per_token=lambda width: 0, peak_flops=None, world_size=1, optimizer=optimizer
    )
    one = torch.tensor(1.0)
    state.update(add_step=1, loss=one, grad_norm=one, tokens=torch.tensor(1))
    metrics = state.get_logging_metrics()
    assert (metrics["train/lr_muon"], metrics["train/lr_adamw"]) == (0.02, 3e-4)
