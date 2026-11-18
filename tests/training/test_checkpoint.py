from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import torch
from safetensors.torch import load_file
from torch import nn

from flock.training.core import checkpoint as checkpoint_module
from flock.training.core.checkpoint import CheckpointManager


def test_saved_weights_buffers_and_nested_state_survive_later_updates(tmp_path: Path) -> None:
    model = nn.Sequential(nn.Linear(2, 2), nn.BatchNorm1d(2))
    optimizer = torch.optim.AdamW(model.parameters())
    model(torch.ones(2, 2)).sum().backward()
    optimizer.step()
    model.zero_grad()
    progress_step = torch.tensor(7)
    progress_metadata = {"step": 7}
    state: dict[str, Any] = {
        "optimizer": optimizer.state_dict(),
        "progress": [progress_step, progress_metadata],
    }
    expected_weights = copy.deepcopy(model.state_dict())
    expected_state = copy.deepcopy(state)
    manager = CheckpointManager(tmp_path, run="cerulean/pretrain/run1")
    manager.save_model(7, model, training_state=state)
    assert (tmp_path / "checkpoint-7" / "model.safetensors").is_file()
    with torch.no_grad():
        for tensor in model.state_dict().values():
            tensor.add_(100)
        for values in optimizer.state.values():
            for tensor in values.values():
                if isinstance(tensor, torch.Tensor):
                    tensor.add_(100)
    progress_step.add_(100)
    progress_metadata["step"] = 100

    restored = nn.Sequential(nn.Linear(2, 2), nn.BatchNorm1d(2))
    loaded = manager.load_checkpoint(tmp_path / "checkpoint-7", restored)
    for key, tensor in restored.state_dict().items():
        torch.testing.assert_close(tensor, expected_weights[key], rtol=0, atol=0)
    for index, values in expected_state["optimizer"]["state"].items():
        for key, tensor in values.items():
            torch.testing.assert_close(loaded["optimizer"]["state"][index][key], tensor)
    assert loaded["progress"][0].item() == loaded["progress"][1]["step"] == 7
    manager.finish_checkpoint_uploads()


def test_saved_checkpoint_preserves_tied_weights(tmp_path: Path) -> None:
    input_layer = nn.Linear(2, 2)
    output_layer = nn.Linear(2, 2)
    model = nn.ModuleDict({"input": input_layer, "output": output_layer})
    output_layer.weight = input_layer.weight
    expected = input_layer.weight.detach().clone()
    manager = CheckpointManager(tmp_path)
    manager.save_model(1, model)
    with torch.no_grad():
        input_layer.weight.zero_()
    manager.finish_checkpoint_uploads()
    weights = load_file(tmp_path / "checkpoint-1" / "model.safetensors")
    assert len({"input.weight", "output.weight"} & weights.keys()) == 1
    assert manager.load_checkpoint(tmp_path / "checkpoint-1", model) == {}
    assert input_layer.weight is output_layer.weight
    torch.testing.assert_close(input_layer.weight, expected)


def test_local_retention_runs_after_each_save(tmp_path: Path) -> None:
    manager = CheckpointManager(tmp_path, save_total_limit=1)
    model = nn.Linear(2, 2)
    manager.save_model(1, model)
    manager.save_model(2, model)
    assert manager.checkpoints() == [tmp_path / "checkpoint-2"]


def test_write_error_is_immediate_and_preserves_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = CheckpointManager(tmp_path, overwrite_output_dir=True, save_total_limit=1)
    model = nn.Linear(2, 2)
    manager.save_model(1, model, blocking=True)
    original = (tmp_path / "checkpoint-1" / "model.safetensors").read_bytes()

    def fail_write(model: Any, folder: Path, **kwargs: Any) -> None:
        folder.mkdir()
        (folder / "model.safetensors").write_bytes(b"incomplete")
        raise OSError("disk full")

    monkeypatch.setattr(checkpoint_module, "write_model", fail_write)
    with pytest.raises(OSError, match="disk full"):
        manager.save_model(1, model)
    assert (tmp_path / "checkpoint-1" / "model.safetensors").read_bytes() == original
    assert not list(tmp_path.glob(".checkpoint-*"))
    manager.finish_checkpoint_uploads()


def test_state_validation_precedes_weight_restore(tmp_path: Path) -> None:
    model = nn.Linear(2, 2)
    manager = CheckpointManager(tmp_path)
    manager.save_model(1, model, training_state={"step": 1}, blocking=True)
    with torch.no_grad():
        model.weight.zero_()
    before = model.weight.detach().clone()

    def reject(state: dict[str, Any]) -> None:
        assert state == {"step": 1}
        raise ValueError("incompatible run")

    with pytest.raises(ValueError, match="incompatible run"):
        manager.load_checkpoint(tmp_path / "checkpoint-1", model, validate_state=reject)
    torch.testing.assert_close(model.weight, before)


@pytest.mark.parametrize("limit", [0, -1])
def test_standalone_manager_rejects_nonpositive_retention(tmp_path: Path, limit: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        CheckpointManager(tmp_path, save_total_limit=limit)
