# Train with Flock

`Trainer` runs one shuffled pass, or its first `max_steps` updates, over a Lance
text dataset on one device or across distributed workers. Each batch produces one
optimizer update: tokenize the text, build next-token targets, compute
cross-entropy, backpropagate, clip gradients, update weights and advance the
learning rate. Padding targets are ignored when computing loss.
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
from flock.training import AdamWConfig, LRSchedulerConfig, LRScheduleSegment, TrainerConfig

config = TrainerConfig(
    run_name="olmo2_dolma3",
    input_column="text",
    batch_size=2,
    optimizer_config=AdamWConfig(lr=1e-3, betas=(0.95, 0.99)),
    lr_scheduler_config=LRSchedulerConfig(
        schedule=[
            LRScheduleSegment(start=0, end=0.1, start_ratio=0, end_ratio=1),
            LRScheduleSegment(start=0.1, end=0.9, start_ratio=1, end_ratio=1),
            LRScheduleSegment(
                start=0.9, end=1.0, start_ratio=1, end_ratio=0, interpolation="cosine"
            ),
        ]
    ),
    dataloader_num_workers=2,
    dataloader_prefetch_factor=1,
    wandb_project="test",
    wandb_mode="offline",
    save_steps=200,
)
```

`batch_size` counts dataset rows per rank per update. The run has
`ceil(len(dataset) / (batch_size * world_size))` updates, including a final partial
batch. For a single-device run, `world_size` is `1`.
Set `max_steps` to end the run earlier: training stops after that many updates of
the same shuffled order, and fractional schedule times become fractions of the
shorter run. A checkpoint resumes only with the same effective number of updates.
The example warms up linearly over the first 10% of updates, holds the
optimizer's base rate through 90%, then decays to zero with a cosine curve.
To decay immediately after warmup, remove the constant segment and change the
decay segment's `start` to `0.1`.

Each segment names its timeframe, starting and ending LR ratios, and
`"linear"` or `"cosine"` interpolation. Integer times count completed optimizer
updates; floating-point times are fractions of the run, rounded down to whole
updates. Use `100` for step 100 and `1.0` for the run endpoint.
Adjacent segments must share a time boundary; gaps are rejected. Use equal
starting and ending ratios for an explicit stable phase. After the last segment,
the scheduler holds its ending ratio. Before the first segment, it holds that
segment's starting ratio.
The later segment takes precedence at a shared boundary. Timeframes must be
ordered, non-overlapping and nonempty after rounding, and fit the run.
The optimizer config supplies the base rates; ratios scale each parameter group.

Use `dataloader_num_workers=0` and `dataloader_prefetch_factor=None` to load in
the training process. With workers, use a positive prefetch count or `None` for
PyTorch's default. `TrainerConfig` rejects unknown fields and invalid intervals.
See [Configuration](python-api#configuration) for all fields and defaults.

Use `AdamWConfig` to apply AdamW to the model. To pair Muon with AdamW, pass
this value as `optimizer_config` when constructing `TrainerConfig`.

```python
from flock.training import AdamWConfig, MuonConfig

optimizer_config = MuonConfig(
    lr=0.02,
    momentum=0.95,
    weight_decay=0.1,
    companion_optimizer_config=AdamWConfig(lr=1e-3, betas=(0.95, 0.99)),
)
```

Muon updates every trainable 2D parameter, including embedding and output
matrices. The companion updates the trainable parameters of all other
dimensions, such as biases and normalization weights. The model must have
at least one trainable parameter in each category. Each optimizer has its
own base learning rate, and the same schedule multiplier applies to both.

With the recipe's `model`, `tokenizer` and `dataset` already constructed, run:

```python
from flock.training import Trainer

trainer = Trainer(config=config, model=model, tokenizer=tokenizer, dataset=dataset)
try:
    trainer.train()
finally:
    if trainer.run is not None:
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

## Keep long documents as chunks

Prepare a local token cache before constructing the model or trainer. The cache keeps
source columns and tokenizes each document once without truncation. Your tokenizer's
special-token policy applies once per document; no BOS or EOS is inserted at chunk
boundaries. Configure the tokenizer to add EOS at document ends if your corpus needs it.

