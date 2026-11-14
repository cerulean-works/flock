# Training API

`flock.training` exposes the trainer and its configuration classes. Start with
[Train with Flock](trainer) for setup and a complete local example.

## Trainer

[[autodoc]] training.Trainer
    - train
    - evaluate
    - save_model
    - load_checkpoint
    - data_config
    - get_trainer_state
    - load_trainer_state

## Configuration

All `TrainerConfig` fields, including their defaults, are documented below.
Validation rejects unknown fields, nonpositive batch sizes and intervals,
negative worker counts, nonfinite or negative clipping thresholds, and unknown
evaluation task names. With zero workers, set `dataloader_prefetch_factor=None`.

| Field | Default | Meaning |
|---|---|---|
| `run_name` | Required | Default checkpoint folder name and evaluation label. Does not set the W&B run name. |
| `input_column` | Required | Text column in each dataset row. |
| `batch_size` | Required | Positive rows per optimizer update. |
| `optimizer_config` | Required | `AdamWConfig`; Muon is not implemented. |
| `lr_scheduler_config` | Required | `CosineConfig` or `WSDConfig`. |
| `wandb_project` | Required | Tracking project started during trainer construction. |
| `wandb_mode` | `"online"` | `"online"`, `"offline"`, `"disabled"` or `"shared"`. |
| `dataloader_num_workers` | `4` | Nonnegative number of spawned workers. |
| `dataloader_prefetch_factor` | `4` | Positive batches per worker, or `None` for PyTorch's default with workers. Must be `None` with zero workers. |
| `validation_tasks` | `[]` | Registered task keys. Empty skips evaluation. |
| `validation_steps` | `None` | Positive update interval; also evaluates at the final update. `None` disables automatic evaluation. |
| `validation_limit` | `None` | Positive document prefix per task; `None` scores all documents. |
| `validation_batch_tokens` | `32768` | Positive padded-token budget for scoring. One request can exceed it. |
| `output_dir` | `None` | Uses `.training-checkpoints/<run_name>` in the current directory when unset. |
| `save_steps` | `None` | Positive update interval; also saves at the final update and off-interval loop cleanup. `None` disables automatic saves. |
| `save_total_limit` | `None` | Positive local retention limit by save timestamp. Pending uploads can exceed it temporarily. `None` keeps all checkpoints. |
| `save_to_hub_repo` | `None` | Optional `org/model` upload destination. |
| `overwrite_output_dir` | `False` | Permit fresh training in a nonempty directory and replacement of a step's checkpoint. |
| `model_dtype` | `torch.bfloat16` | Autocast dtype; model weights are not cast by the trainer. |
| `max_norm` | `1.0` | Finite, nonnegative gradient clipping threshold; zero clips gradients to zero. |
| `logging_steps` | `10` | Positive logging interval; also logs at the final update. |
| `seed` | `42` | Training RNG and sampler seed; model initialization has its own seed. |

### TrainerConfig

[[autodoc]] training.TrainerConfig
    - none

### Optimizers

[[autodoc]] training.AdamWConfig

[[autodoc]] training.MuonConfig

### Learning rates

The scheduler uses completed optimizer updates as its index. It applies the
step-zero rate during construction, then advances after each optimizer update.
Warmup therefore starts with a zero learning rate. Choose schedule lengths that
fit the pass; the scheduler dataclasses do not validate their bounds.

| Configuration | Required fields | Optional fields and defaults |
|---|---|---|
| `CosineConfig` | `warmup_steps`, `min_lr_ratio` | None |
| `WSDConfig` | `warmup_steps`, `decay_steps` | `min_lr_ratio=0.0` |

`OptimizerConfig` is the type alias `AdamWConfig | MuonConfig`.
`LRSchedulerConfig` is the type alias `CosineConfig | WSDConfig`.

[[autodoc]] training.CosineConfig

[[autodoc]] training.WSDConfig

[[autodoc]] training.core.lr_scheduler.StepLRScheduler

[[autodoc]] training.core.lr_scheduler.lr_multiplier

## Data

`DatasetConfig` and `get_dataset` are public exports from `flock.training`.
The batch, collator and sampler live in `flock.training.core.data_utils`.

[[autodoc]] training.DatasetConfig

[[autodoc]] training.get_dataset

[[autodoc]] training.core.data_utils.Batch

[[autodoc]] training.core.data_utils.LanceBatchCollator

[[autodoc]] training.core.data_utils.ResumableBatchDataSampler

## Models

Import model implementations and presets from `flock.training.core.models`.
`Olmo2Config` is also exported from `flock.training`. Both presets initialize
fresh weights; their `hf_repo` values identify tokenizers.

| Preset | Vocabulary | Hidden width | MLP width | Layers | Query/KV heads | Context |
|---|---|---|---|---|---|---|
| `OLMO2_1B_CONFIG` | `100352` | `2048` | `8192` | `16` | `16 / 16` | `4096` |
| `OLMO2_7B_CONFIG` | `100352` | `4096` | `11008` | `32` | `32 / 32` | `4096` |

[[autodoc]] training.Olmo2Config

[[autodoc]] training.core.models.BaseModel
    - get_hf_tokenizer
    - forward

[[autodoc]] training.core.models.Olmo2
    - none

The `Olmo2` forward and tokenizer methods follow the `BaseModel` contract above.

[[autodoc]] training.core.models.HFCompatibleConfig

[[autodoc]] training.core.models.HFCompatibleModel
    - from_hf

`HFCompatibleModel` follows the same contract. See
[Train a Hugging Face architecture](trainer#train-a-hugging-face-architecture).

### Neural network layers

[[autodoc]] training.core.attention.GroupedAttentionWithRoPE
    - forward

[[autodoc]] training.core.nn.SwiGLU
    - forward

## Checkpoint state

`save_model` persists weights and configuration alongside separate safetensors
files for each top-level training-state entry. See the
[Hub model format](../hub/model-storage#what-a-version-holds) for file metadata.
These schemas and metric buffers are implementation-level APIs under
`flock.training.core`.

[[autodoc]] training.core.trainer.TrainerState

[[autodoc]] training.core.checkpoint.CheckpointSchema
    - none

[[autodoc]] training.core.checkpoint.CheckpointDataConfig
    - none

[[autodoc]] training.core.checkpoint.CheckpointTrainerState
    - none

[[autodoc]] training.core.checkpoint.CheckpointSchedulerState
    - none

[[autodoc]] training.core.checkpoint.TrainerCheckpoint
    - none
