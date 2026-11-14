# Train with Flock

`Trainer` runs one shuffled pass over a Lance text dataset on one device. Each
batch produces one optimizer update: tokenize the text, build next-token targets,
compute cross-entropy, backpropagate, clip gradients, update weights and advance
the learning rate. Padding targets are ignored when computing loss.
The [Training API](python-api) lists configuration fields, defaults and public methods.

## Run the scaffold experiment

The [experiment recipe](https://github.com/cerulean-works/flock/blob/main/flock/training/experiments/scaffold/olmo2_dolma3.py) reads
`s3://flock-datasets/finepdfs` at version `1`, using its `text` column. The current
`get_dataset` helper calls `s3.get_process` for read-only access to `cerulean-labs`,
which your account must have access to. This sets up Lance's native credential
process and its environment automatically. Pinning a version keeps the training
data fixed between runs.

The recipe initializes a fresh OLMo 2 1B model with FP32 weights and uses BF16
autocast for training; it does not load
pretrained weights. Its Hugging Face repository supplies the tokenizer. Use a
compatible GPU with enough memory for the model, gradients and optimizer state.
The trainer selects CUDA when available, otherwise CPU, but does not change the model's
dtype for those devices. The recipe logs offline and saves local checkpoints
every 200 updates and at the final update.

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
    save_steps=200,
)
```

`batch_size` counts dataset rows per update. The run has
`ceil(len(dataset) / batch_size)` updates, including a final partial batch.
Set `max_steps` to end the run earlier: training stops after that many updates of
the same shuffled order, and both schedules decay over the shorter run. A checkpoint
resumes only with the same effective number of updates.
**WSD** means warmup, stable, decay: increase the learning rate linearly, hold it
at the optimizer's configured rate, then decay it with a cosine curve over the
last `decay_steps`. Choose warmup and decay lengths that fit the run. For cosine
decay immediately after warmup, use
`CosineConfig(warmup_steps=100, min_lr_ratio=0.1)` instead.

Use `dataloader_num_workers=0` and `dataloader_prefetch_factor=None` to load in
the training process. With workers, use a positive prefetch count or `None` for
PyTorch's default. `TrainerConfig` rejects unknown fields and invalid intervals.
AdamW is implemented; selecting `MuonConfig` raises `NotImplementedError`.
See [Configuration](python-api#configuration) for all fields and defaults.

With the recipe's `model`, `tokenizer` and `dataset` already constructed, run:

```python
from flock.training import Trainer

trainer = Trainer(config=config, model=model, tokenizer=tokenizer, dataset=dataset)
try:
    trainer.train()
finally:
    trainer.run.finish()
```

When using workers, create the data, model and trainer inside an
`if __name__ == "__main__":` guard in your experiment script: the loader starts
workers with `spawn`. The tokenizer
controls padding and truncation; a custom model must accept the collated batch
and return logits shaped `[batch, sequence, vocabulary]`. The collator already
shifts labels for next-token prediction, so the model should not shift them again.
The resulting tensors have shape `[batch, padded_length - 1]`. Targets use `-100`
when either the input or target position is padding. Loss is summed cross-entropy
over valid targets divided by their count; an all-ignored batch has zero loss.
Supply text that produces at least two tokens per row for useful training.

## Train a Hugging Face architecture

`HFCompatibleModel` wraps a causal language model from `transformers`, such as Qwen 3,
Llama or OLMo 2, so the trainer, `save_to_hub` and `load_from_hub` handle it like
`Olmo2`. Only the architecture comes from `transformers`: construction runs no code
from the repository and loads no weights unless you ask for them.

```python
from flock.training.core.models import HFCompatibleModel

fresh = HFCompatibleModel.from_hf("Qwen/Qwen3-0.6B")
smaller = HFCompatibleModel.from_hf("Qwen/Qwen3-0.6B", num_hidden_layers=8, init_seed=7)
tuned = HFCompatibleModel.from_hf("Qwen/Qwen3-0.6B", pretrained=True)
tokenizer = fresh.get_hf_tokenizer()
```

`from_hf` reads the repository's `config.json` and sets `hf_repo` to the repository,
so `get_hf_tokenizer()` loads its tokenizer. Keyword arguments that name
`HFCompatibleConfig` fields, such as `init_seed` or `attn_implementation`, set those
fields. Any other keyword replaces that key of `config.json`, which is how you change
the architecture. `repo` can also be a local folder.

- **Fresh weights** use Flock's seeded initialization, like `Olmo2`: the same
  `init_seed` gives the same weights. Linear and embedding weights are drawn with the
  config's `initializer_range` (the repository's value by default, then `0.02`).
  Norms keep the ones `transformers` gives them.
- **`pretrained=True`** builds the model on the meta device and loads the repository's
  safetensors weights, renamed as an [import](../hub/import-from-hf) renames them.
  Repositories with only `.bin` weights are refused.
- **Attention** uses `attn_implementation`, which defaults to `"sdpa"`.
  `"flash_attention_2"` works when it's installed. The setting is part of the config,
  so a resumed checkpoint keeps it.

To build one from a config you already have, pass an `HFCompatibleConfig`:

```python
from flock.training.core.models import HFCompatibleConfig, HFCompatibleModel

