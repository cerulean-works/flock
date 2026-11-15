import copy
from collections.abc import Iterable
from typing import cast

import pytest
import torch
from torch import nn

from flock.training.core.optimizers import (
    AdamWConfig,
    BaseOptimizer,
    MuonConfig,
    OptimizerConfig,
    OptimizerInterface,
)


def test_adamw_forwards_configuration_and_matches_torch_updates() -> None:
    model = nn.Linear(3, 2)
    reference = copy.deepcopy(model)
    optimizer: BaseOptimizer = OptimizerInterface.get_interface(AdamWConfig)(
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


def test_muon_remains_explicitly_unsupported() -> None:
    with pytest.raises(NotImplementedError, match="Muon"):
        OptimizerInterface.get_interface(MuonConfig)(
            MuonConfig(lr=0.01, momentum=0.9), nn.Linear(3, 2).parameters()
        )


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

    def make_sgd(config: AdamWConfig, params: Iterable[torch.Tensor]) -> BaseOptimizer:
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
