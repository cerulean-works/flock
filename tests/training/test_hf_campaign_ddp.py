from __future__ import annotations

import hashlib
import json
import os
import random
import socket
import sys
import time
from collections.abc import Iterable
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import torch
import torch.distributed as dist
import torch.multiprocessing as mp
from accelerate import Accelerator, checkpointing
from accelerate.state import AcceleratorState
from accelerate.utils import DistributedType

from flock.training import DatasetConfig
from flock.training.experiments.hf_campaign import runtime, train
from flock.training.experiments.hf_campaign.provisional import ProvisionalMix
from flock.training.experiments.hf_campaign.runtime import Experiment, GpuExperiment
from tests.training.test_hf_campaign import sdpa_config, tiny_config, word_tokenizer, write_mix


def test_rank_shards_tile_every_update_without_changing_the_contract(tmp_path: Path) -> None:
    config = write_mix(tmp_path / "mix.lance", ["2 3 4 5 6 7 8 9"] * 20)
    whole = ProvisionalMix(config, word_tokenizer(), window=3, rows_per_update=4)
    shards = [
        ProvisionalMix(config, word_tokenizer(), window=3, rows_per_update=4, rank=r, world_size=2)
        for r in range(2)
    ]
    for update in range(len(whole)):
        torch.testing.assert_close(torch.cat([shard[update] for shard in shards]), whole[update])
    assert all(shard.contract == whole.contract for shard in shards)
    assert all(len(shard) == len(whole) for shard in shards)
    with pytest.raises(ValueError, match="divide evenly"):
        ProvisionalMix(config, word_tokenizer(), window=3, rows_per_update=3, world_size=2)


def distributed_worker(rank: int, port: int, root: str) -> None:
    os.environ.update(
        RANK=str(rank),
        LOCAL_RANK=str(rank),
        WORLD_SIZE="2",
        LOCAL_WORLD_SIZE="2",
        MASTER_ADDR="127.0.0.1",
        MASTER_PORT=str(port),
        ACCELERATE_USE_CPU="1",
    )
    torch.set_num_threads(1)
    AcceleratorState._reset_state(True)
    accelerator = Accelerator(cpu=True, mixed_precision="no")
    experiment = Experiment(
        tiny_config(),
        accelerator=accelerator,
        source={"world_size": 2},
        peak_lr=1e-3,
        betas=(0.9, 0.999),
        weight_decay=0.0,
        seed=3,
        total_targets=64,
        warmup_targets=16,
    )
    windows = torch.tensor([[2, 3, 4, 1, 5], [6, 7, 8, 9, 2], [9, 8, 7, 6, 5], [4, 3, 2, 1, 9]])
    rows = windows[rank * 2 : (rank + 1) * 2]
    unclipped = []
    clip = torch.nn.utils.clip_grad_norm_

    def capture(parameters: Iterable[torch.Tensor], *args: Any, **kwargs: Any) -> Any:
        parameters = list(parameters)
        for parameter in parameters:
            assert parameter.grad is not None
            unclipped.append(parameter.grad.clone())
        return clip(parameters, *args, **kwargs)

    torch.nn.utils.clip_grad_norm_ = capture
    loss = experiment._step([(part[:, :-1], part[:, 1:]) for part in rows.split(1)])
    torch.nn.utils.clip_grad_norm_ = clip
    torch.save(
        {"loss": loss, "gradients": unclipped, "targets": experiment.targets},
        Path(root) / f"rank-{rank}.pt",
    )
    torch.manual_seed(100 + rank)
    np.random.seed(200 + rank)
    random.seed(300 + rank)
    experiment.checkpoint(Path(root) / "checkpoint")
    expected_random = (torch.rand(3), np.random.rand(3), random.random())
    expected = experiment._step([(rows[:, :-1], rows[:, 1:])])
    expected_weights = {k: v.clone() for k, v in experiment.model.state_dict().items()}
    experiment.resume(Path(root) / "checkpoint")
    torch.testing.assert_close(torch.rand(3), expected_random[0], rtol=0, atol=0)
    np.testing.assert_array_equal(np.random.rand(3), expected_random[1])
    assert random.random() == expected_random[2]
    assert experiment._step([(rows[:, :-1], rows[:, 1:])]) == expected
    for name, weight in experiment.model.state_dict().items():
        torch.testing.assert_close(weight, expected_weights[name], rtol=0, atol=0)
    dist.destroy_process_group()
    AcceleratorState._reset_state(True)


