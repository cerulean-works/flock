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
    LlamaConfig,
    LRSchedulerConfig,
    LRScheduleSegment,
    MuonAdamWConfig,
    Olmo2Config,
    TrainerConfig,
)
from flock.training.core.models import (
    LLAMA3_2_1B_CONFIG,
    LLAMA3_2_3B_CONFIG,
    OLMO2_1B_CONFIG,
    Llama,
    Olmo2,
)
from flock.training.core.models import BaseModel as TrainingModel
from flock.training.experiments.proxy.olmo2_proxy import PROXY_SIZES

from .wsd import TokenRounding, WSDSettings

type ArchitectureConfig = Olmo2Config | LlamaConfig

MODEL_PRESETS: dict[str, ArchitectureConfig] = {
    **{name: size.model for name, size in PROXY_SIZES.items()},
    "1b": OLMO2_1B_CONFIG,
    "llama3.2-1b": LLAMA3_2_1B_CONFIG,
    "llama3.2-3b": LLAMA3_2_3B_CONFIG,
    # Llama 3.2 proportions (head width 64, 4:1 GQA, 4x MLP) at 202M parameters, 98.5M
    # of them the tied embedding.
    "llama3.2-200m": replace(
        LLAMA3_2_1B_CONFIG,
        hidden_size=768,
        intermediate_size=3072,
        num_hidden_layers=12,
        num_attention_heads=12,
        num_key_value_heads=4,
    ),
    # Scaling-law pilot sizes on the same Llama 3.2 proportions; hidden width / layers stays near
    # 53-57. Parameter counts include the tied 128256-row embedding.
    "llama3.2-100m": replace(  # 99.9M
        LLAMA3_2_1B_CONFIG,
        hidden_size=512,
        intermediate_size=2048,
        num_hidden_layers=9,
        num_attention_heads=8,
        num_key_value_heads=2,
    ),
    "llama3.2-400m": replace(  # 405M
        LLAMA3_2_1B_CONFIG,
        hidden_size=1024,
        intermediate_size=4096,
        num_hidden_layers=18,
        num_attention_heads=16,
        num_key_value_heads=4,
    ),
    "llama3.2-750m": replace(  # 730M
        LLAMA3_2_1B_CONFIG,
        hidden_size=1280,
        intermediate_size=5120,
        num_hidden_layers=24,
        num_attention_heads=20,
        num_key_value_heads=4,
    ),
}
"""Named architectures a run can start from: OLMo 2 sizes and Llama 3.2 sizes."""

ARCHITECTURES: dict[str, type[ArchitectureConfig]] = {"olmo2": Olmo2Config, "llama": LlamaConfig}
"""Config class for each `ModelSettings.architecture`."""

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
    """Data the run trains on: a prepared token cache, or raw text tokenized during training.

    Set exactly one of `cache_dir` and `text_path`.

    Args:
        cache_dir (`str`, *optional*): `TokenChunkDataset` directory on storage every node can read.
        text_path (`str`, *optional*): Local raw-text Lance dataset, read in stored order and
            tokenized by the training process's DataLoader workers. Shuffle it when writing it.
        text_version (`int`, *optional*): Lance version of `text_path`; pin it so a resumed
            run reads the same rows.
        block_rows (`int`, *optional*, defaults to `2048`): Rows a worker tokenizes at once.
        add_bos (`bool`, *optional*, defaults to `False`): Start each streamed document with BOS.
        add_eos (`bool`, *optional*, defaults to `True`): End each streamed document with EOS.
        chunk_length (`int`, *optional*, defaults to `4096`): Token IDs per chunk.
        input_column (`str`, *optional*, defaults to `"text"`): Source text column.
        source (`DataSource`, *optional*): Dataset to tokenize when the cache is missing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    cache_dir: str | None = None
    text_path: str | None = None
    text_version: int | None = None
    block_rows: int = Field(default=2048, gt=0)
    add_bos: bool = False
    add_eos: bool = True
    chunk_length: int = Field(default=4096, ge=2)
    input_column: str = "text"
    source: DataSource | None = None

    @model_validator(mode="after")
    def validate_kind(self) -> Self:
        """Require exactly one data kind, and streaming settings only when streaming."""
        if (self.cache_dir is None) == (self.text_path is None):
            raise ValueError("Set exactly one of data.cache_dir and data.text_path")
        if self.text_path is not None and self.source is not None:
            raise ValueError("data.source prepares a cache; a text stream needs none")
        return self

    @property
    def streams(self) -> bool:
        """Return whether the run tokenizes `text_path` during training."""
        return self.text_path is not None


class ModelSettings(BaseModel):
    """Architecture: a preset with field overrides, or every field spelled out.

    Args:
        preset (`str`, *optional*): Key of `MODEL_PRESETS`, such as `"190m"` or
            `"llama3.2-1b"`. The preset decides the architecture.
        architecture (`str`, *optional*): `"olmo2"` or `"llama"`. Required without a
            preset, unless the run is OLMo 2; with a preset it must match it when set.
        attn_implementation (`str`, *optional*, defaults to `"flash_attention_4"`):
            Registered attention backend.
        overrides (`dict[str, Any]`, *optional*): `Olmo2Config` or `LlamaConfig` fields
            to change. Without a preset, these must hold every required field.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    preset: str | None = None
    architecture: Literal["olmo2", "llama"] | None = None
    attn_implementation: str = "flash_attention_4"
    overrides: dict[str, Any] = Field(default_factory=dict)

    def architecture_config(self, init_seed: int) -> ArchitectureConfig:
        """Return the architecture with the run's initialization seed."""
        fields: dict[str, Any] = {
            **self.overrides,
            "attn_implementation": self.attn_implementation,
        }
        fields["init_seed"] = init_seed
        if self.preset is None:
            return ARCHITECTURES[self.architecture or "olmo2"](**fields)
        if self.preset not in MODEL_PRESETS:
            raise ValueError(f"Unknown model preset {self.preset!r}; use {sorted(MODEL_PRESETS)}")
        preset = MODEL_PRESETS[self.preset]
        if self.architecture is not None and not isinstance(
            preset, ARCHITECTURES[self.architecture]
        ):
            raise ValueError(f"Preset {self.preset!r} is not a {self.architecture} architecture")
        return replace(preset, **fields)

    def build(self, init_seed: int) -> TrainingModel:
        """Construct a freshly initialized model for this architecture."""
        config = self.architecture_config(init_seed)
        return Llama(config) if isinstance(config, LlamaConfig) else Olmo2(config)


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


