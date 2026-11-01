"""A model as a version's files (D99, D104): safetensors shards, `config.json` and `flock.json`
written from a `BaseModel`, and the model built again from them. torch and safetensors are
imported here only, so the rest of `flock.hub.models` stays light."""

import dataclasses
import importlib
import json
import os
import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from flock.hub.models._common import ModelError

if TYPE_CHECKING:
    import torch

    from flock.training.core.models.base import BaseModel

FORMAT = 1
SINGLE = "model.safetensors"
INDEX = "model.safetensors.index.json"
ALLOWED = "flock.training"
# How a nested state dict (an optimizer's) is kept in a safetensors file's metadata.
STRUCTURE = "flock_structure"


def _torch() -> Any:
    try:
        import safetensors.torch  # noqa: F401
        import torch
    except ImportError:
        raise ImportError("torch and safetensors aren't installed: pip install flock-hub[models]")
    return torch


def flock_commit() -> str:
    """The flock commit running, as eval's provenance finds it: `FLOCK_COMMIT`, else git's
    HEAD when this is a flock checkout (`-dirty` with uncommitted changes), else
    `"unknown"`."""
    if commit := os.environ.get("FLOCK_COMMIT"):
        return commit
    package = Path(__file__).resolve().parent

    def git(*args: str) -> str:
        done = subprocess.run(
            ["git", *args], cwd=package, capture_output=True, text=True, check=True
        )
        return done.stdout.strip()

    try:
        root, head = git("rev-parse", "--show-toplevel", "HEAD").splitlines()
        if package != Path(root) / "flock" / "hub" / "models":
            return "unknown"  # an installed copy inside some other repository
        changed = git("status", "--porcelain", "--untracked-files=no")
    except OSError, subprocess.CalledProcessError, ValueError:
        return "unknown"
    return f"{head}-dirty" if changed else head


def import_path(cls: type) -> str:
    return f"{cls.__module__}:{cls.__qualname__}"


def check_config(model: BaseModel) -> Any:
    config = getattr(model, "config", None)
    params = getattr(type(config), "__dataclass_params__", None)
    if config is None or params is None or not params.frozen:
        raise TypeError(
            f"{type(model).__name__}.config must be a frozen dataclass, so the model can be "
            "built again from config.json"
        )
    return config


# ---------------------------------------------------------------------- tensors


def _unshared(tensors: dict[str, torch.Tensor]) -> dict[str, torch.Tensor]:
    """`tensors` without the names that alias another's exact tensor (tied weights, kept
    once as safetensors' `save_model` keeps them), and with partial overlaps copied, since a
    safetensors file can't hold tensors that share memory."""
    seen: dict[tuple[Any, ...], str] = {}
    storages: set[tuple[Any, ...]] = set()
    kept: dict[str, torch.Tensor] = {}
    for name, tensor in tensors.items():
        tensor = tensor.detach()
        if tensor.device.type == "meta":
            raise ValueError(f"{name} is on the meta device and has no values to save")
        storage = (tensor.device, tensor.untyped_storage().data_ptr())
        view = (*storage, tensor.storage_offset(), tuple(tensor.shape), tensor.stride())
        view += (tensor.dtype,)
        if view in seen:
            continue
        seen[view] = name
        if storage in storages and tensor.untyped_storage().nbytes():
            tensor = tensor.clone()
        storages.add(storage)
        kept[name] = tensor.contiguous()
    return kept


def _nbytes(tensor: torch.Tensor) -> int:
    return tensor.numel() * tensor.element_size()


def _shards(tensors: dict[str, torch.Tensor], shard_size: int) -> list[dict[str, torch.Tensor]]:
    """`tensors` in order, split into groups of at most `shard_size` bytes (a larger tensor
    alone in its own)."""
    shards: list[dict[str, torch.Tensor]] = [{}]
    used = 0
    for name, tensor in tensors.items():
        size = _nbytes(tensor)
        if shards[-1] and used + size > shard_size:
            shards.append({})
            used = 0
        shards[-1][name] = tensor
        used += size
    return shards


