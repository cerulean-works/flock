"""Describe one scaling-law run, and expand a sweep file into many of them."""

import copy
import itertools
import json
import re
from dataclasses import asdict, replace
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import torch
import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from flock.training import (
    AdamWConfig,
    LRSchedulerConfig,
    LRScheduleSegment,
    MuonAdamWConfig,
    Olmo2Config,
    TrainerConfig,
)
from flock.training.core.models import OLMO2_1B_CONFIG
from flock.training.experiments.proxy.olmo2_proxy import PROXY_SIZES

MODEL_PRESETS: dict[str, Olmo2Config] = {
    **{name: size.model for name, size in PROXY_SIZES.items()},
    "1b": OLMO2_1B_CONFIG,
}
"""Named OLMo 2 architectures a run can start from."""

_SUFFIXES = {"k": 10**3, "m": 10**6, "b": 10**9, "g": 10**9, "t": 10**12}


def parse_token_count(value: Any) -> Any:
    """Read token counts written as `3800000000`, `3.8e9`, `"3.8e9"`, `"3.8B"` or `"500M"`.

    YAML reads `3.8e9` as a string, so strings are parsed too. The result must be
    a whole number; other values pass through for Pydantic to reject.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().replace("_", "").lower()
        scale = 1
        if text and text[-1] in _SUFFIXES:
            text, scale = text[:-1], _SUFFIXES[text[-1]]
        try:
            value = float(text) * scale
        except ValueError:
            return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"Token count {value} is not a whole number")
        return int(value)
    return value


type TokenCount = Annotated[int, BeforeValidator(parse_token_count), Field(gt=0)]


class DataSource(BaseModel):
    """Pinned Lance dataset that `sweep prepare` tokenizes into the cache.

    Args:
        uri (`str`): Local path or Lance object-storage URI.
        version (`int` or `str`): Lance version number or tag.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    uri: str
    version: int | str


class DataSettings(BaseModel):
    """Token cache the run trains on.

    Args:
        cache_dir (`str`): `TokenChunkDataset` directory on storage every node can read.
        chunk_length (`int`, *optional*, defaults to `4096`): Token IDs per chunk.
        input_column (`str`, *optional*, defaults to `"text"`): Source text column.
        source (`DataSource`, *optional*): Dataset to tokenize when the cache is missing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    cache_dir: str
    chunk_length: int = Field(default=4096, ge=2)
    input_column: str = "text"
    source: DataSource | None = None


class ModelSettings(BaseModel):
    """OLMo 2 architecture: a preset with field overrides, or every field spelled out.

    Args:
        preset (`str`, *optional*): Key of `MODEL_PRESETS`, such as `"190m"`.
        attn_implementation (`str`, *optional*, defaults to `"flash_attention_4"`):
            Registered attention backend.
        overrides (`dict[str, Any]`, *optional*): `Olmo2Config` fields to change.
            Without a preset, these must hold every required field.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    preset: str | None = None
    attn_implementation: str = "flash_attention_4"
    overrides: dict[str, Any] = Field(default_factory=dict)

    def olmo2_config(self, init_seed: int) -> Olmo2Config:
        """Return the architecture with the run's initialization seed."""
        fields: dict[str, Any] = {
            **self.overrides,
            "attn_implementation": self.attn_implementation,
        }
        fields["init_seed"] = init_seed
        if self.preset is None:
            return Olmo2Config(**fields)
        if self.preset not in MODEL_PRESETS:
            raise ValueError(f"Unknown model preset {self.preset!r}; use {sorted(MODEL_PRESETS)}")
        return replace(MODEL_PRESETS[self.preset], **fields)


class AdamWSettings(BaseModel):
    """AdamW on every parameter. Fields match `AdamWConfig`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["adamw"] = "adamw"
    lr: float = Field(gt=0)
    betas: tuple[float, float] = (0.9, 0.95)

    def build(self) -> AdamWConfig:
        """Return the trainer's optimizer configuration."""
        return AdamWConfig(lr=self.lr, betas=self.betas)


class MuonAdamWSettings(BaseModel):
    """Muon on hidden matrices and AdamW elsewhere. Fields match `MuonAdamWConfig`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["muon_adamw"] = "muon_adamw"
    lr: float = Field(gt=0)
    betas: tuple[float, float] = (0.9, 0.95)
    weight_decay: float = Field(default=0.0, ge=0)
    eps: float = Field(default=1e-8, gt=0)
    muon_lr: float = Field(gt=0)
    muon_momentum: float = Field(default=0.95, ge=0)
    muon_weight_decay: float = Field(default=0.1, ge=0)
    muon_nesterov: bool = True
    muon_ns_steps: int = Field(default=5, gt=0, lt=100)
    muon_adjust_lr_fn: Literal["original", "match_rms_adamw"] | None = None

    def build(self) -> MuonAdamWConfig:
        """Return the trainer's optimizer configuration."""
        return MuonAdamWConfig(**self.model_dump(exclude={"type"}))


