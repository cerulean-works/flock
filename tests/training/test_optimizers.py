import copy
from collections.abc import Iterable
from typing import cast

import pytest
import torch
from torch import nn
from torch.optim import Optimizer

from flock.training.core.optimizers import (
    AdamWConfig,
    MuonConfig,
    OptimizerConfig,
    OptimizerInterface,
    prepare_for_compile,
)


def test_adamw_forwards_configuration_and_matches_torch_updates() -> None:
    model = nn.Linear(3, 2)
    reference = copy.deepcopy(model)
    optimizer: Optimizer = OptimizerInterface.get_interface(AdamWConfig)(
        AdamWConfig(lr=0.02, betas=(0.7, 0.95)), model.parameters()
    )
    expected_optimizer = torch.optim.AdamW(reference.parameters(), lr=0.02, betas=(0.7, 0.95))
    assert type(optimizer) is torch.optim.AdamW
    assert optimizer.state_dict()["param_groups"] == expected_optimizer.state_dict()["param_groups"]
    x = torch.tensor([[1.0, -2.0, 3.0], [0.5, 1.0, -1.5]])
    for _ in range(2):
        optimizer.zero_grad()
        expected_optimizer.zero_grad()
        model(x).square().mean().backward()
        reference(x).square().mean().backward()
        optimizer.step()
        expected_optimizer.step()
    for actual, expected in zip(model.parameters(), reference.parameters(), strict=True):
        torch.testing.assert_close(actual, expected, atol=0, rtol=0)


def test_compile_preparation_holds_tensor_rates_and_saves_float_rates() -> None:
    model = nn.Linear(3, 2)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.02)
    model(torch.ones(1, 3)).sum().backward()
    optimizer.step()
    eager = copy.deepcopy(optimizer.state_dict())

    prepare_for_compile(optimizer, torch.device("cpu"))
    assert all(isinstance(group["lr"], torch.Tensor) for group in optimizer.param_groups)
    saved = optimizer.state_dict()
    # The rate round-trips through float32; resuming reapplies the scheduler's exact rates.
    saved_lr = saved["param_groups"][0]["lr"]
    assert isinstance(saved_lr, float) and saved_lr == pytest.approx(0.02)
    saved["param_groups"][0]["lr"] = eager["param_groups"][0]["lr"]
    torch.testing.assert_close(saved, eager, atol=0, rtol=0)

    # The schedule fills the live tensor; loading replaces it with the checkpoint's float.
    optimizer.param_groups[0]["lr"].fill_(0.5)
    optimizer.load_state_dict(eager)
    lr = optimizer.param_groups[0]["lr"]
    assert isinstance(lr, torch.Tensor) and lr.item() == pytest.approx(0.02)


def test_muon_companion_preserves_all_groups_on_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        OptimizerInterface, "_global_mapping", OptimizerInterface._global_mapping.copy()
    )

    def make_sgd(config: AdamWConfig, params: Iterable[torch.Tensor]) -> Optimizer:
        first, second = params
        return torch.optim.SGD(
            [{"params": [first]}, {"params": [second], "lr": config.lr / 2}],
            lr=config.lr,
            momentum=0.9,
        )

    OptimizerInterface.register(AdamWConfig, make_sgd)
    config = MuonConfig(
        lr=0.01,
        momentum=0.9,
        weight_decay=0.0,
        companion_optimizer_config=AdamWConfig(lr=0.02, betas=(0.7, 0.95)),
    )
    factory = OptimizerInterface.get_interface(MuonConfig)
    model = nn.Sequential(nn.Linear(3, 2), nn.Linear(2, 1))
    with torch.no_grad():
        for param in model.parameters():
            param.fill_(0.25)
    optimizer = factory(config, model.parameters())

    inputs = torch.ones(1, 3)

    def step(model: nn.Module, optimizer: Optimizer) -> None:
        optimizer.zero_grad()
        model(inputs).square().mean().backward()
        optimizer.step()

    step(model, optimizer)
    checkpoint = copy.deepcopy(optimizer.state_dict())
    assert len(checkpoint["param_groups"]) == 3
    assert len(checkpoint["state"]) == 4

    resumed_model = copy.deepcopy(model)
    resumed_optimizer = factory(config, resumed_model.parameters())
    resumed_optimizer.load_state_dict(checkpoint)
    names = [group["name"] for group in resumed_optimizer.param_groups]
    assert names == ["muon", "sgd", "sgd"]
    step(model, optimizer)
    step(resumed_model, resumed_optimizer)
    for actual, expected in zip(resumed_model.parameters(), model.parameters(), strict=True):
        torch.testing.assert_close(actual, expected, atol=0, rtol=0)


def test_unsupported_configuration_reports_actual_type() -> None:
    with pytest.raises(TypeError, match="Unsupported OptimizerConfig type object"):
        OptimizerInterface.get_interface(cast(type[OptimizerConfig], object))


def test_registered_factory_replaces_default_for_future_optimizers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        OptimizerInterface, "_global_mapping", OptimizerInterface._global_mapping.copy()
    )
    config = AdamWConfig(lr=0.02, betas=(0.7, 0.95))
    model = nn.Linear(3, 2)
    original = OptimizerInterface.get_interface(type(config))(config, model.parameters())

    def make_sgd(config: AdamWConfig, params: Iterable[torch.Tensor]) -> Optimizer:
        return torch.optim.SGD(params, lr=config.lr)

    OptimizerInterface.register(AdamWConfig, make_sgd)
    assert OptimizerInterface.get_interface(AdamWConfig) is make_sgd
    replacement = OptimizerInterface.get_interface(type(config))(config, model.parameters())
    assert type(original) is torch.optim.AdamW
    assert type(replacement) is torch.optim.SGD
    assert replacement.param_groups[0]["lr"] == config.lr
    replacement.zero_grad()
    model(torch.ones(1, 3)).sum().backward()
    before = model.weight.detach().clone()
    replacement.step()
    torch.testing.assert_close(model.weight, before - config.lr)