class SaveEvalSettings(BaseModel):
    """Flock perplexity suites to score, across the run's ranks, just before each save.

    Args:
        suites (`dict[str, str]`, *optional*): Flock suite name to its W&B label; defaults to
            Paloma as `paloma` and tw-text-v0 as `tw_text_v0`.
        batch_tokens (`int`, *optional*, defaults to `32768`): Scoring batch budget.
        limit (`int`, *optional*): Documents per task, for quick tests.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    suites: dict[str, str] = Field(
        default_factory=lambda: {"paloma": "paloma", "tw_text": "tw_text_v0"}
    )
    batch_tokens: int = Field(default=32768, gt=0)
    limit: int | None = Field(default=None, gt=0)


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


_PLACEHOLDER_SCHEDULE = (LRScheduleSegment(start=0, end=1.0, start_ratio=1.0, end_ratio=1.0),)


class ExperimentConfig(BaseModel):
    """One training run: model, data, optimizer, schedule and when to save and stop.

    Args:
        name (`str`): Run name, used for W&B and the run directory.
        output_dir (`str`): Checkpoint directory on shared storage.
        data (`DataSettings`): Token cache.
        model (`ModelSettings`): Architecture and attention backend.
        optimizer (`AdamWSettings` or `MuonAdamWSettings`): Chosen by its `type` field.
        schedule (`list[LRScheduleSegment]`, *optional*): Learning-rate segments. Fractions
            are fractions of the run up to `stop_tokens`. Set this or `wsd`.
        wsd (`WSDSettings`, *optional*): Warmup-stable-decay schedule with token boundaries,
            resolved to updates when training starts. Needs a text stream.
        trainer (`dict[str, Any]`): Other `TrainerConfig` fields, such as
            `batch_tokens`, `torch_compile` or `wandb_project`. Name `model_dtype`
            as a string, such as `"bfloat16"`.
        stop_tokens (`int`): Train until this many target tokens, save, then stop.
        save_tokens (`list[int]`, *optional*): Also save once each of these counts is reached.
        seed (`int`, *optional*, defaults to `0`): Initialization and data-order seed.
        wandb_group (`str`, *optional*): W&B group, usually the sweep name.
        branch_from (`str`, *optional*): Checkpoint of another run to start this one from,
            continuing its weights, optimizer, counters and data position under this run's
            schedule. Used only on the first start; restarts resume from this run's own
            checkpoints. Data, batching, seed, GPUs and peak rates must match the parent.
        token_rounding (`str`, *optional*, defaults to `"first_reaching"`): Save and schedule
            token counts land on the first update reaching them, or the `"nearest"` update.
        one_pass_tokens (`int`, *optional*): Targets one pass over the data can train on;
            budgets beyond it are refused. Also the denominator of `train/epoch_pct`.
        nominal_params (`int`, *optional*): Parameter count for `train/tokens_per_param`.
        save_evals (`SaveEvalSettings`, *optional*): Score Flock suites before each save.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=r"^[A-Za-z0-9._-]+$")
    output_dir: str
    data: DataSettings
    model: ModelSettings
    optimizer: Annotated[AdamWSettings | MuonAdamWSettings, Field(discriminator="type")]
    schedule: list[LRScheduleSegment] = Field(default_factory=list)
    wsd: WSDSettings | None = None
    trainer: dict[str, Any] = Field(default_factory=dict)
    stop_tokens: TokenCount
    save_tokens: list[TokenCount] = Field(default_factory=list)
    seed: int = 0
    wandb_group: str | None = None
    branch_from: str | None = None
    token_rounding: TokenRounding = "first_reaching"
    one_pass_tokens: TokenCount | None = None
    nominal_params: TokenCount | None = None
    save_evals: SaveEvalSettings | None = None

    @model_validator(mode="after")
    def validate_run(self) -> Self:
        """Build every derived config now, so a typo fails before any job starts."""
        managed = _MANAGED_TRAINER_FIELDS & self.trainer.keys()
        if managed:
            raise ValueError(f"Set {sorted(managed)} outside the trainer table")
        if any(tokens > self.stop_tokens for tokens in self.save_tokens):
            raise ValueError("save_tokens must not exceed stop_tokens")
        if bool(self.schedule) == (self.wsd is not None):
            raise ValueError("Set exactly one of schedule and wsd")
        if self.wsd is not None and not self.data.streams:
            raise ValueError("A wsd schedule needs data.text_path")
        if self.one_pass_tokens is not None and self.stop_tokens > self.one_pass_tokens:
            raise ValueError(
                f"stop_tokens {self.stop_tokens} exceeds one pass ({self.one_pass_tokens})"
            )
        self.trainer_config()
        self.model.architecture_config(self.seed)
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
                # A WSD run's schedule depends on its world size; the trainer resolves it.
                "lr_scheduler_config": LRSchedulerConfig(
                    schedule=tuple(self.schedule) or _PLACEHOLDER_SCHEDULE
                ),
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
    return asdict(experiment.model.architecture_config(experiment.seed))
