# Train with Flock

`Trainer` runs one shuffled pass over a Lance text dataset on one device. Each
batch produces one optimizer update: tokenize the text, build next-token targets,
compute cross-entropy, backpropagate, clip gradients, update weights and advance
the learning rate. Padding targets are ignored when computing loss.

## Run the scaffold experiment

The [experiment recipe](https://github.com/cerulean-works/flock/blob/feat/scaffold-training-loop/flock/training/experiments/scaffold/olmo2_dolma3.py) reads
`s3://flock-datasets/finepdfs` at version `1`, using its `text` column. The current
`get_dataset` helper requests storage credentials for `cerulean-labs`, so your
account needs access to that organization. Pinning a version keeps the training
data fixed between runs.

The recipe initializes a fresh OLMo 2 1B model in bfloat16; it does not load
pretrained weights. Its Hugging Face repository supplies the tokenizer. Use a
compatible GPU with enough memory for the model, gradients and optimizer state.
The trainer selects CUDA, then MPS, then CPU, but does not change the model's
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

## Configure a run

Edit the recipe's configuration, or import the public classes from
`flock.training` in your own experiment:

```python
from flock.training import AdamWConfig, CosineConfig, TrainerConfig, WSDConfig

config = TrainerConfig(
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

The trained weights remain in `trainer.model`. This scaffold has no built-in
checkpoint saving or resumption, evaluation loop, gradient accumulation or
distributed training. Keep any saving and evaluation in the experiment script.
See the [trainer implementation](https://github.com/cerulean-works/flock/blob/feat/scaffold-training-loop/flock/training/core/trainer.py) for the current loop.
