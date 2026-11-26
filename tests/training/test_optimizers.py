import copy
from collections.abc import Iterable
from typing import cast

import pytest
import torch
from torch import nn
from torch.optim import Optimizer
from torch.optim.optimizer import ParamsT

from flock.training.core.models.llama32 import Llama32, Llama32Config
from flock.training.core.models.olmo2 import Olmo2, Olmo2Config
from flock.training.core.optimizers import (
    AdamWConfig,
    MuonConfig,
    OptimizerConfig,
    OptimizerInterface,
    prepare_for_compile,
)
from flock.training.core.optimizers.groups import split_embedding_parameters
from flock.training.core.optimizers.muon import MuonWithCompanion


def test_adamw_forwards_configuration_and_matches_torch_updates() -> None:
    model = nn.Linear(3, 2)
    reference = copy.deepcopy(model)
    optimizer: Optimizer = OptimizerInterface.get_interface(AdamWConfig)(
        AdamWConfig(lr=0.02, betas=(0.7, 0.95)), model.parameters()
    )
    expected_optimizer = torch.optim.AdamW(
        reference.parameters(), lr=0.02, betas=(0.7, 0.95), eps=1e-8, weight_decay=0.1
    )
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

    def make_sgd(config: AdamWConfig, params: nn.Module | ParamsT) -> Optimizer:
        first, second = cast(Iterable[torch.Tensor], params)
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

    def make_sgd(config: AdamWConfig, params: nn.Module | ParamsT) -> Optimizer:
        return torch.optim.SGD(cast(ParamsT, params), lr=config.lr)

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


def _group_ids(optimizer: Optimizer) -> dict[str, set[int]]:
    return {
        group["name"]: {id(param) for param in group["params"]} for group in optimizer.param_groups
    }


def test_embedding_split_keeps_a_tied_weight_once(llama32_config: Llama32Config) -> None:
    model = Llama32(llama32_config)
    assert model.lm_head.weight is model.model.embed_tokens.weight
    embeddings, others = split_embedding_parameters(model)
    assert [id(p) for p in embeddings] == [id(model.model.embed_tokens.weight)]
    ids = [id(p) for p in embeddings + others]
    assert len(ids) == len(set(ids)) == len(list(model.parameters()))


def test_adamw_decays_every_parameter_but_the_embeddings(config: Olmo2Config) -> None:
    model = Olmo2(config)
    optimizer = OptimizerInterface.get_interface(AdamWConfig)(
        AdamWConfig(lr=0.01, betas=(0.9, 0.95)), model
    )
    groups = {group["name"]: group for group in optimizer.param_groups}
    assert groups["adamw"]["weight_decay"] == 0.1
    assert groups["adamw_embeddings"]["weight_decay"] == 0.0
    ids = _group_ids(optimizer)
    assert ids["adamw_embeddings"] == {id(model.model.embed_tokens.weight)}
    # Norm weights and the untied output projection decay.
    norms = {id(m.weight) for m in model.modules() if isinstance(m, nn.RMSNorm)}
    assert norms and norms <= ids["adamw"]
    assert id(model.lm_head.weight) in ids["adamw"]
    assert ids["adamw"] | ids["adamw_embeddings"] == {id(p) for p in model.parameters()}


def test_adamw_can_decay_embeddings(config: Olmo2Config) -> None:
    model = Olmo2(config)
    optimizer = OptimizerInterface.get_interface(AdamWConfig)(
        AdamWConfig(lr=0.01, betas=(0.9, 0.95), weight_decay=0.2, no_decay_embeddings=False),
        model,
    )
    assert [(g["name"], g["weight_decay"]) for g in optimizer.param_groups] == [("adamw", 0.2)]


def test_muon_sends_tied_embeddings_to_the_companion_without_decay(
    llama32_config: Llama32Config,
) -> None:
    model = Llama32(llama32_config)
    config = MuonConfig(
        lr=6e-3,
        momentum=0.95,
        adjust_lr_fn="match_rms_adamw",
        companion_optimizer_config=AdamWConfig(lr=3e-3, betas=(0.9, 0.99)),
    )
    optimizer = OptimizerInterface.get_interface(MuonConfig)(config, model)
    assert isinstance(optimizer, MuonWithCompanion)
    groups = {group["name"]: group for group in optimizer.param_groups}
    assert list(groups) == ["muon", "adamw", "adamw_embeddings"]
    assert groups["muon"]["weight_decay"] == 0.1
    assert groups["muon"]["adjust_lr_fn"] == "match_rms_adamw"
    assert groups["adamw"]["weight_decay"] == 0.1
    assert groups["adamw_embeddings"]["weight_decay"] == 0.0
    ids = _group_ids(optimizer)
    embedding = model.model.embed_tokens.weight
    assert ids["adamw_embeddings"] == {id(embedding)}
    assert id(embedding) not in ids["muon"]
    assert all(p.ndim == 2 for p in groups["muon"]["params"])
    assert all(p.ndim == 1 for p in groups["adamw"]["params"])
    # Every trainable parameter, the tied weight included, is in exactly one group.
    all_ids = [id(p) for group in optimizer.param_groups for p in group["params"]]
    assert sorted(all_ids) == sorted(id(p) for p in model.parameters())

    def step(model: nn.Module, optimizer: Optimizer) -> None:
        optimizer.zero_grad()
        batch = torch.tensor([[1, 2, 3, 4]])
        inputs = {
            "input_ids": batch,
            "attention_mask": torch.ones_like(batch, dtype=torch.bool),
            "labels": batch,
            "position_ids": torch.arange(4)[None],
        }
        model(inputs).square().mean().backward()
        optimizer.step()

    step(model, optimizer)
    checkpoint = copy.deepcopy(optimizer.state_dict())
    resumed_model = copy.deepcopy(model)
    resumed = OptimizerInterface.get_interface(MuonConfig)(config, resumed_model)
    resumed.load_state_dict(checkpoint)
    assert isinstance(resumed, MuonWithCompanion)
    assert len(resumed.muon.param_groups) == 1 and len(resumed.companion.param_groups) == 2
    step(model, optimizer)
    step(resumed_model, resumed)
    for actual, expected in zip(resumed_model.parameters(), model.parameters(), strict=True):
        torch.testing.assert_close(actual, expected, atol=0, rtol=0)


def test_muon_scheduler_scales_every_group_of_both_optimizers(
    llama32_config: Llama32Config,
) -> None:
    from flock.training.core.lr_scheduler import (
        LRSchedulerConfig,
        LRScheduleSegment,
        StepLRScheduler,
    )

    model = Llama32(llama32_config)
    optimizer = OptimizerInterface.get_interface(MuonConfig)(
        MuonConfig(
            lr=0.02,
            momentum=0.95,
            companion_optimizer_config=AdamWConfig(lr=0.004, betas=(0.9, 0.99)),
        ),
        model,
    )
    assert isinstance(optimizer, MuonWithCompanion)
    scheduler = StepLRScheduler(
        optimizer,
        LRSchedulerConfig(schedule=[LRScheduleSegment(start=0, end=4, start_ratio=0, end_ratio=1)]),
        total_steps=4,
    )
    scheduler.step()
    assert [g["lr"] for g in optimizer.muon.param_groups] == [pytest.approx(0.005)]
    assert [g["lr"] for g in optimizer.companion.param_groups] == [pytest.approx(0.001)] * 2
