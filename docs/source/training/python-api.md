# Training API

`flock.training` exposes the trainer and its configuration classes. Start with
[Train with Flock](trainer) for setup and a complete local example.

## Trainer

[[autodoc]] training.Trainer
    - train
    - evaluate
    - state_dict
    - validate_state_dict
    - load_state_dict

## Distributed runtime

Use `flock.training.core.distributed.distributed_run()` to initialize and tear down
the process group, and pass its `DistributedRuntime` to every `Trainer`. The
runtime reads `RANK`, `LOCAL_RANK` and `WORLD_SIZE` together and defaults to
`(0, 0, 1)` when all three are unset. CUDA uses the device at `LOCAL_RANK`.
Set `enable_cpu=True` on the context manager to select CPU. The context
initializes NCCL for multiple CUDA ranks or Gloo for multiple CPU ranks.

All ranks call `train()` and `evaluate()` in the same order. Only rank zero
initializes W&B, scores evaluation tasks and writes checkpoints.
See [Launch distributed training](trainer#launch-distributed-training).

## Configuration

All `TrainerConfig` fields, including their defaults, are documented below.
Validation rejects unknown fields, nonpositive batch sizes and intervals,
negative worker counts, nonfinite or negative clipping thresholds, and unknown
evaluation task names. With zero workers, set `dataloader_prefetch_factor=None`.

| Field | Default | Meaning |
|---|---|---|
| `run_name` | Required | Default checkpoint folder name and evaluation label. Does not set the W&B run name. |
| `input_column` | Required | Text column in each dataset row. |
| `batch_size` | Required | Positive rows or cached chunks per rank per optimizer update. |
| `max_steps` | `None` | Positive cap on optimizer updates; schedules plan over the capped run. `None` trains the full pass. |
| `optimizer_config` | Required | `AdamWConfig`; Muon is not implemented. |
| `lr_scheduler_config` | Required | `LRSchedulerConfig` containing learning-rate segments. |
| `wandb_project` | Required | Tracking project started during construction on rank zero. |
| `wandb_mode` | `"online"` | `"online"`, `"offline"`, `"disabled"` or `"shared"`. |
| `dataloader_num_workers` | `4` | Nonnegative number of spawned workers per rank. |
| `dataloader_prefetch_factor` | `4` | Positive batches per worker, or `None` for PyTorch's default with workers. Must be `None` with zero workers. |
| `validation_tasks` | `[]` | Registered task keys. Empty skips evaluation. |
| `validation_steps` | `None` | Positive update interval; also evaluates at the final update. `None` disables automatic evaluation. |
| `validation_limit` | `None` | Positive document prefix per task; `None` scores all documents. |
| `validation_batch_tokens` | `32768` | Positive padded-token budget for scoring. One request can exceed it. |
| `output_dir` | `None` | Uses `.training-checkpoints/<run_name>` in the current directory when unset. |
| `save_steps` | `None` | Positive update interval. Cleanup saves an off-interval step after progress on a successful run, or after a loop error in a single-process run. `None` disables automatic saves. |
| `save_total_limit` | `None` | Positive local retention limit by save timestamp, applied after each successful save. Pending uploads can temporarily put the number of checkpoints over the limit. `None` keeps all checkpoints. |
| `save_to_hub_repo` | `None` | Optional `org/model` upload destination. |
| `overwrite_output_dir` | `False` | Permit fresh training in a nonempty directory and replacement of a step's checkpoint. |
| `model_dtype` | `torch.bfloat16` | Autocast dtype; model weights are not cast by the trainer. |
| `torch_compile` | `False` | Compile the training model, the loss, gradient clipping with the optimizer step, and the evaluation model. See [Compile training](trainer#compile-training). |
| `torch_compile_mode` | `"default"` | PyTorch mode: `"default"`, `"reduce-overhead"`, `"max-autotune"` or `"max-autotune-no-cudagraphs"`. Used when `torch_compile=True`. |
| `max_norm` | `1.0` | Finite, nonnegative gradient clipping threshold; zero clips gradients to zero. |
| `logging_steps` | `10` | Positive logging interval; also logs at the final update. |
| `seed` | `42` | Training RNG and sampler seed; model initialization has its own seed. |

### TrainerConfig

[[autodoc]] training.TrainerConfig
    - none

### Optimizers

Optimizer configs and factories live in `training.core.optimizers.adamw` and
`training.core.optimizers.muon`. In
`training.core.optimizers.registry`, `OptimizerInterface` maps config types to
optimizer factories. `register(config_type, factory)` registers or replaces a
factory; `get_interface(config_type)` returns it. Each factory accepts the config
and model parameters and returns a `BaseOptimizer`. After moving the model to its
device, the trainer calls `OptimizerInterface.get_interface(type(config))` and
constructs the optimizer with the returned factory.
Registration affects future optimizer construction. `TrainerConfig` still
accepts the config types in `OptimizerConfig`.
`BaseOptimizer` describes the PyTorch interface shared by the trainer, scheduler
and checkpoints: `zero_grad()`, `step()`, `param_groups`, `state_dict()` and
`load_state_dict()`. Compiled training also uses the state-dict post-hook
registration methods. Native PyTorch optimizers satisfy this protocol directly.
The trainer runs backpropagation and gradient clipping before `optimizer.step()`.

[[autodoc]] training.AdamWConfig

[[autodoc]] training.MuonConfig

### Learning rates

The scheduler uses completed optimizer updates as its index. It applies the
step-zero rate during construction, then advances after each optimizer update.
The optimizer config supplies each parameter group's base learning rate.

| Configuration | Field | Default | Meaning |
|---|---|---|---|
| `LRSchedulerConfig` | `schedule` | Required | Nonempty sequence of `LRScheduleSegment` values, stored as an immutable tuple. |
| `LRScheduleSegment` | `start` | Required | Nonnegative integer update count or float fraction in `[0, 1]`. |
| `LRScheduleSegment` | `end` | Required | Integer update count or float fraction in `[0, 1]`, after `start`. |
| `LRScheduleSegment` | `start_ratio` | Required | Initial ratio of the optimizer group's base rate, in `[0, 1]`. |
| `LRScheduleSegment` | `end_ratio` | Required | Final ratio of the optimizer group's base rate, in `[0, 1]`. |
| `LRScheduleSegment` | `interpolation` | `"linear"` | `"linear"` or `"cosine"`, for increases or decreases. |

`1` means update one; `1.0` means the run endpoint. Fractional times resolve to
`floor(fraction * total_steps)`. Construction rejects nonpositive run lengths,
overlaps, gaps between segments, reversed timeframes, endpoints beyond the run,
and segments that become empty after rounding. Configuration fields are frozen and reject extras.

Adjacent segments must share a time boundary. Gaps in matching units are rejected
by `LRSchedulerConfig`; mixed units are checked after resolving the run length.
Before the first segment, the scheduler holds its starting ratio. After the last
segment, it holds that segment's ending ratio. At a shared boundary the later segment takes precedence.
Use equal starting and ending ratios for a constant segment. See the
[run configuration example](trainer#configure-a-run) for warmup, a stable phase
and cosine decay.

Checkpoint state contains the segment config, planned update count, base rates
and completed updates. Resume requires matching settings, including integer
versus fractional times. Checkpoints from the former `CosineConfig`/`WSDConfig`
API are incompatible with this format.

`OptimizerConfig` is the type alias `AdamWConfig | MuonConfig`.

[[autodoc]] training.LRSchedulerConfig
    - none

[[autodoc]] training.LRScheduleSegment
    - none

[[autodoc]] training.core.lr_scheduler.normalize_training_schedule

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

`CheckpointManager.save_model(step, model, training_state=state)` persists weights
and configuration alongside separate safetensors files for each top-level
training-state entry. See the
[Hub model format](../hub/model-storage#what-a-version-holds) for file metadata.
These schemas and metric buffers are implementation-level APIs under
`flock.training.core`.

[[autodoc]] training.core.trainer.TrainerState

[[autodoc]] training.core.trainer.CheckpointTrainerState
    - none

[[autodoc]] training.core.trainer.CheckpointRankState
    - none

[[autodoc]] training.core.lr_scheduler.LRSchedulerState
    - none

[[autodoc]] training.core.trainer.TrainerCheckpoint
    - none

## Checkpoint persistence

Construct a manager with persistence settings and pass the step and module at
each save. It has no trainer dependency:

```python
from flock.training.core.checkpoint import CheckpointManager

manager = CheckpointManager(
    "checkpoints", save_to_hub_repo="cerulean-labs/olmo2-dolma3", save_total_limit=3
)
manager.save_model(step, model, training_state=state)
manager.finish_checkpoint_uploads()
```

Local serialization always finishes before `save_model` returns. With
`blocking=False`, the Hub uploads the completed checkpoint in the background.
Later model or training-state updates cannot change the saved files.
With `blocking=True`, the save waits for all pending uploads before pruning.
Both modes enforce `overwrite_output_dir` and apply local retention, keeping
folders while their uploads are pending. `finish_checkpoint_uploads()` waits for
the remaining uploads and applies retention again. Local persistence errors
propagate to the caller. Upload errors are logged.

`load_checkpoint(path, model)` restores weights and returns training state.
Pass `validate_state=trainer.validate_state_dict` to validate the run before
weights change, then call `trainer.load_state_dict(state)` with the result.
Optimizer group compatibility checks remain in the trainer. Modules without a
config can be saved and restored into an existing matching module; construction
through the Hub requires a supported Flock model and frozen config dataclass.

[[autodoc]] training.core.checkpoint.CheckpointManager
    - save_model
    - finish_checkpoint_uploads
    - load_checkpoint
    - checkpoints
    - latest_checkpoint

## Token chunks

[[autodoc]] training.TokenChunkDataset
    - prepare