```python
from flock.training import TokenChunkDataset

chunks = TokenChunkDataset.prepare(
    dataset=dataset,
    tokenizer=tokenizer,
    input_column="text",
    cache_dir=".training-cache/documents",
    chunk_length=4096,
)
trainer = Trainer(config=config, model=model, tokenizer=tokenizer, dataset=chunks)
```

Here `batch_size=8` means eight chunks per rank per update. The sampler shuffles chunks independently,
including chunks from the same document. The schedule counts chunk batches, so
retaining long documents increases the number of updates in a complete pass.
Choose the learning-rate schedule for that new count.

Each item holds at most 4096 token IDs, yielding at most 4095 next-token targets.
Adjacent chunks overlap by one context token. Every original next-token target appears
exactly once, including a final tail with only one target. Documents with fewer than two
tokens produce no items. Short documents remain separate; there is no packing.
Apply document exclusions and deduplication before preparing the cache. Each chunk
keeps its document metadata and adds its document index and chunk index.

Keep this cache private like its source: it contains the source text and tokens.
Preparation refuses an existing path. Reopen the completed cache to resume, or use a
different chunk length without tokenizing again:

```python
chunks = TokenChunkDataset(cache_dir=".training-cache/documents", chunk_length=4096)
```

The cache pins its first Lance version. Do not modify it. Resume checks its source URI
and version, tokenizer rules and chunk length in addition to the normal sampler settings.
Changing any of those requires a fresh training run. The trainer also checks the cache's
text column and tokenizer against the supplied configuration. Chunk length must not
exceed the tokenizer's model limit.

## Launch distributed training

Use `torchrun` to start one process per GPU. Each process keeps a full copy of
the model and optimizer, so each GPU must have enough memory for a replica.
`torchrun` supplies `RANK`, `LOCAL_RANK` and `WORLD_SIZE`; `distributed_run()`
uses those variables to select the device and initialize the process group for
multiple ranks. It uses NCCL on CUDA and Gloo on CPU, and destroys the group when
you exit the context. Pass `enable_cpu=True` to `distributed_run()` to select
CPU even on a CUDA host.

Save this as `train_distributed.py`. It uses the same pinned dataset and model
as the scaffold recipe. Each host needs the installed workspace, dataset access
and access to the model's tokenizer.

```python
from flock.training import (
    AdamWConfig,
    DatasetConfig,
    LRSchedulerConfig,
    LRScheduleSegment,
    Trainer,
    TrainerConfig,
    get_dataset,
)
from flock.training.core.distributed import distributed_run
from flock.training.core.models import OLMO2_1B_CONFIG, Olmo2


def main() -> None:
    with distributed_run() as runtime:
        dataset = get_dataset(DatasetConfig(uri="s3://flock-datasets/finepdfs", version=1))
        model = Olmo2(OLMO2_1B_CONFIG)
        tokenizer = model.get_hf_tokenizer()
        assert tokenizer is not None
        config = TrainerConfig(
            run_name="olmo2_dolma3",
            input_column="text",
            batch_size=2,
            optimizer_config=AdamWConfig(lr=1e-3, betas=(0.95, 0.99)),
            lr_scheduler_config=LRSchedulerConfig(
                schedule=[LRScheduleSegment(start=0, end=1.0, start_ratio=1, end_ratio=0)]
            ),
            dataloader_num_workers=2,
            dataloader_prefetch_factor=1,
            wandb_project="test",
            wandb_mode="offline",
            output_dir=".training-checkpoints/olmo2_dolma3",
            save_steps=200,
        )
        trainer = Trainer(
            config=config, model=model, tokenizer=tokenizer, dataset=dataset, runtime=runtime
        )
        try:
            trainer.train()
        finally:
            if trainer.run is not None:
                trainer.run.finish()


if __name__ == "__main__":
    main()
```

On a host with four visible GPUs, launch from the directory containing the script:

```bash
uv run torchrun --standalone --nproc-per-node=4 train_distributed.py
```

Set `--nproc-per-node` to the number of GPUs you want to use on that host. The
trainer selects each worker's GPU from `LOCAL_RANK`, synchronizes gradients with
DDP and partitions the shuffled dataset across ranks. With `batch_size=2` and four
ranks, a full global update processes eight rows. The trainer supplies the
distributed sampler and the DDP wrapper. On the final partial update, ranks
with no rows still participate using a dummy batch whose targets are all ignored.
Each rank's contribution to the gradient is weighted by its valid target count,
so the update uses the mean loss over all valid targets across ranks.
All ranks use the same shuffle seed. The trainer seeds Python and PyTorch on
each rank with `seed + rank`.