def test_gloo_gradients_loss_accounting_and_resume_match_one_process(tmp_path: Path) -> None:
    # No fixed port: concurrent test invocations can run their own groups.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    mp.spawn(distributed_worker, args=(port, str(tmp_path)), nprocs=2, join=True)
    AcceleratorState._reset_state(True)
    experiment = Experiment(
        tiny_config(),
        accelerator=Accelerator(cpu=True, mixed_precision="no"),
        source={},
        peak_lr=1e-3,
        betas=(0.9, 0.999),
        weight_decay=0.0,
        seed=3,
        total_targets=64,
        warmup_targets=16,
    )
    windows = torch.tensor([[2, 3, 4, 1, 5], [6, 7, 8, 9, 2], [9, 8, 7, 6, 5], [4, 3, 2, 1, 9]])
    # Read the oracle gradient before clipping, which would hide an incorrect divisor.
    logits = experiment.model(windows[:, :-1], use_cache=False).logits.float()
    loss = torch.nn.functional.cross_entropy(logits.flatten(0, 1), windows[:, 1:].flatten())
    loss.backward()
    for rank in range(2):
        result = torch.load(tmp_path / f"rank-{rank}.pt", weights_only=True)
        assert result["targets"] == 16
        assert result["loss"] == pytest.approx(float(loss.detach()), abs=1e-6)
        for gradient, parameter in zip(
            result["gradients"], experiment.model.parameters(), strict=True
        ):
            torch.testing.assert_close(gradient, parameter.grad, rtol=1e-5, atol=1e-7)
    with pytest.raises(ValueError, match="contract"):
        experiment.resume(tmp_path / "checkpoint")
    manifest = json.loads((tmp_path / "checkpoint" / "COMMITTED.json").read_text())
    assert {"random_states_0.pkl", "random_states_1.pkl"} <= manifest["files"].keys()
    AcceleratorState._reset_state(True)


def test_one_gpu_contract_matches_the_pre_distributed_runtime(tmp_path: Path) -> None:
    AcceleratorState._reset_state(True)
    previous = os.environ.get("ACCELERATE_USE_CPU")
    os.environ["ACCELERATE_USE_CPU"] = "1"
    try:
        config = write_mix(tmp_path / "mix", ["2 3 4 5 6 7 8 9"] * 4)
        mix = ProvisionalMix(config, word_tokenizer(), window=3, rows_per_update=2)
        mix.contract["uri"] = "fixture://one-gpu-contract"
        experiment = GpuExperiment(
            sdpa_config(),
            mix,
            peak_lr=1e-3,
            seed=17,
            total_targets=24,
            warmup_targets=6,
            micro_rows=1,
            device="cpu",
        )
        # Generated with this fixture on the one-device implementation before DDP.
        assert (
            experiment.contract
            == "cffcb8383ff984485ca0e16581a0ea18fcfcc390fac8b57ae3b8552152f4c560"
        )
    finally:
        AcceleratorState._reset_state(True)
        if previous is None:
            os.environ.pop("ACCELERATE_USE_CPU", None)
        else:
            os.environ["ACCELERATE_USE_CPU"] = previous