def _save(tensors: dict[str, torch.Tensor], path: Path, metadata: dict[str, str]) -> None:
    from safetensors.torch import save_file

    path.parent.mkdir(parents=True, exist_ok=True)
    save_file(tensors, str(path), metadata={"format": "pt", **metadata})


def write_weights(model: BaseModel, folder: Path, shard_size: int) -> list[str]:
    """Writes `model`'s state dict to `folder` as `model.safetensors`, or as shards with
    Hugging Face's index when it's larger than `shard_size`. Returns the files' names."""
    tensors = _unshared(model.state_dict())
    shards = _shards(tensors, shard_size)
    if len(shards) == 1:
        _save(shards[0], folder / SINGLE, {})
        return [SINGLE]
    names = [f"model-{i:05d}-of-{len(shards):05d}.safetensors" for i in range(1, len(shards) + 1)]
    weight_map = {}
    for name, shard in zip(names, shards, strict=True):
        _save(shard, folder / name, {})
        weight_map.update(dict.fromkeys(shard, name))
    index = {
        "metadata": {"total_size": sum(map(_nbytes, tensors.values()))},
        "weight_map": weight_map,
    }
    (folder / INDEX).write_text(json.dumps(index, indent=2) + "\n")
    return [*names, INDEX]


def _flatten(value: Any, path: str, tensors: dict[str, Any]) -> Any:
    """`value`'s structure as JSON, with each tensor replaced by `{"tensor": key}` and put in
    `tensors` under its dotted path."""
    torch = _torch()
    if isinstance(value, torch.Tensor):
        key = path or "tensor"
        while key in tensors:
            key += "_"
        tensors[key] = value
        return {"tensor": key}
    if isinstance(value, Mapping):
        items = [
            [k, _flatten(v, f"{path}.{k}" if path else str(k), tensors)] for k, v in value.items()
        ]
        return {"dict": items}
    if isinstance(value, (list, tuple)):
        flat = [
            _flatten(v, f"{path}.{i}" if path else str(i), tensors) for i, v in enumerate(value)
        ]
        return {"tuple" if isinstance(value, tuple) else "list": flat}
    if value is None or isinstance(value, (bool, int, float, str)):
        return {"value": value}
    raise TypeError(
        f"training_state holds {type(value).__name__} at {path}: only tensors, "
        "mappings, lists and plain values can be saved"
    )


def _unflatten(node: dict[str, Any], tensors: Mapping[str, Any]) -> Any:
    ((kind, value),) = node.items()
    if kind == "tensor":
        return tensors[value]
    if kind == "dict":
        return {k: _unflatten(v, tensors) for k, v in value}
    if kind in ("list", "tuple"):
        items = [_unflatten(v, tensors) for v in value]
        return tuple(items) if kind == "tuple" else items
    return value


def write_training_state(state: Mapping[str, Any], folder: Path) -> list[str]:
    """Writes each of `state`'s values to `training_state/{name}.safetensors`. A flat state
    dict is saved as it is; anything nested (an optimizer's state dict) keeps its structure
    in the file's metadata, for [`~hub.models.load_training_state`]."""
    torch = _torch()
    written = []
    for name, value in state.items():
        if not name or "/" in name or name.startswith("."):
            raise ValueError(f"{name!r} can't name a training_state file")
        flat = isinstance(value, Mapping) and all(
            isinstance(k, str) and isinstance(v, torch.Tensor) for k, v in value.items()
        )
        tensors: dict[str, Any] = {}
        metadata = {}
        if flat:
            tensors = dict(value)
        else:
            metadata[STRUCTURE] = json.dumps(_flatten(value, "", tensors))
        path = f"training_state/{name}.safetensors"
        _save(_unshared(tensors), folder / path, metadata)
        written.append(path)
    return written


def read_training_state(path: Path, device: str | None = None) -> Any:
    """What [`write_training_state`] wrote to `path`."""
    from safetensors import safe_open
    from safetensors.torch import load_file

    with safe_open(str(path), framework="pt") as file:
        structure = (file.metadata() or {}).get(STRUCTURE)
    tensors = load_file(str(path), device=device or "cpu")
    return tensors if structure is None else _unflatten(json.loads(structure), tensors)


