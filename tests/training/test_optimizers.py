import copy
from typing import cast

import pytest
import torch
from torch import nn

from flock.training.core.optimizers import (
    AdamWConfig,
    BaseOptimizer,
    MuonAdamW,
    MuonAdamWConfig,
    MuonConfig,
    OptimizerConfig,
    OptimizerInterface,
)


def test_adamw_forwards_configuration_and_matches_torch_updates() -> None:
    model = nn.Linear(3, 2)
    reference = copy.deepcopy(model)
    optimizer: BaseOptimizer = OptimizerInterface.get_interface(AdamWConfig)(
        AdamWConfig(lr=0.02, betas=(0.7, 0.95)), model
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
            MuonConfig(lr=0.01, momentum=0.9), nn.Linear(3, 2)
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
    original = OptimizerInterface.get_interface(type(config))(config, model)

    def make_sgd(config: AdamWConfig, model: nn.Module) -> BaseOptimizer:
        return torch.optim.SGD(model.parameters(), lr=config.lr)

    OptimizerInterface.register(AdamWConfig, make_sgd)
    assert OptimizerInterface.get_interface(AdamWConfig) is make_sgd
    replacement = OptimizerInterface.get_interface(type(config))(config, model)
    assert type(original) is torch.optim.AdamW
    assert type(replacement) is torch.optim.SGD
    assert replacement.param_groups[0]["lr"] == config.lr
    replacement.zero_grad()
    model(torch.ones(1, 3)).sum().backward()
    before = model.weight.detach().clone()
    replacement.step()
    torch.testing.assert_close(model.weight, before - config.lr)


class TinyLM(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.embed_tokens = nn.Embedding(7, 4)
        self.hidden = nn.Linear(4, 6)
        self.norm = nn.LayerNorm(6)
        self.down = nn.Linear(6, 4, bias=False)
        self.lm_head = nn.Linear(4, 7, bias=False)

    def forward(self, ids: torch.Tensor) -> torch.Tensor:  # ty: ignore[invalid-mutable-override]
        return self.lm_head(self.down(self.norm(self.hidden(self.embed_tokens(ids)))))


MUON_ADAMW = MuonAdamWConfig(lr=0.01, betas=(0.9, 0.95), muon_lr=0.02, weight_decay=0.01)


def test_muon_adamw_routes_hidden_matrices_to_muon() -> None:
    model = TinyLM()
    optimizer = OptimizerInterface.get_interface(MuonAdamWConfig)(MUON_ADAMW, model)
    assert isinstance(optimizer, MuonAdamW)
    muon_ids = {id(p) for p in optimizer.muon.param_groups[0]["params"]}
    adamw_ids = {id(p) for p in optimizer.adamw.param_groups[0]["params"]}
    assert muon_ids == {id(model.hidden.weight), id(model.down.weight)}
    assert adamw_ids == {id(p) for p in model.parameters()} - muon_ids
    assert [group["lr"] for group in optimizer.param_groups] == [0.02, 0.01]


def test_muon_adamw_matches_separate_torch_optimizers() -> None:
    model = TinyLM()
    reference = copy.deepcopy(model)
    optimizer = OptimizerInterface.get_interface(MuonAdamWConfig)(MUON_ADAMW, model)
    muon = torch.optim.Muon(
        [reference.hidden.weight, reference.down.weight], lr=0.02, weight_decay=0.1
    )
    adamw = torch.optim.AdamW(
        [
            reference.embed_tokens.weight,
            reference.hidden.bias,
            reference.norm.weight,
            reference.norm.bias,
            reference.lm_head.weight,
        ],
        lr=0.01,
        betas=(0.9, 0.95),
        weight_decay=0.01,
    )
    ids = torch.tensor([[1, 2, 3], [4, 5, 6]])
    for _ in range(3):
        for module in (model, reference):
            module.zero_grad()
            module(ids).square().mean().backward()
        optimizer.step()
        muon.step()
        adamw.step()
    for actual, expected in zip(model.parameters(), reference.parameters(), strict=True):
        torch.testing.assert_close(actual, expected, atol=0, rtol=0)


def test_muon_adamw_state_round_trips_through_one_group_list() -> None:
    model = TinyLM()
    optimizer = OptimizerInterface.get_interface(MuonAdamWConfig)(MUON_ADAMW, model)
    model(torch.tensor([[1, 2]])).sum().backward()
    optimizer.step()
    state = optimizer.state_dict()
    assert len(state["param_groups"]) == 2
    assert set(state["state"]) == {"muon", "adamw"}

    restored_model = copy.deepcopy(model)
    restored = OptimizerInterface.get_interface(MuonAdamWConfig)(MUON_ADAMW, restored_model)
    assert isinstance(restored, MuonAdamW)
    restored.load_state_dict(copy.deepcopy(state))
    torch.testing.assert_close(restored.state_dict(), state)
    # The scheduler edits param_groups, so they must be the optimizers' live groups.
    assert restored.param_groups[0] is restored.muon.param_groups[0]
    assert restored.param_groups[1] is restored.adamw.param_groups[0]
    with pytest.raises(ValueError, match="parameter groups"):
        restored.load_state_dict({**state, "param_groups": state["param_groups"][:1]})


def test_muon_adamw_needs_hidden_matrices() -> None:
    with pytest.raises(ValueError, match="hidden weight matrices"):
        OptimizerInterface.get_interface(MuonAdamWConfig)(MUON_ADAMW, nn.LayerNorm(4))