def distributed_gpu_worker(rank: int, port: int, root: str) -> None:
    os.environ.update(
        RANK=str(rank),
        LOCAL_RANK=str(rank),
        WORLD_SIZE="2",
        LOCAL_WORLD_SIZE="2",
        MASTER_ADDR="127.0.0.1",
        MASTER_PORT=str(port),
        ACCELERATE_USE_CPU="1",
    )
    torch.set_num_threads(1)
    AcceleratorState._reset_state(True)
    mix = ProvisionalMix(
        DatasetConfig(uri=str(Path(root) / "mix"), version=1),
        word_tokenizer(),
        window=3,
        rows_per_update=4,
        rank=rank,
        world_size=2,
    )
    arguments: dict[str, Any] = {
        "peak_lr": 1e-3,
        "seed": 17,
        "total_targets": 36,
        "warmup_targets": 12,
        "device": "cpu",
    }
    with pytest.raises(ValueError, match="micro-batches"):
        GpuExperiment(sdpa_config(), mix, micro_rows=3, **arguments)
    os.environ["LOCAL_WORLD_SIZE"] = "1"
    with pytest.raises(ValueError, match="one node"):
        GpuExperiment(sdpa_config(), mix, micro_rows=1, **arguments)
    os.environ["LOCAL_WORLD_SIZE"] = "2"
    experiment = GpuExperiment(sdpa_config(), mix, micro_rows=1, **arguments)
    assert experiment.source["world_size"] == 2
    assert experiment.update_shape == (2, 4)
    experiment.update(mix[0])
    experiment.checkpoint(Path(root) / "gpu-checkpoint")
    expected = experiment.update(mix[1])
    expected_weights = {k: v.clone() for k, v in experiment.model.state_dict().items()}
    with pytest.MonkeyPatch.context() as patch:
        if rank == 1:

            def refuse_peer_hash(*args: Any, **kwargs: Any) -> Any:
                raise AssertionError("only rank zero verifies the shared checkpoint")

            patch.setattr(runtime.hashlib, "file_digest", refuse_peer_hash)
        experiment.resume(Path(root) / "gpu-checkpoint")
    assert experiment.update(mix[1]) == expected
    assert experiment.targets == 24
    for name, weight in experiment.model.state_dict().items():
        torch.testing.assert_close(weight, expected_weights[name], rtol=0, atol=0)
    experiment.export(Path(root) / "export", word_tokenizer())
    experiment.accelerator.wait_for_everyone()

    # Each failure reaches both ranks without poisoning the group or publishing a checkpoint.
    if rank == 0:
        (Path(root) / "locked.incomplete").mkdir()
    experiment.accelerator.wait_for_everyone()
    with pytest.raises(RuntimeError, match="FileExistsError"):
        experiment.checkpoint(Path(root) / "locked")

    def fail_write(*args: Any, **kwargs: Any) -> Any:
        error: Any = OSError("injected checkpoint failure")
        error.unpicklable = lambda: None
        raise error

    for phase in ("save", "publish"):
        with pytest.MonkeyPatch.context() as patch:
            if phase == "save" and rank == 1:
                patch.setattr(experiment.accelerator, "save_state", fail_write)
            if phase == "publish" and rank == 0:
                patch.setattr(runtime.os, "fsync", fail_write)
            with pytest.raises(RuntimeError, match="injected checkpoint failure"):
                experiment.checkpoint(Path(root) / phase)
        assert (Path(root) / f"{phase}.incomplete").is_dir()
        assert not (Path(root) / phase).exists()

    original_load = torch.load
    for phase in ("load", "rng-read", "rng-apply"):
        with pytest.MonkeyPatch.context() as patch:
            if rank == 1:
                if phase == "load":
                    patch.setattr(experiment.accelerator, "load_state", fail_write)
                elif phase == "rng-apply":
                    patch.setattr(torch, "set_rng_state", fail_write)
                else:

                    def fail_rng_read(path: Any, *args: Any, **kwargs: Any) -> Any:
                        if str(path).endswith("random_states_1.pkl"):
                            raise OSError("injected checkpoint failure")
                        return original_load(path, *args, **kwargs)

                    patch.setattr(torch, "load", fail_rng_read)
            with pytest.raises(RuntimeError, match="injected checkpoint failure"):
                experiment.resume(Path(root) / "gpu-checkpoint")
        experiment.accelerator.wait_for_everyone()

    if rank == 0:
        checkpoint = Path(root) / "gpu-checkpoint"
        (checkpoint / "random_states_1.pkl").unlink()
        manifest = json.loads((checkpoint / "COMMITTED.json").read_text())
        del manifest["files"]["random_states_1.pkl"]
        (checkpoint / "COMMITTED.json").write_text(json.dumps(manifest))
    experiment.accelerator.wait_for_everyone()
    with pytest.raises(RuntimeError, match="missing rank 1's RNG"):
        experiment.resume(Path(root) / "gpu-checkpoint")
    dist.destroy_process_group()
    AcceleratorState._reset_state(True)


def test_gpu_path_uses_shards_and_resumes_under_gloo(tmp_path: Path) -> None:
    write_mix(tmp_path / "mix", ["2 3 4 5 6 7 8 9"] * 10)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    mp.spawn(distributed_gpu_worker, args=(port, str(tmp_path)), nprocs=2, join=True)
    card = json.loads((tmp_path / "export" / "experiment.json").read_text())
    assert card["targets"] == 24
    assert card["source"]["world_size"] == 2