Construct the trainer and call `train()` on every rank inside the
`distributed_run()` context. If you call `evaluate()` directly, call it on every
rank too. Rank zero scores the tasks and broadcasts completion or an exception.
Rank zero also owns the W&B run. Check
`trainer.run is not None` before finishing it, as in the example.

### Launch across hosts

For two hosts with four GPUs each, run the same script and configuration on both
hosts. Set `output_dir` to a shared filesystem path writable by rank zero and readable
by every rank for resume. Both hosts must reach the first host at the chosen address and port.
Replace `10.0.0.10` below with that host's address.

On the first host:

```bash
uv run torchrun --nnodes=2 --nproc-per-node=4 --node-rank=0 --master-addr=10.0.0.10 --master-port=29500 train_distributed.py
```

On the second host:

```bash
uv run torchrun --nnodes=2 --nproc-per-node=4 --node-rank=1 --master-addr=10.0.0.10 --master-port=29500 train_distributed.py
```

This creates eight ranks and a full global batch of sixteen rows. Use a new
output directory for a fresh run. To resume, change the call in the script to
`trainer.train(resume_from_checkpoint=True)` on every rank and launch with the
same world size. See [Save and resume training](#save-and-resume-training) for
checkpoint visibility, per-rank RNG state and compatibility checks.

## Compile training

Set `torch_compile=True` in `TrainerConfig` to compile each training step with
`torch.compile`. Compilation is off by default. The trainer compiles three parts:

- The training model, including its backward.
- The loss, including its backward.
- Gradient clipping together with the optimizer step.

Evaluation also uses a compiled model.
Compilation happens on first use, and new input shapes can compile again, so measure
speed after warmup.

The trainer keeps each learning rate in a tensor and updates it in place, so a changing
schedule does not compile the optimizer step again. With float rates, the first change
would trigger a recompile. On CUDA, optimizers that support it run with
`capturable=True`. Checkpoints store float learning rates, so you can resume a
checkpoint with compilation enabled or disabled. A checkpoint from a compiled CUDA run
keeps `capturable=True`, and an eager run that resumes it keeps that setting.

`torch_compile_mode` selects the PyTorch mode:

| Mode | Use |
|---|---|
| `"default"` | Fuse operations without CUDA graphs. |
| `"max-autotune-no-cudagraphs"` | Also benchmark kernel choices. The first step takes longer. |
| `"reduce-overhead"` | Replay CUDA graphs to cut kernel launch overhead. Uses more memory. |
| `"max-autotune"` | Autotune and replay CUDA graphs. |

With CUDA graphs, each training step starts a new graph generation. Evaluation can
record a graph for every batch shape it scores; use a mode without CUDA graphs if that
memory matters.

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

from flock.training import AdamWConfig, LRSchedulerConfig, LRScheduleSegment, Trainer, TrainerConfig
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
            lr_scheduler_config=LRSchedulerConfig(
                schedule=[
                    LRScheduleSegment(
                        start=0,
                        end=1.0,
                        start_ratio=1,
                        end_ratio=0.1,
                        interpolation="cosine",
                    ),
                ]
            ),
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

## Choose an attention backend

`Olmo2Config.attn_implementation` defaults to `"sdpa"`, which lets PyTorch choose
an attention kernel. Leave it unset to keep the default. The backends on this
page apply to OLMo 2 only: `HFCompatibleConfig.attn_implementation` names a
`transformers` kernel instead.

Pass a `[batch, sequence]` padding mask in `Batch["attention_mask"]`, with True/1
for real tokens, and each input's position in `Batch["position_ids"]`, counting from
the row's first real token. The collator builds both. OLMo 2 handles causality and
prepares the backend's mask for you.

### Use FlashAttention-3 or FlashAttention-4

Set `attn_implementation` to `"flash_attention_3"` or `"flash_attention_4"`. FA3
runs only on Hopper GPUs (compute capability 9.0), such as H100 and H200. FA4 also
runs on Blackwell data center GPUs (10.x), such as B200 and B300, so use FA4 there.

```python
from dataclasses import replace

from flock.training.core.models import OLMO2_1B_CONFIG

config = replace(OLMO2_1B_CONFIG, attn_implementation="flash_attention_4")
```

- **Kernels** come from prebuilt packages that `uv sync` installs on Linux with
  the default `gpu` dependency group: `flash-attn-3` from PyTorch's CUDA 13 wheel
  index, and `flash-attn-4` from PyPI, which compiles its kernels on first use.
  Neither needs a local build. PyTorch 2.14 marks its FA4 integration as a prototype.
- **Inputs** must be FP16 or BF16, which the trainer's default BF16 autocast
  provides. Padding can sit at either end of a row, and rows can be empty. Masks
  with holes, dense masks and unequal Q/K sequence lengths are unsupported.
- **Padded batches** reuse one `VarlenMetadata`, built once per forward, and each
  layer packs the real tokens into one `varlen_attn` call. Unpadded batches use one
  batched SDPA call per layer.

An unsupported GPU or input raises an error; there is no fallback. Outputs at
padding positions are zero, so final logits there can differ from SDPA. Compare
real-token outputs; the trainer's collator already excludes padding from the loss.

Activating FA3 or FA4 replaces PyTorch's Flash backend for the whole process, so
only one of them can be active. Keep it active until all pending backward passes
finish.

### Register a custom backend

Register your attention function with a matching mask preparer. For an
`attention_fn` that accepts SDPA masks:

```python
from flock.training.core.attention import AttentionInterface, prepare_sdpa_mask

AttentionInterface.register("custom", attention_fn, mask_preparer=prepare_sdpa_mask)
```

Set `attn_implementation="custom"` to select it. Register before constructing or
loading the model; saved configs keep the backend name.

The function takes Q/K/V shaped `[batch, heads, sequence, head_dim]` and the
keyword arguments `attention_metadata` and `is_causal`. Return the same layout,
with Q's heads and sequence length. With SDPA, an explicit mask must include the
causal restriction.

A preparer takes a boolean 2D `padding_mask` (or `None`) and keyword-only
`is_causal`, and returns the metadata its attention function reads. The model
calls it once per forward and passes the result to every layer unchanged.
`AttentionMetadata` lists the kinds of metadata, one type per kind:

| Kind | Preparer | Read by |
|---|---|---|
| Dense mask (`torch.Tensor`) | `prepare_sdpa_mask` | `sdpa_attention` |
| `VarlenMetadata` | `prepare_varlen_metadata` | `flash_attention_3`, `flash_attention_4` |

`None` means no padding. To support a new kind, add its type to
`AttentionMetadata`; model code does not change.

Direct callers of `flash_attention_3` or `flash_attention_4` prepare the padding
mask first:

```python
metadata = prepare_varlen_metadata(padding_mask, is_causal=True)
output = flash_attention_3(q, k, v, attention_metadata=metadata, is_causal=True)
```

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

Loss and token counts include all ranks. `train/loss_window_max` is the largest
per-rank batch mean loss in the window. Rank zero logs the metrics. When the
application has yet to configure root logging, the trainer sets up INFO logging
with the format `MM-DD HH:MM:SS INFO <logger> rank:<rank> <message>`.

The trained weights remain in `trainer.model`. Each batch produces one optimizer
update, with no gradient accumulation.

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
other errors propagate to every rank. Interval checkpoints are saved before
validation on the same update. If the final update falls between save intervals,
validation runs first and the final save happens during loop cleanup.

## Save and resume training

Set `save_steps` in `TrainerConfig` to save at that update interval. The default
`None` disables automatic saves. When training ends between intervals, loop
cleanup saves the final step if the loop completed at least one update.
Single-process cleanup also attempts this save if training raises. Distributed
cleanup attempts it only after the loop returns successfully. The final save
queues a Hub upload like an interval save. Rank zero then waits for pending
uploads and applies local retention again.
Each checkpoint holds model weights, model configuration, optimizer and scheduler
state, training counters, the unfinished logging window, and Python and PyTorch
RNG states. Checkpoints use the [Hub model format](../hub/model-storage#what-a-version-holds).

In distributed training, every rank contributes its Python, CPU and CUDA RNG
states and completed-batch cursor. Rank zero writes these records alongside one
copy of the model, optimizer, scheduler and global metrics. If any rank fails
to capture its state, the trainer logs the error and skips that checkpoint.
On a successful save, all ranks synchronize after rank zero writes the local
files and queues any Hub upload. Local persistence errors propagate on the
writing rank. Hub upload errors are logged.

Set `output_dir` for a checkpoint root, or use the default
`.training-checkpoints/<run_name>`. Fresh training requires an empty directory
unless `overwrite_output_dir=True`. After each successful save, `save_total_limit`
retains the newest local checkpoints by `flock.json`'s `saved_at`. Older folders
with pending uploads can temporarily put the number of checkpoints over the limit.

A checkpoint appears as `output_dir/checkpoint-<step>` only after all of its files
have been written. Construct a new trainer with the same model configuration,
tokenizer, pinned dataset, batch size, seed and world size, then resume the checkpoint with
the latest `saved_at` timestamp:

```python
trainer = Trainer(config=config, model=model, tokenizer=tokenizer, dataset=dataset)
try:
    trainer.train(resume_from_checkpoint=True)
finally:
    if trainer.run is not None:
        trainer.run.finish()
```

You can also pass a specific local folder:

```python
trainer.train(resume_from_checkpoint=".training-checkpoints/olmo2_dolma3/checkpoint-200")
```

The trainer restores weights into the existing model and resumes after the saved
number of batches in the seeded shuffle. Logging and saving keep their original
step numbers. Resume rejects a changed model architecture/configuration, dataset
size, batch size, seed, input column, world size, optimizer group settings or scheduler
configuration/base rates. Keep the dataset contents and tokenizer unchanged;
ordinary text-dataset checkpoints do not verify their identity. Token-cache checkpoints also verify the cache identity described above. Resume initializes a new tracking
run and records the restored global steps there.

Every rank reads weights and training state directly from the checkpoint folder
and restores the RNG streams and sampler cursor saved under its global rank.
Make the same checkpoint available to every rank. Keep the original world size
and each rank's visible CUDA device count. Trainer resume requires a checkpoint
with `world_size` and a `rank_states` entry for the calling rank.

To resume a checkpoint stored in the Hub, download its training state too, then
pass the downloaded folder. For a distributed run, make the downloaded files
readable on every host before calling `train()` on all ranks.

```python
from flock.hub import models

checkpoint = models.download(
    "cerulean-labs/olmo2-dolma3@checkpoint-200", include_training_state=True
)
trainer.train(resume_from_checkpoint=checkpoint)
```

Use `trainer.state_dict()` to capture shared training state and the calling
rank's local record without model weights or communication. Scheduled saves
gather all local records into `rank_states` before writing. Its `config`
dictionary records dataset size, batch size, seed, input column and world size
for resume compatibility checks.
`trainer.validate_state_dict(state)` checks the run contract and optimizer group
compatibility, and asks the metric buffers and scheduler to validate their state
without changing them.
`trainer.load_state_dict(state)` performs these checks before restoring any
training state. Tensors can share storage with live objects, so serialize the
result immediately or deep-copy it for an in-memory snapshot.

For a manual save in a single-process run, pass the current step, model and
resumable state explicitly:

```python
manager = trainer.checkpoint_manager
manager.save_model(
    int(trainer.state.step.item()), trainer.model, training_state=trainer.state_dict()
)
manager.finish_checkpoint_uploads()
```

The manager writes weights, buffers and training state to local files before
`save_model` returns. Later training updates cannot change those files. The Hub
handles background uploads. After a save, the manager collects completed uploads
and applies retention. `finish_checkpoint_uploads()` waits for the remaining
uploads and applies retention again.

Set `save_to_hub_repo="org/model"` to upload each checkpoint with a
`checkpoint-<step>` tag. Passing `blocking=True` to `save_model` waits for all
uploads after saving. Replacing an existing checkpoint requires
`overwrite_output_dir=True`.
Upload failures are logged. Local checkpoints remain subject to the configured
retention limit, which applies only to local folders. The manager takes
persistence settings at construction, independently of the trainer.

To restore a local checkpoint without starting training, validate the training
state before any weights change, then apply the returned state:

```python
state = manager.load_checkpoint(path, trainer.model, validate_state=trainer.validate_state_dict)
trainer.load_state_dict(state)
```

Pending uploads and published checkpoint folders are available through
`manager.pending_uploads` and `manager.checkpoints()` respectively.