config = HFCompatibleConfig(hf_config=qwen_config_json, max_position_embeddings=2048)
model = HFCompatibleModel(config)
```

`hf_config` is the `transformers` `config.json` as a dict, and must name a causal
language model that the installed `transformers` ships. `max_position_embeddings`
limits the input length the trainer accepts, without changing the architecture.

The model's state-dict names are the Hugging Face names with `model.` in front, such
as `model.lm_head.weight`, because it holds the `transformers` model as `self.model`.
The forward pass gives `transformers` the 2-D attention mask and position IDs that
skip left padding, and returns logits. The trainer computes the loss as it does for
`Olmo2`.

## Try a local run

This complete example trains a tiny model on three local rows, saves two
checkpoints, and restores the latest one in a new trainer. It needs the installed
workspace but no sign-in, downloaded tokenizer or remote dataset. Temporary
files are removed when it finishes. Save it as a Python script and run it from
your workspace environment.

```python runnable:local-training
from pathlib import Path
from tempfile import TemporaryDirectory

import lance
import pyarrow as pa
import torch
from tokenizers import Tokenizer, models, pre_tokenizers
from transformers import TokenizersBackend

from flock.training import AdamWConfig, CosineConfig, Trainer, TrainerConfig
from flock.training import DatasetConfig, Olmo2Config, get_dataset
from flock.training.core.models import Olmo2


def main() -> None:
    backend = Tokenizer(
        models.WordLevel({"[UNK]": 0, "[PAD]": 1, "a": 2, "b": 3, "c": 4}, unk_token="[UNK]")
    )
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer = TokenizersBackend(
        tokenizer_object=backend,
        unk_token="[UNK]",
        pad_token="[PAD]",
        padding_side="left",
        model_max_length=16,
    )
    model_config = Olmo2Config(
        vocab_size=5,
        hidden_size=16,
        intermediate_size=32,
        num_hidden_layers=1,
        num_attention_heads=2,
        num_key_value_heads=2,
        max_position_embeddings=16,
    )
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        uri = str(root / "text.lance")
        lance.write_dataset(pa.table({"text": ["a b c", "b c", "c a b"]}), uri)
        dataset = get_dataset(DatasetConfig(uri=uri, version=1))
        config = TrainerConfig(
            run_name="local-example",
            input_column="text",
            batch_size=2,
            optimizer_config=AdamWConfig(lr=1e-3, betas=(0.9, 0.95)),
            lr_scheduler_config=CosineConfig(warmup_steps=0, min_lr_ratio=0.1),
            dataloader_num_workers=0,
            dataloader_prefetch_factor=None,
            wandb_project="local-example",
            wandb_mode="disabled",
            model_dtype=torch.float32,
            output_dir=str(root / "checkpoints"),
            save_steps=1,
        )
        trainer = Trainer(
            config=config, model=Olmo2(model_config), tokenizer=tokenizer, dataset=dataset
        )
        try:
            trainer.train()
            assert int(trainer.state.step.item()) == 2  # doc-builder: hide
            print(f"Completed {int(trainer.state.step.item())} updates")
        finally:
            trainer.run.finish()
        resumed = Trainer(
            config=config, model=Olmo2(model_config), tokenizer=tokenizer, dataset=dataset
        )
        try:
            resumed.train(resume_from_checkpoint=True)
            assert int(resumed.state.step.item()) == 2  # doc-builder: hide
            print(f"Restored {int(resumed.state.step.item())} updates")
        finally:
            resumed.run.finish()