# Fields the experiment sets itself, so the passthrough `trainer` table cannot.
_MANAGED_TRAINER_FIELDS = {
    "run_name",
    "output_dir",
    "optimizer_config",
    "lr_scheduler_config",
    "seed",
    "max_steps",
    "input_column",
}


class ExperimentConfig(BaseModel):
    """One training run: model, data, optimizer, schedule and when to save and stop.

    Args:
        name (`str`): Run name, used for W&B and the run directory.
        output_dir (`str`): Checkpoint directory on shared storage.
        data (`DataSettings`): Token cache.
        model (`ModelSettings`): Architecture and attention backend.
        optimizer (`AdamWSettings` or `MuonAdamWSettings`): Chosen by its `type` field.
        schedule (`list[LRScheduleSegment]`): Learning-rate segments. Fractions are
            fractions of the run up to `stop_tokens`.
        trainer (`dict[str, Any]`): Other `TrainerConfig` fields, such as
            `batch_tokens`, `torch_compile` or `wandb_project`. Name `model_dtype`
            as a string, such as `"bfloat16"`.
        stop_tokens (`int`): Train until this many target tokens, save, then stop.
        save_tokens (`list[int]`, *optional*): Also save once each of these counts is reached.
        seed (`int`, *optional*, defaults to `0`): Initialization and data-order seed.
        wandb_group (`str`, *optional*): W&B group, usually the sweep name.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=r"^[A-Za-z0-9._-]+$")
    output_dir: str
    data: DataSettings
    model: ModelSettings
    optimizer: Annotated[AdamWSettings | MuonAdamWSettings, Field(discriminator="type")]
    schedule: list[LRScheduleSegment] = Field(min_length=1)
    trainer: dict[str, Any] = Field(default_factory=dict)
    stop_tokens: TokenCount
    save_tokens: list[TokenCount] = Field(default_factory=list)
    seed: int = 0
    wandb_group: str | None = None

    @model_validator(mode="after")
    def validate_run(self) -> Self:
        """Build every derived config now, so a typo fails before any job starts."""
        managed = _MANAGED_TRAINER_FIELDS & self.trainer.keys()
        if managed:
            raise ValueError(f"Set {sorted(managed)} outside the trainer table")
        if any(tokens > self.stop_tokens for tokens in self.save_tokens):
            raise ValueError("save_tokens must not exceed stop_tokens")
        self.trainer_config()
        self.model.olmo2_config(self.seed)
        return self

    def trainer_config(self, max_steps: int | None = None) -> TrainerConfig:
        """Return the trainer configuration, capped at `max_steps` updates when given."""
        trainer = dict(self.trainer)
        if isinstance(trainer.get("model_dtype"), str):
            # Files name the dtype, such as "bfloat16"; the trainer wants the torch object.
            dtype = getattr(torch, trainer["model_dtype"], None)
            if not isinstance(dtype, torch.dtype):
                # Pydantic reports ValueError as a validation error with the run's index.
                raise ValueError(f"Unknown model_dtype {trainer['model_dtype']!r}")  # noqa: TRY004
            trainer["model_dtype"] = dtype
        return TrainerConfig.model_validate(
            {
                "wandb_project": "scaling-law",
                **trainer,
                "run_name": self.name,
                "input_column": self.data.input_column,
                "output_dir": self.output_dir,
                "optimizer_config": self.optimizer.build(),
                "lr_scheduler_config": LRSchedulerConfig(schedule=tuple(self.schedule)),
                "seed": self.seed,
                "max_steps": max_steps,
            }
        )

    def token_targets(self) -> list[int]:
        """Return every token count to save at, ascending and ending with `stop_tokens`."""
        return sorted({*self.save_tokens, self.stop_tokens})

    @classmethod
    def load(cls, path: str | Path) -> Self:
        """Read a run from a JSON or YAML file."""
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))


def set_dotted(target: dict[str, Any], key: str, value: Any) -> None:
    """Set `target["a"]["b"] = value` for `key="a.b"`, creating tables on the way."""
    *parents, last = key.split(".")
    for part in parents:
        child = target.setdefault(part, {})
        if not isinstance(child, dict):
            raise TypeError(f"Cannot set {key!r}: {part!r} is not a table")
        target = child
    target[last] = copy.deepcopy(value)


def get_dotted(source: dict[str, Any], key: str) -> Any:
    """Return `source["a"]["b"]` for `key="a.b"`."""
    for part in key.split("."):
        if not isinstance(source, dict) or part not in source:
            raise KeyError(key)
        source = source[part]
    return source


class SweepSettings(BaseModel):
    """How a sweep expands and where its runs live.

    Args:
        name (`str`): Sweep name; also the W&B group.
        output_root (`str`): Shared directory; runs live in `<output_root>/<name>/runs/`.
        gpus_per_run (`int`): GPUs each run trains on.
        name_template (`str`, *optional*): Run name with `{dotted.key}` fields,
            such as `"{model.preset}-lr{optimizer.lr}"`. Defaults to `<name>-<index>`.
        grid (`dict[str, list]`, *optional*): Every combination of these values.
        zip (`dict[str, list]`, *optional*): Equal-length lists varied together.
        runs (`list[dict]`, *optional*): Explicit overrides, one entry per variant.
        master_port (`int`, *optional*, defaults to `29500`): First rendezvous port.
        srun_args (`list[str]`, *optional*): Extra `srun` arguments for every run.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=r"^[A-Za-z0-9._-]+$")
    output_root: str
    gpus_per_run: int = Field(gt=0)
    name_template: str | None = None
    grid: dict[str, list[Any]] = Field(default_factory=dict)
    zip: dict[str, list[Any]] = Field(default_factory=dict)
    runs: list[dict[str, Any]] = Field(default_factory=list)
    master_port: int = Field(default=29500, gt=0, lt=65536)
    srun_args: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_axes(self) -> Self:
        """Require nonempty grid axes and equal-length zip lists."""
        if any(not values for values in self.grid.values()):
            raise ValueError("Every grid axis needs at least one value")
        if len({len(values) for values in self.zip.values()}) > 1:
            raise ValueError("Every zip list must have the same length")
        return self

    @property
    def directory(self) -> Path:
        """Return the sweep's directory."""
        return Path(self.output_root) / self.name

    def variants(self) -> list[dict[str, Any]]:
        """Return dotted-key overrides for each run: runs × zip rows × grid combinations."""
        explicit = self.runs or [{}]
        rows = (
            [dict(zip(self.zip, values, strict=True)) for values in zip(*self.zip.values())]
            if self.zip
            else [{}]
        )
        grid = [
            dict(zip(self.grid, values, strict=True))
            for values in itertools.product(*self.grid.values())
        ]
        return [
            {**run, **row, **combination}
            for run, row, combination in itertools.product(explicit, rows, grid)
        ]


