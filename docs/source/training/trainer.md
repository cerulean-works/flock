# Train with Flock

`Trainer` runs one shuffled pass over a Lance text dataset on one device. Each
batch produces one optimizer update: tokenize the text, build next-token targets,
compute cross-entropy, backpropagate, clip gradients, update weights and advance
the learning rate. Padding targets are ignored when computing loss.

## Run the scaffold experiment

The [experiment recipe](https://github.com/cerulean-works/flock/blob/feat/scaffold-training-loop/flock/training/experiments/scaffold/olmo2_dolma3.py) reads
`s3://flock-datasets/finepdfs` at version `1`, using its `text` column. The current
`get_dataset` helper calls `s3.get_process` for read-only access to `cerulean-labs`,
which your account must have access to. This sets up Lance's native credential
process and its environment automatically. Pinning a version keeps the training
data fixed between runs.

The recipe initializes a fresh OLMo 2 1B model in bfloat16; it does not load
pretrained weights. Its Hugging Face repository supplies the tokenizer. Use a
compatible GPU with enough memory for the model, gradients and optimizer state.
The trainer selects CUDA when available, otherwise CPU, but does not change the model's
dtype for those devices. The recipe currently logs offline.

1. [Install the workspace](../installation), then sign in from the repository root:

   ```bash
   uv run flock login
   ```

2. Run the experiment:

   ```bash
   uv run python -m flock.training.experiments.scaffold.olmo2_dolma3
   ```

3. Check that the logs report a loaded dataloader and training metrics. The recipe
   keeps run data locally in `wandb/`; see [Check the run](#check-the-run) below.

For S3 URIs, `get_dataset` configures the credential process before opening the
dataset, so no manual AWS profile setup or shell exports are needed. Spawned
workers inherit that environment and use Lance's native renewal. Local paths
skip this setup. The [native-renewal reference](../cli/python-s3#native-renewal-from-python)
lists the environment changes and explains how keys are fetched and renewed.

## Configure a run

Edit the recipe's configuration, or import the public classes from
`flock.training` in your own experiment:

```python
from flock.training import AdamWConfig, CosineConfig, TrainerConfig, WSDConfig

config = TrainerConfig(
    run_name="olmo2_dolma3",
    input_column="text",
    batch_size=2,
    optimizer_config=AdamWConfig(lr=1e-3, betas=(0.95, 0.99)),
    lr_scheduler_config=WSDConfig(warmup_steps=100, decay_steps=100),
    dataloader_num_workers=2,
    dataloader_prefetch_factor=1,
    wandb_project="test",
    wandb_mode="offline",
)
```

`batch_size` counts dataset rows per update. The run has
`ceil(len(dataset) / batch_size)` updates, including a final partial batch.
**WSD** means warmup, stable, decay: increase the learning rate linearly, hold it
at the optimizer's configured rate, then decay it with a cosine curve over the
last `decay_steps`. Choose warmup and decay lengths that fit the run. For cosine
decay immediately after warmup, use
`CosineConfig(warmup_steps=100, min_lr_ratio=0.1)` instead.

| Setting | Meaning |
|---|---|
| `input_column` | Required text column. Rows must contain text with usable next-token targets. |
| `optimizer_config` | AdamW learning rate and betas. `MuonConfig` is exported but not implemented. |
| `dataloader_num_workers` | Worker processes; default `4`. Use `0` to load data in the training process, with `dataloader_prefetch_factor=None`. |
| `dataloader_prefetch_factor` | Batches prefetched per worker; default `4`. Use a positive integer with workers, or `None` to let the loader choose. |
| `max_norm` | Gradient-clipping threshold; default `1.0`. |
| `logging_steps` | Updates per logging window; default `10`. |
| `wandb_project`, `wandb_mode` | Project is required. The mode defaults to `online`; use `offline` to keep logs locally or `disabled` to turn logging off. |

With the recipe's `model`, `tokenizer` and `dataset` already constructed, run:

```python
from flock.training import Trainer

trainer = Trainer(config=config, model=model, tokenizer=tokenizer, dataset=dataset)
trainer.train()
trainer.run.finish()
```

When using workers, create the data, model and trainer inside an
`if __name__ == "__main__":` guard in your experiment script: the loader starts
workers with `spawn`. The tokenizer
controls padding and truncation; a custom model must accept the collated batch
and return logits shaped `[batch, sequence, vocabulary]`. The collator already
shifts labels for next-token prediction, so the model should not shift them again.

## Check the run

Watch `global_step`, `train/loss_mean`, `train/loss_window_max`,
`train/grad_norm_mean` and `train/grad_norm_window_max`. Mean loss is weighted by
the number of valid target tokens; gradient norms are measured before clipping.
With the recipe's `wandb_mode="offline"`, check the local run directory or
[sync the run later](../app/log-runs#resume-or-sync-a-run). For live logging, set
`wandb_mode="online"`, accept SDK setup during `flock login`, and check the
project in [Cerulean's dashboard](https://flock.cerulean.works).

The trained weights remain in `trainer.model`. The scaffold has no evaluation
loop, gradient accumulation or distributed training.
See the [trainer implementation](https://github.com/cerulean-works/flock/blob/feat/scaffold-training-loop/flock/training/core/trainer.py) for the current loop.

## Save and resume training

Set `save_steps` in `TrainerConfig` to save after that many updates and at the
end of the pass. With the default `None`, training does not save automatically.
Each checkpoint holds model weights, model configuration, optimizer and scheduler
state, training counters, the unfinished logging window, and Python and PyTorch
RNG states. Checkpoints use the [Hub model format](../hub/models#what-a-version-holds).

| Setting | Meaning |
|---|---|
| `output_dir` | Checkpoint directory; defaults to `.training-checkpoints/<run_name>` under the current directory. |
| `save_steps` | Positive number of updates between saves, or `None` to disable automatic saving. |
| `save_total_limit` | Positive maximum number of local checkpoint folders to keep, or `None` to keep all. The lowest numbered checkpoints are removed after a successful save. |
| `save_to_hub_repo` | Optional `org/model` destination. Each save uploads the complete local checkpoint with a `checkpoint-<step>` tag. Local retention does not delete Hub versions. |
| `overwrite_output_dir` | Defaults to `False`: fresh training requires an empty output directory, and existing checkpoint folders cannot be replaced. Set to `True` to permit reuse and replacement. |

A checkpoint appears as `output_dir/checkpoint-<step>` only after all of its files
have been written. Construct a new trainer with the same model configuration,
tokenizer, pinned dataset, batch size and seed, then resume the latest numbered
checkpoint:

```python
trainer = Trainer(config=config, model=model, tokenizer=tokenizer, dataset=dataset)
trainer.train(resume_from_checkpoint=True)
trainer.run.finish()
```

You can also pass a specific local folder:

```python
trainer.train(resume_from_checkpoint=".training-checkpoints/olmo2_dolma3/checkpoint-200")
```

The trainer restores weights into the existing model and resumes after the saved
number of batches in the seeded shuffle. Logging and saving keep their original
step numbers. Resume rejects a changed model configuration, dataset size, batch
size, seed, or input column. Keep the dataset contents and tokenizer unchanged;
the checkpoint does not verify their identity. Resume initializes a new tracking
run and records the restored global steps there.

To resume a checkpoint stored in the Hub, download its training state too, then
pass the downloaded folder:

```python
from flock.hub import models

checkpoint = models.download(
    "cerulean-labs/olmo2-dolma3@checkpoint-200", include_training_state=True
)
trainer.train(resume_from_checkpoint=checkpoint)
```