main()
```

Both lines report `2` updates. The three rows form one full batch and one
partial batch. The resumed pass is already complete, so it performs no further
updates. The example uses FP32 autocast settings for a small CPU run; the trainer
still selects CUDA if it is available.
PyTorch warns that FP32 training autocast is unsupported and disables it;
the example's weights and computations remain FP32.

## Check the run

Watch `global_step`, `train/loss_mean`, `train/loss_window_max`,
`train/grad_norm_mean` and `train/grad_norm_window_max`. Mean loss is weighted by
the number of valid target tokens; gradient norms are measured before clipping.
`train/tokens` counts the valid target tokens since the previous log, and
`train/total_tokens` counts them over the whole run, continuing across a resume.
A valid target is a label that is not `-100`, so padding never counts.
With the recipe's `wandb_mode="offline"`, check the local run directory or
[sync the run later](../app/log-runs#resume-or-sync-a-run). For live logging, set
`wandb_mode="online"`, accept SDK setup during `flock login`, and check the
project in [Cerulean's dashboard](https://flock.cerulean.works).

The trained weights remain in `trainer.model`. The scaffold has no gradient
accumulation or distributed training.
See the [trainer implementation](https://github.com/cerulean-works/flock/blob/main/flock/training/core/trainer.py) for the current loop.

## Validate during training

Set `validation_tasks` to registered [task names](../evals/tasks) and
`validation_steps` to a positive update interval. Validation runs at that interval
and at the final update, logging each metric as `eval/<task>/<metric>` against
`global_step`. You can also call `trainer.evaluate()` directly.

Use `validation_limit` for a document prefix and `validation_batch_tokens` to
control padded scoring batch sizes. A single request can exceed that budget.
The [configuration reference](python-api#configuration) lists defaults.

Each task is loaded and tokenized on its first validation, then its prepared
requests are reused for later steps. Disabled validation does not load tasks.
Keep the task list, document limit and tokenizer unchanged while reusing a trainer.
Limited scores describe the selected documents, rather than the full benchmark.

Validation uses `model_dtype` for autocast, which defaults to `torch.bfloat16`,
while retaining the FP32 master weights. Set `model_dtype=torch.float32` to
disable autocast for validation. Log-softmax stays in FP32. The eval CLI loads
BF16 weights by default, so its scores can differ from autocast over FP32 weights.
Keep precision and batching consistent when comparing checkpoints; see
[Scoring behavior](../evals/scoring#batching-and-precision).

Validation restores each module's previous training mode, including when scoring
fails. A `KeyError` or `FileExistsError` is logged and skips that validation;
other errors stop training. When saving and validation fall on the same update,
the checkpoint is saved first so an evaluation failure leaves it available to resume.

## Save and resume training

Set `save_steps` in `TrainerConfig` to save after that many updates and at the
end of the pass. The default `None` disables scheduled saving.
Each checkpoint holds model weights, model configuration, optimizer and scheduler
state, training counters, the unfinished logging window, and Python and PyTorch
RNG states. Checkpoints use the [Hub model format](../hub/model-storage#what-a-version-holds).

Set `output_dir` for a checkpoint root, or use the default
`.training-checkpoints/<run_name>`. Fresh training requires an empty directory
unless `overwrite_output_dir=True`. `save_total_limit` retains the most recently
saved local checkpoints by `flock.json`'s `saved_at`, protecting older folders
while their uploads are pending. The limit can temporarily be exceeded.

A checkpoint appears as `output_dir/checkpoint-<step>` only after all of its files
have been written. Construct a new trainer with the same model configuration,
tokenizer, pinned dataset, batch size and seed, then resume the checkpoint with
the latest `saved_at` timestamp:

```python
trainer = Trainer(config=config, model=model, tokenizer=tokenizer, dataset=dataset)
try:
    trainer.train(resume_from_checkpoint=True)
finally:
    trainer.run.finish()
```

You can also pass a specific local folder:

```python
trainer.train(resume_from_checkpoint=".training-checkpoints/olmo2_dolma3/checkpoint-200")
```

The trainer restores weights into the existing model and resumes after the saved
number of batches in the seeded shuffle. Logging and saving keep their original
step numbers. Resume rejects a changed model architecture/configuration, dataset
size, batch size, seed, input column, optimizer group settings or scheduler
configuration/base rates. Keep the dataset contents and tokenizer unchanged;
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

For manual saves, call `trainer.save_model()`. Set `save_to_hub_repo="org/model"`
to start an asynchronous upload of each new checkpoint with a `checkpoint-<step>`
tag. `trainer.save_model(blocking=True)` waits for pending uploads; it reuses an
existing current-step checkpoint without rewriting it or starting another upload.
Upload failures are logged and leave the local checkpoint available. Local
retention does not delete Hub versions.

After entering the training loop, cleanup waits for pending uploads and, when
automatic saving is enabled, saves the current step if it falls between save
intervals. This also happens when the loop raises an exception. Local save errors
propagate. Always finish the tracking run in your own `finally` block.