@pytest.mark.parametrize("backend", [DistributedType.FSDP, DistributedType.DEEPSPEED])
def test_gpu_runtime_refuses_sharded_backends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: DistributedType
) -> None:
    config = write_mix(tmp_path / "mix", ["2 3 4 5 6 7 8 9"] * 4)
    mix = ProvisionalMix(config, word_tokenizer(), window=3, rows_per_update=2)
    fake = SimpleNamespace(device=torch.device("cpu"), distributed_type=backend)
    monkeypatch.setattr(runtime, "Accelerator", lambda **_: fake)
    with pytest.raises(ValueError, match="ordinary DDP"):
        GpuExperiment(
            sdpa_config(),
            mix,
            peak_lr=1e-3,
            seed=17,
            total_targets=24,
            warmup_targets=6,
            micro_rows=1,
            device="cpu",
        )


def distributed_launcher_worker(rank: int, port: int, root: str, resume: bool) -> None:
    os.environ.update(
        RANK=str(rank),
        LOCAL_RANK=str(rank),
        WORLD_SIZE="2",
        LOCAL_WORLD_SIZE="2",
        MASTER_ADDR="127.0.0.1",
        MASTER_PORT=str(port),
        ACCELERATE_USE_CPU="1",
    )
    torch.set_num_threads(1)
    AcceleratorState._reset_state(True)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(train.AutoTokenizer, "from_pretrained", lambda *_, **__: word_tokenizer())
        patch.setattr(train, "proxy_config", lambda _: sdpa_config(context=3))
        patch.setattr(train, "GpuExperiment", partial(GpuExperiment, device="cpu"))
        patch.setattr(
            sys,
            "argv",
            [
                "train",
                "--size=76m",
                f"--dataset-uri={root}/mix",
                "--dataset-version=1",
                "--warmup-targets=12",
                "--rows-per-update=4",
                "--micro-rows=1",
                f"--output-dir={root}/run",
                "--checkpoint-every=2",
                "--workers=0",
                "--max-updates=3" if resume else "--max-updates=2",
                *(["--resume"] if resume else []),
            ],
        )
        if resume:
            original_resume = GpuExperiment.resume
            original_record = train.record_attempt

            def mark_loaded(experiment: GpuExperiment, checkpoint: Path) -> None:
                original_resume(experiment, checkpoint)
                if rank == 1:
                    deadline = time.monotonic() + 20
                    while not (Path(root) / "rank0-loaded").exists():
                        if time.monotonic() > deadline:
                            raise TimeoutError("rank zero did not finish loading")
                        time.sleep(0.01)
                    time.sleep(0.1)
                (Path(root) / f"rank{rank}-loaded").touch()

            def record_after_all_load(*args: Any, **kwargs: Any) -> int:
                assert (Path(root) / "rank1-loaded").exists()
                return original_record(*args, **kwargs)

            patch.setattr(GpuExperiment, "resume", mark_loaded)
            patch.setattr(train, "record_attempt", record_after_all_load)
        train.main()
        if not resume:
            with pytest.raises(RuntimeError, match="FileExistsError"):
                train.main()
            dist.barrier()
    dist.destroy_process_group()
    AcceleratorState._reset_state(True)


def test_distributed_launcher_keeps_one_record_and_log_per_update(tmp_path: Path) -> None:
    write_mix(tmp_path / "mix", ["2 3 4 5 6 7 8 9"] * 10)
    for resume in (False, True):
        if resume:
            with (tmp_path / "run" / "log.jsonl").open("a") as log:
                log.write('{"attempt": 0, "upd')
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        mp.spawn(
            distributed_launcher_worker, args=(port, str(tmp_path), resume), nprocs=2, join=True
        )
    root = tmp_path / "run"
    assert sorted(path.name for path in root.glob("run-*.json")) == ["run-000.json", "run-001.json"]
    logs = [json.loads(line) for line in (root / "log.jsonl").read_text().splitlines()]
    assert [(line["attempt"], line["update"], line["targets"]) for line in logs] == [
        (0, 1, 12),
        (0, 2, 24),
        (1, 3, 36),
    ]
    summary = json.loads((root / "summary-000003.json").read_text())
    assert summary["world_size"] == 2
    assert summary["rank_peak_memory_bytes"] == [[0, 0], [0, 0]]
    assert summary["attempt"] == 1 and summary["segment"] == 2