class Sweep(BaseModel):
    """A sweep file: shared run settings plus the axes that vary between runs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sweep: SweepSettings
    base: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path) -> Self:
        """Read a sweep from a YAML or JSON file."""
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))

    def resolve(self) -> list[ExperimentConfig]:
        """Expand and validate every run, in a stable order.

        Raises:
            `ValueError`: If a run is invalid or two runs share a name.
        """
        experiments = []
        for index, overrides in enumerate(self.sweep.variants()):
            fields = copy.deepcopy(self.base)
            for key, value in overrides.items():
                set_dotted(fields, key, value)
            name = self._name(index, fields)
            fields["name"] = name
            fields["output_dir"] = str(self.sweep.directory / "runs" / name / "checkpoints")
            fields.setdefault("wandb_group", self.sweep.name)
            try:
                experiments.append(ExperimentConfig.model_validate(fields))
            except ValueError as error:
                raise ValueError(f"Run {index} ({name}) is invalid: {error}") from error
        names = [experiment.name for experiment in experiments]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"Runs share names {duplicates}; add fields to name_template")
        return experiments

    def _name(self, index: int, fields: dict[str, Any]) -> str:
        template = self.sweep.name_template
        if template is None:
            return f"{self.sweep.name}-{index:03d}"

        def field(match: re.Match[str]) -> str:
            value = get_dotted(fields, match.group(1))
            return value if isinstance(value, str) else json.dumps(value)

        # Keep names safe for paths and W&B.
        return re.sub(r"[^A-Za-z0-9._-]+", "_", re.sub(r"\{([^{}]+)\}", field, template))


def experiment_json(experiment: ExperimentConfig) -> str:
    """Serialize a run for `train.py`, keeping integers exact."""
    return json.dumps(experiment.model_dump(mode="json"), indent=2, sort_keys=True)


def model_summary(experiment: ExperimentConfig) -> dict[str, Any]:
    """Return the resolved architecture as plain JSON, for plans and records."""
    return asdict(experiment.model.olmo2_config(experiment.seed))
