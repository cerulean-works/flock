# Run a scaling-law sweep

A scaling-law sweep trains many small runs that differ in size, learning rate or
seed, each up to a fixed number of tokens. The `scaling_law` experiment trains
them inside one Slurm allocation, several at a time. This keeps you within a QoS
limit on concurrent jobs: one job holds, say, 16 GPUs on 4 nodes and works
through 20 runs of 4 GPUs each.

The experiment lives in `flock/training/experiments/scaling_law/`. It has four
commands, all run with `python -m flock.training.experiments.scaling_law.sweep`:

| Command | What it does |
|---|---|
| `plan` | Expands the sweep file, validates every run and reports how runs fill the allocation. |
| `prepare` | Tokenizes each data source into its token cache, once. |
| `launch` | Trains the runs inside the current allocation until none are left. |
| `status` | Shows whether each run is pending, running, interrupted, done or failed. |

## Write the sweep file

A sweep file has two parts. `base` holds the settings every run shares. `sweep`
names the sweep and lists what varies between runs. Start from
[`pilot.yaml`](https://github.com/cerulean-works/flock/blob/main/flock/training/experiments/scaling_law/pilot.yaml):

```yaml
sweep:
  name: scaling-law-pilot
  output_root: /shared/flock/sweeps
  gpus_per_run: 4
  name_template: "{model.preset}-mlr{optimizer.muon_lr}-s{seed}"
  grid:
    model.preset: [76m, 190m]
    optimizer.muon_lr: [0.01, 0.02]
    seed: [0, 1, 2, 3, 4]

base:
  data:
    cache_dir: /shared/flock/tokens/pilot-mix-4096
    chunk_length: 4096
    source: {uri: s3://flock-datasets/finepdfs, version: 1}
  model: {preset: 76m, attn_implementation: flash_attention_4}
  optimizer: {type: muon_adamw, lr: 3.0e-3, muon_lr: 0.02}
  schedule:
    - {start: 0, end: 0.01, start_ratio: 0.0, end_ratio: 1.0}
    - {start: 0.01, end: 1.0, start_ratio: 1.0, end_ratio: 0.0}
  trainer: {batch_tokens: 65536, torch_compile: true, save_steps: 500}
  stop_tokens: 1.5B
  save_tokens: [0.5B, 1B]
```

Each run is `base` with one set of overrides applied. Overrides use dotted keys,
such as `optimizer.muon_lr`, to reach nested settings. The sweep can vary runs in
three ways, and combines them as a product:

| Key | Runs it makes |
|---|---|
| `grid` | Every combination of the listed values. |
| `zip` | One run per position. Every list must have the same length, so the values vary together. |
| `runs` | One run per entry. Each entry is a table of dotted-key overrides. |

The example's grid makes 2 × 2 × 5 = 20 runs. Use `zip` to pair values instead of
crossing them, for example a token budget per model size:

```yaml
  zip:
    model.preset: [76m, 190m]
    stop_tokens: [1.5B, 3.8B]
```

`name_template` names each run from its settings. Without it, runs are numbered
`<name>-000`, `<name>-001` and so on. Two runs with the same name are an error.

### Run settings

| Key | Meaning |
|---|---|
| `data.cache_dir` | Token cache on storage every node can read. |
| `data.chunk_length` | Token IDs per chunk in the cache. |
| `data.source` | Lance `uri` and `version` that `prepare` tokenizes. |
| `model.preset` | `76m`, `190m` or `1b`. Omit it and give every `Olmo2Config` field in `model.overrides`. |
| `model.overrides` | `Olmo2Config` fields to change, such as `hidden_size`. |
| `model.attn_implementation` | Attention backend. Defaults to `flash_attention_4`. |
| `optimizer` | `type: muon_adamw` or `type: adamw`, with that config's fields. |
| `schedule` | Learning-rate segments. Fractions are fractions of the run up to `stop_tokens`. |
| `trainer` | Other `TrainerConfig` fields, such as `batch_tokens`, `torch_compile`, `save_steps` and `wandb_project`. |
| `stop_tokens` | Train until this many target tokens, save a checkpoint, then stop. |
| `save_tokens` | Also save a checkpoint once each of these token counts is reached. |
| `seed` | Model initialization and data order. Defaults to `0`. |

Token counts accept `1500000000`, `1.5e9`, `1.5B`, `500M` or `20k`.

## Save at token counts

`stop_tokens` and `save_tokens` count the target tokens the run has trained on,
across all of its GPUs. This is the `train/total_tokens` value in the logs.

When a run starts, it works out which update first reaches each count. It uses
the same seeded batch plan that the data loader follows, so this needs no GPU
work and matches the logged counts exactly. The run then plans its schedule up to
the update that reaches `stop_tokens` and stops there. A fractional schedule
therefore always ends at the token budget, whatever the batch size.

Each update trains on a whole number of chunks, so a run reaches a count during an
update and saves at the end of it. The trained total can pass the target by up to
one update's tokens. `token_checkpoints.json`, next to the checkpoints, records
each target, the update that saved it and the exact tokens trained by then.

Interval saves from `save_steps` still happen. Keep them on, so a run that hits
the job's time limit can resume close to where it stopped.

## Check the plan

Before submitting, check that every run is valid and see how the runs fill the
allocation you will request:

```bash
python -m flock.training.experiments.scaling_law.sweep plan pilot.yaml --nodes 4 --gpus-per-node 4
```

`plan` lists each run with its overrides, then the number of runs that train at
once and how many waves they take. It warns when GPUs would sit idle:

- when `gpus_per_run` does not divide the allocation, some GPUs never run anything
- when the number of runs is not a multiple of the concurrent runs, the last wave leaves slots empty

For example, 20 runs on 3 nodes of 4 GPUs run 3 at a time. The last wave runs 2
and leaves one slot idle, so `plan` suggests a multiple of 3 runs.

Once the token caches exist, add `--check-tokens` to see the update that reaches
each run's token counts. It reports any run whose budget is larger than one pass
over its data.

## Prepare the token caches

Tokenize each data source once, before launching. Run this on a CPU node with
access to the source and the tokenizer:

```bash
python -m flock.training.experiments.scaling_law.sweep prepare pilot.yaml
```

`prepare` skips caches that already exist. Runs refuse to start when their cache
is missing, so concurrent runs never tokenize the same data at the same time.

## Launch in one allocation

Submit the template batch script from the repository root:

```bash
sbatch flock/training/experiments/scaling_law/launch_sweep.sbatch pilot.yaml
```

Edit the `#SBATCH` lines first. Set the nodes, GPUs per node, time limit, and
your cluster's partition, QoS and account. The script activates `.venv` and runs
`sweep launch`. The environment must be on storage that every node can read.

`launch` splits the allocation into slots of `gpus_per_run` GPUs. Each slot claims
the next unfinished run and trains it as a Slurm job step. When the run ends, the
slot claims another run, until none are left. A run uses part of one node or
whole nodes. For example, with 4 GPUs per node, `gpus_per_run: 2` puts two runs
on each node, and `gpus_per_run: 8` gives each run two whole nodes.

Each job step uses `srun --exact` and gets its share of the node's CPUs and
memory. Add cluster-specific `srun` options with `srun_args` in the `sweep`
table. Rendezvous ports start at `master_port`, which defaults to `29500`, and
increase by one per slot.

To try a sweep on one machine without Slurm, add `--local`. Each slot then gets
its own `CUDA_VISIBLE_DEVICES`:

```bash
python -m flock.training.experiments.scaling_law.sweep launch pilot.yaml --local
```

Add `--dry-run` to print the first wave's commands without training anything.

## Resume after a time limit

Submit the same command again. `launch` skips finished runs, retries failed ones,
and resumes interrupted runs from their latest checkpoint. A run's config must
not change once it has checkpoints. If it does, `launch` stops and asks you to
restore the sweep file or rename the run.

The batch script asks Slurm for `SIGTERM` two minutes before the time limit.
`launch` then stops its runs and leaves them claimed. A claim stays valid while
its Slurm job is still listed by `squeue`. After that, the next submission takes
the run over. You can also submit more than one job for the same sweep. Each job
only claims runs that no running job holds.

## Read the results

Each run has a directory in `<output_root>/<name>/runs/<run name>/`:

| File | Contents |
|---|---|
| `config.json` | The resolved run settings. |
| `train.log` | Output from every attempt, appended. |
| `checkpoints/` | Checkpoints and `token_checkpoints.json`. |
| `done.json` or `failed.json` | How the last attempt ended, with its exit code. |
| `claim.json` | Present while a launcher holds the run. |

Check progress from a login node:

```bash
python -m flock.training.experiments.scaling_law.sweep status pilot.yaml
```

W&B runs use the run name and are grouped under the sweep name.