def test_gpu_runtime_refuses_a_virtual_rank_device_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write_mix(tmp_path / "mix", ["2 3 4 5 6 7 8 9"] * 4)
    mix = ProvisionalMix(
        config, word_tokenizer(), window=3, rows_per_update=4, rank=1, world_size=2
    )
    fake = SimpleNamespace(
        device=torch.device("cuda:0"),
        distributed_type=DistributedType.MULTI_GPU,
        num_processes=2,
        process_index=1,
    )
    monkeypatch.setattr(runtime, "Accelerator", lambda **_: fake)
    with pytest.raises(ValueError, match="ordinary torchrun"):
        GpuExperiment(
            sdpa_config(),
            mix,
            peak_lr=1e-3,
            seed=17,
            total_targets=24,
            warmup_targets=6,
            micro_rows=1,
        )


@pytest.mark.parametrize("rank", [None, 0, 1])
def test_resume_restores_only_the_owned_cuda_rng(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rank: int | None
) -> None:
    # Saved with eight visible devices, resumed with just the devices the run uses.
    states = {
        "random_state": random.getstate(),
        "numpy_random_seed": np.random.get_state(),
        "torch_manual_seed": torch.get_rng_state(),
        "torch_cuda_manual_seed": [torch.tensor([index]) for index in range(8)],
    }
    experiment = object.__new__(Experiment)
    accelerator = SimpleNamespace(
        is_main_process=False,
        process_index=rank or 0,
        num_processes=1,
        device=torch.device("cuda" if rank is None else f"cuda:{rank}"),
        load_state=lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(experiment, "accelerator", accelerator, raising=False)
    monkeypatch.setattr(runtime, "gather_object", lambda values: values)
    monkeypatch.setattr(torch, "load", lambda *args, **kwargs: states)
    monkeypatch.setattr(torch.cuda, "current_device", lambda: 0)
    restored = []

    def restore_owned(state: torch.Tensor, device: torch.device) -> None:
        assert device.index == rank
        restored.append(state.tolist())

    monkeypatch.setattr(torch.cuda, "set_rng_state", restore_owned)
    experiment.resume(tmp_path)
    assert restored == [[rank or 0]]


def test_real_accelerate_load_recovers_from_unused_cuda_rng_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    AcceleratorState._reset_state(True)
    experiment = Experiment(
        tiny_config(),
        accelerator=Accelerator(cpu=True, mixed_precision="no"),
        source={},
        peak_lr=1e-3,
        betas=(0.9, 0.999),
        weight_decay=0.0,
        seed=3,
        total_targets=64,
        warmup_targets=16,
    )
    checkpoint = tmp_path / "checkpoint"
    experiment.checkpoint(checkpoint)
    rng_path = checkpoint / "random_states_0.pkl"
    states = torch.load(rng_path, weights_only=False)
    states["torch_cuda_manual_seed"] = [torch.tensor([index]) for index in range(8)]
    torch.save(states, rng_path)
    manifest_path = checkpoint / "COMMITTED.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][rng_path.name] = hashlib.sha256(rng_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    attempts = []

    def fail_on_unused_device(states: list[torch.Tensor]) -> None:
        attempts.append("accelerate")
        raise IndexError("saved unused CUDA device is no longer visible")

    def restore_owned(state: torch.Tensor, device: torch.device) -> None:
        assert state.tolist() == [0] and device == torch.device("cuda")
        attempts.append("owned")

    with monkeypatch.context() as patch:
        patch.setattr(experiment.accelerator.state, "device", torch.device("cuda"))
        patch.setattr(checkpointing, "is_cuda_available", lambda: True)
        patch.setattr(torch.cuda, "set_rng_state_all", fail_on_unused_device)
        patch.setattr(torch.cuda, "set_rng_state", restore_owned)
        patch.setattr(torch.cuda, "current_device", lambda: 0)
        experiment.resume(checkpoint)
    assert attempts == ["accelerate", "owned"]
    AcceleratorState._reset_state(True)
