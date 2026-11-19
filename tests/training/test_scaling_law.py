"""Scaling-law sweeps: config resolution, slot planning, claims, launching and token saves."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

import lance
import numpy as np
import pyarrow as pa
import pytest
import torch
import yaml

from flock.training import TokenChunkDataset
from flock.training.experiments.scaling_law import sweep as sweep_module
from flock.training.experiments.scaling_law.config import (
    ExperimentConfig,
    Sweep,
    parse_token_count,
)
from flock.training.experiments.scaling_law.sweep import (
    Allocation,
    Launcher,
    RunDirectory,
    Slot,
    SlotLayout,
    capacity_warnings,
    run_state,
    train_command,
    try_claim,
    write_configs,
)
from flock.training.experiments.scaling_law.train import build_trainer, prepare_cache
from flock.training.experiments.scaling_law.trainer import step_target_counts, steps_for_tokens

if TYPE_CHECKING:
    from transformers import TokenizersBackend

    from flock.training.core.models.olmo2 import Olmo2Config


def base_fields(tmp_path: Path) -> dict[str, Any]:
    return {
        "data": {"cache_dir": str(tmp_path / "tokens"), "chunk_length": 4},
        "model": {"preset": "76m", "attn_implementation": "sdpa"},
        "optimizer": {"type": "muon_adamw", "lr": 0.01, "muon_lr": 0.02},
        "schedule": [{"start": 0, "end": 1.0, "start_ratio": 1.0, "end_ratio": 0.0}],
        "trainer": {"batch_tokens": 8, "wandb_mode": "disabled"},
        "stop_tokens": "1k",
    }


def make_sweep(tmp_path: Path, **settings: Any) -> Sweep:
    return Sweep.model_validate(
        {
            "sweep": {
                "name": "pilot",
                "output_root": str(tmp_path / "sweeps"),
                "gpus_per_run": 2,
                **settings,
            },
            "base": base_fields(tmp_path),
        }
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (3800, 3800),
        (3.8e9, 3_800_000_000),
        ("3.8e9", 3_800_000_000),
        ("1.5B", 1_500_000_000),
        ("500M", 500_000_000),
        ("2_000", 2000),
        ("20k", 20_000),
    ],
)
def test_token_counts_accept_scientific_and_suffixed_values(value: Any, expected: int) -> None:
    assert parse_token_count(value) == expected


def test_token_counts_must_be_whole() -> None:
    with pytest.raises(ValueError, match="whole"):
        parse_token_count("1.5")


def test_sweep_expands_runs_zip_and_grid(tmp_path: Path) -> None:
    sweep = make_sweep(
        tmp_path,
        name_template="{model.preset}-lr{optimizer.muon_lr}-s{seed}-{stop_tokens}",
        runs=[{"model.preset": "76m"}, {"model.preset": "190m"}],
        zip={"stop_tokens": ["1k", "2k"], "save_tokens": [["500"], ["1k"]]},
        grid={"optimizer.muon_lr": [0.01, 0.02], "seed": [0, 1, 2]},
    )
    experiments = sweep.resolve()
    assert len(experiments) == 2 * 2 * 2 * 3
    first = experiments[0]
    assert first.name == "76m-lr0.01-s0-1k"
    assert first.output_dir == str(tmp_path / "sweeps/pilot/runs/76m-lr0.01-s0-1k/checkpoints")
    assert first.wandb_group == "pilot"
    assert first.stop_tokens == 1000 and first.save_tokens == [500]
    assert {e.model.preset for e in experiments} == {"76m", "190m"}
    assert len({e.name for e in experiments}) == len(experiments)
    # Overrides never leak between runs.
    assert sweep.base["model"]["preset"] == "76m"


def test_default_names_number_the_runs(tmp_path: Path) -> None:
    names = [e.name for e in make_sweep(tmp_path, grid={"seed": [0, 1]}).resolve()]
    assert names == ["pilot-000", "pilot-001"]


def test_duplicate_names_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="share names"):
        make_sweep(tmp_path, name_template="{model.preset}", grid={"seed": [0, 1]}).resolve()


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"trainer.max_steps": 3}, "outside the trainer table"),
        ({"trainer.batch_size": 2}, "exactly one"),
        ({"save_tokens": ["2k"]}, "must not exceed"),
        ({"model.preset": "9b"}, "Unknown model preset"),
        ({"optimizer.type": "sgd"}, "optimizer"),
    ],
)
def test_invalid_runs_name_their_index(
    tmp_path: Path, override: dict[str, Any], message: str
) -> None:
    sweep = make_sweep(tmp_path, runs=[{}, override])
    with pytest.raises(ValueError, match=rf"(?s)Run 1 \(pilot-001\).*{message}"):
        sweep.resolve()


def test_experiments_round_trip_through_their_json(tmp_path: Path) -> None:
    experiment = make_sweep(tmp_path).resolve()[0]
    path = tmp_path / "run.json"
    path.write_text(sweep_module.experiment_json(experiment))
    assert ExperimentConfig.load(path) == experiment
    config = experiment.trainer_config(max_steps=5)
    assert config.max_steps == 5 and config.run_name == experiment.name


@pytest.mark.parametrize(
    ("nodes", "per_node", "per_run", "slots", "nodes_per_run"),
    [(4, 4, 4, 4, 1), (4, 4, 2, 8, 1), (4, 4, 8, 2, 2), (4, 8, 3, 8, 1), (3, 4, 8, 1, 2)],
)
def test_slot_layout_fits_runs_to_the_allocation(
    nodes: int, per_node: int, per_run: int, slots: int, nodes_per_run: int
) -> None:
    layout = SlotLayout.plan(Allocation(nodes, per_node), per_run)
    assert (layout.slots, layout.nodes_per_run) == (slots, nodes_per_run)


@pytest.mark.parametrize(("nodes", "per_run"), [(4, 6), (1, 8)])
def test_slot_layout_rejects_runs_that_cannot_fit(nodes: int, per_run: int) -> None:
    with pytest.raises(ValueError, match="multiple|needs"):
        SlotLayout.plan(Allocation(nodes, 4), per_run)


def test_capacity_warnings_explain_idle_gpus() -> None:
    allocation = Allocation(4, 4)
    layout = SlotLayout.plan(allocation, 4)
    assert capacity_warnings(20, allocation, layout) == []
    [uneven] = capacity_warnings(22, allocation, layout)
    assert "last wave runs 2 and leaves 2 slots idle" in uneven
    [few] = capacity_warnings(3, allocation, layout)
    assert "1 slot stay idle" in few
    odd = Allocation(1, 8)
    [idle] = capacity_warnings(8, odd, SlotLayout.plan(odd, 3))
    assert "2 of 8 GPUs" in idle


def test_claims_are_exclusive_and_replace_only_stale_owners(tmp_path: Path) -> None:
    run = RunDirectory(tmp_path)
    assert try_claim(run, {"job": "1"}, is_live=lambda claim: True)
    assert not try_claim(run, {"job": "2"}, is_live=lambda claim: True)
    assert run.read(run.claim) == {"job": "1"}
    assert try_claim(run, {"job": "2"}, is_live=lambda claim: False)
    assert run.read(run.claim) == {"job": "2"}
    assert len(list(tmp_path.glob("claim.stale.*.json"))) == 1


def test_local_claims_from_exited_processes_are_stale(tmp_path: Path) -> None:
    run = RunDirectory(tmp_path)
    owner = sweep_module.current_owner(0)
    dead = {**owner, "job": f"local-{owner['host']}-1", "pid": 2**22 + 7}
    assert not sweep_module.claim_is_live(dead)
    assert sweep_module.claim_is_live(owner)
    run.write(run.claim, dead)
    assert run_state(run) == "interrupted"


def test_srun_command_runs_one_torchrun_per_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SLURM_GPUS_ON_NODE", "4")
    monkeypatch.setenv("SLURM_CPUS_ON_NODE", "64")
    monkeypatch.setenv("SLURM_MEM_PER_NODE", "512000")
    run = RunDirectory(tmp_path / "runs" / "76m-s0")
    layout = SlotLayout.plan(Allocation(4, 4), 8)
    command = train_command(
        run, layout, Slot(1), mode="srun", master_port=29500, srun_args=["--partition=gpu"]
    )
    assert command[:3] == ["srun", "--exact", "--kill-on-bad-exit=1"]
    assert "--nodes=2" in command and "--ntasks=2" in command
    assert "--gpus-per-task=4" in command
    assert "--cpus-per-task=64" in command and "--mem=512000M" in command
    assert "--partition=gpu" in command
    script = command[-1]
    assert "--nnodes=2" in script and "--nproc-per-node=4" in script
    assert "--master-port=29501" in script and '--node-rank="$SLURM_PROCID"' in script
    assert str(run.config) in script

    local = train_command(run, layout, Slot(2), mode="local", master_port=29500)
    assert "--master-addr=127.0.0.1" in local and "--master-port=29502" in local
    half = train_command(
        run, SlotLayout.plan(Allocation(1, 4), 2), Slot(0), mode="srun", master_port=1
    )
    assert "--cpus-per-task=32" in half and "--mem=256000M" in half


def test_local_launch_trains_every_run_and_records_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sweep = make_sweep(tmp_path, gpus_per_run=1, grid={"seed": [0, 1, 2, 3, 4]})
    runs = write_configs(sweep, sweep.resolve())
    seen_devices = []

    def command(self: Launcher, run: RunDirectory, slot: Slot) -> list[str]:
        seen_devices.append(slot.devices)
        # Seed 3 fails; the rest succeed.
        code = 1 if run.path.name.endswith("003") else 0
        return [sys.executable, "-c", f"import sys; print('training'); sys.exit({code})"]

    monkeypatch.setattr(Launcher, "command", command)
    launcher = Launcher(sweep, runs, SlotLayout.plan(Allocation(1, 2), 1), mode="local")
    results = launcher.launch()
    assert results == {run.path.name: int(run.path.name.endswith("003")) for run in runs}
    assert {devices for devices in seen_devices} == {(0,), (1,)}
    assert [run_state(run) for run in runs] == ["done", "done", "done", "failed", "done"]
    assert not any(run.claim.exists() for run in runs)
    assert "training" in runs[0].log.read_text()

    # A relaunch only retries what is not done.
    relaunched = Launcher(sweep, runs, SlotLayout.plan(Allocation(1, 2), 1), mode="local")
    assert [run.path.name for run in relaunched.queue] == ["pilot-003"]


def test_runs_with_checkpoints_keep_their_config(tmp_path: Path) -> None:
    sweep = make_sweep(tmp_path)
    [run] = write_configs(sweep, sweep.resolve())
    (run.path / "checkpoints" / "checkpoint-3").mkdir(parents=True)
    changed = make_sweep(tmp_path, runs=[{"optimizer.muon_lr": 0.05}])
    with pytest.raises(ValueError, match="different config"):
        write_configs(changed, changed.resolve())
    write_configs(sweep, sweep.resolve())


def test_steps_for_tokens_finds_the_first_update_reaching_each_target() -> None:
    counts = np.array([5, 5, 3, 7])
    assert steps_for_tokens(counts, [1, 5, 6, 13, 20]) == [1, 1, 2, 3, 4]
    with pytest.raises(ValueError, match="trains on 20 tokens"):
        steps_for_tokens(counts, [21])


@pytest.mark.parametrize("world_size", [1, 2])
def test_token_budget_run_saves_at_targets_and_stops(
    config: Olmo2Config,
    tokenizer: TokenizersBackend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    world_size: int,
) -> None:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    tokenizer.save_pretrained(tmp_path / "tokenizer")
    source = tmp_path / "source.lance"
    lance.write_dataset(pa.table({"text": ["a b c d " * 6, "b c d " * 5, "a b", "c d a"]}), source)
    model = {**asdict(config), "hf_repo": str(tmp_path / "tokenizer")}
    model.pop("attn_implementation")
    fields = {
        **base_fields(tmp_path),
        "data": {
            "cache_dir": str(tmp_path / "tokens"),
            "chunk_length": 4,
            "source": {"uri": str(source), "version": 1},
        },
        "name": "tiny",
        "output_dir": str(tmp_path / "checkpoints"),
        "model": {"attn_implementation": "sdpa", "overrides": model},
        "trainer": {
            "batch_tokens": 6,
            "dataloader_num_workers": 0,
            "dataloader_prefetch_factor": None,
            "wandb_mode": "disabled",
        },
        "stop_tokens": 20,
        "save_tokens": [7],
    }
    experiment = ExperimentConfig.model_validate(fields)
    prepare_cache(experiment.data, experiment.model)

    if world_size == 1:
        trainer = build_trainer(experiment)
        trainer.train()
        records = json.loads((Path(trainer.output_dir) / "token_checkpoints.json").read_text())
        assert [record["tokens"] for record in records] == [7, 20]
        steps = [record["step"] for record in records]
        saved = sorted(
            int(path.name.split("-")[1]) for path in trainer.output_dir.glob("checkpoint-*")
        )
        assert saved == steps
        assert trainer.total_steps == steps[-1]
        assert int(trainer.state.step) == steps[-1]
        assert int(trainer.state.total_tokens) == records[-1]["trained_tokens"] >= 20
        return

    # Ranks share one plan, so two ranks reach the budget in fewer updates.
    dataset = TokenChunkDataset(cache_dir=experiment.data.cache_dir, chunk_length=4)
    trainer_config = experiment.trainer_config()
    single = step_target_counts(trainer_config, dataset, 1)
    double = step_target_counts(trainer_config, dataset, 2)
    assert single.sum() == double.sum() == dataset.target_counts().sum()
    assert steps_for_tokens(double, [20])[0] < steps_for_tokens(single, [20])[0]


def test_pilot_sweep_resolves() -> None:
    path = Path(sweep_module.__file__).with_name("pilot.yaml")
    sweep = Sweep.model_validate(yaml.safe_load(path.read_text()))
    experiments = sweep.resolve()
    assert len(experiments) == 20
    layout = SlotLayout.plan(Allocation(4, 4), sweep.sweep.gpus_per_run)
    assert capacity_warnings(len(experiments), Allocation(4, 4), layout) == []