def dtype_name(model: BaseModel) -> str | None:
    floating = [p.dtype for p in model.parameters() if p.dtype.is_floating_point]
    return str(floating[0]).removeprefix("torch.") if floating else None


def write_model(
    model: BaseModel,
    folder: Path,
    *,
    shard_size: int,
    run: str | None,
    step: int | None,
    training_state: Mapping[str, Any] | None,
) -> None:
    """Writes `model` to `folder` as a version's files (D99)."""
    _torch()
    config = check_config(model)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "config.json").write_text(json.dumps(dataclasses.asdict(config), indent=2) + "\n")
    described = {
        "format": FORMAT,
        "architecture": import_path(type(model)),
        "config_class": import_path(type(config)),
        "dtype": dtype_name(model),
        "flock_commit": flock_commit(),
        "run": run,
        "step": step,
    }
    (folder / "flock.json").write_text(json.dumps(described, indent=2) + "\n")
    write_weights(model, folder, shard_size)
    if training_state:
        write_training_state(training_state, folder)


# ---------------------------------------------------------------------- loading


def _flock_class(path: Any, what: str) -> type:
    """The class `module:qualname` names, refusing anything outside `flock.training` so a
    download never picks the code that runs."""
    if not isinstance(path, str) or ":" not in path:
        raise ModelError(f"flock.json's {what} is {path!r}, not module:Class")
    module, _, qualname = path.partition(":")
    if module != ALLOWED and not module.startswith(ALLOWED + "."):
        raise ModelError(f"flock.json's {what} is {path}: only classes in {ALLOWED} are loaded")
    found: Any = importlib.import_module(module)
    for part in qualname.split("."):
        found = getattr(found, part, None)
    if not isinstance(found, type):
        raise ModelError(f"flock.json's {what} is {path}, which isn't a class")
    return found


def weight_files(folder: Path) -> list[Path]:
    if (folder / INDEX).exists():
        index = json.loads((folder / INDEX).read_text())
        return [folder / name for name in dict.fromkeys(index["weight_map"].values())]
    return [folder / SINGLE]


def _aliases(model: Any) -> dict[str, str]:
    """Each state-dict name that is the same tensor as an earlier one (tied weights), mapped to
    that one."""
    first: dict[int, str] = {}
    aliases = {}
    named = [
        *model.named_parameters(remove_duplicate=False),
        *model.named_buffers(remove_duplicate=False),
    ]
    for name, tensor in named:
        if id(tensor) in first:
            aliases[name] = first[id(tensor)]
        else:
            first[id(tensor)] = name
    return aliases


def build(folder: Path, device: Any, dtype: Any) -> BaseModel:
    """The model in `folder`, built on the meta device and given its weights."""
    torch = _torch()
    from safetensors.torch import load_file

    from flock.training.core.models.base import BaseModel

    described = json.loads((folder / "flock.json").read_text())
    if described.get("format") != FORMAT:
        raise ModelError(f"flock.json has format {described.get('format')!r}, not {FORMAT}")
    architecture: Any = _flock_class(described.get("architecture"), "architecture")
    config_class = _flock_class(described.get("config_class"), "config_class")
    if not issubclass(architecture, BaseModel):
        raise ModelError(f"{described['architecture']} isn't a flock.training BaseModel")
    config = config_class(**json.loads((folder / "config.json").read_text()))
    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
    device = torch.device(device)
    with torch.device("meta"):
        model = architecture(config)
    state: dict[str, Any] = {}
    for path in weight_files(folder):
        state.update(load_file(str(path), device=str(device)))
    for alias, name in _aliases(model).items():
        if alias not in state and name in state:
            state[alias] = state[name]
    if dtype is not None:
        state = {k: v.to(dtype) if v.is_floating_point() else v for k, v in state.items()}
    model.load_state_dict(state, assign=True, strict=True)
    return model.to(device)


def walk(folder: Path) -> Iterator[str]:
    """The files under `folder`, as relative POSIX paths in sorted order, skipping hidden files
    and folders."""
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder)
        if any(part.startswith(".") for part in relative.parts) or not path.is_file():
            continue
        yield relative.as_posix()
