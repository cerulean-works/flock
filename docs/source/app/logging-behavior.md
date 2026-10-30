# Logging behavior

Flock implements the server side of the wandb protocol, so your training code uses the `wandb` SDK without changes. This page covers how to connect the SDK, what Flock records, and how runs behave when they resume, fail or crash.

## Connect the SDK

Use [Connect the wandb SDK](../cli/sign-in#wandb-sdk) for setup or [Log your first run](log-runs#log-a-run) for a complete example. The SDK uses a wandb key and the public API address. It does not accept a CLI token as its wandb key.

Every run is owned by the user whose wandb key created it. Machine-wide SDK login stores a key in `~/.netrc` and the host in wandb settings. `WANDB_BASE_URL` and `WANDB_API_KEY` configure a single process instead.

## Choose the organization and project

```python
run = wandb.init(entity="flock", project="llm-pretrain", name="gpt-small-lr3e-4")
```

- **`entity`** names the organization. Without it, the run goes to your default organization: the first organization you joined, unless you chose another one with [`PATCH /api/v1/me`](api/auth#update-me). You must be a member of the organization. Logging never creates organizations, so an unknown or foreign one fails with `organization <name> not found`.
- **`project`** names the project. Flock creates it if it doesn't exist yet. Runs logged without a project land in `uncategorized`.
- **`name`** sets the run's display name. Without it, Flock generates one in wandb's `adjective-noun-N` style.
- **`id`** sets the run id. Without it, the SDK generates one, such as `9fc2v9ee`. The run id never changes.

Project names that arrive from the SDK are accepted as they are. Names given through the HTTP API follow stricter rules (see [Managing projects and runs](run-management#projects)).

## What Flock records

| In your code | What Flock stores |
|---|---|
| `config=` in `wandb.init`, `run.config.update(...)` | The run's config. Nested dicts become dotted columns, such as `config.model.layers`. |
| `name`, `tags`, `notes`, `group`, `job_type` | The run attributes `display_name`, `tags`, `notes`, `group` and `job_type`. |
| `run.log({...})` | One history line per step. Numbers become curves, and booleans become curves of 0 and 1. Nested dicts are flattened: `{"train": {"acc": 0.9}}` becomes the metric `train.acc`. |
| `run.summary[...]`, `run.summary.update(...)` | The summary. By default it holds the last logged value of each metric. Its keys become `summary.*` columns. |
| `run.define_metric("val_loss", summary="min")` | The summary entry `{"val_loss": {"min": ...}}`, which becomes the column `summary.val_loss.min`. |
| `run.define_metric("*", step_metric="train/global_step")` | The metric's step metric: dashboards plot it against `train/global_step` by default, as wandb does. See [Step metrics](#step-metrics). |
| `run.define_metric("debug", hidden=True)` | A hidden metric: dashboards give it no chart of its own. |
| Console output | Log lines, read with [`GET .../runs/{run}/logs`](api/projects#run-logs). |
| Files the SDK uploads | Run files such as `config.yaml`, `requirements.txt`, `wandb-metadata.json` and `wandb-summary.json`, read with [`GET .../runs/{run}/files`](api/projects#run-files). |
| The SDK's system monitor | System metrics such as `system.cpu` and `system.gpu.0.gpu`. See [System metrics](#system-metrics). |
| The run's exit | The final state and exit code. See [Run states](#run-states). |

A few details matter when you read the data back:
- **Only numbers are plotted.** Strings and media values, such as `wandb.Histogram`, are kept in the history lines and in the summary, but they don't become curves.
- **`NaN` and infinity are kept.** They come back as `null` in curves, which charts draw as gaps.
- **Any metric can be the x-axis.** The query API returns curves against `_step` by default. You can plot against `_runtime`, `_timestamp` or a metric you log, such as `epoch`. Passing `step=` to `run.log` sets `_step` explicitly.
- **Run attributes are filterable.** Config, summary and attributes all become columns of the [run table](api/queries#the-run-table).

## Step metrics

`_step` counts `run.log` calls, so it's not always the step you think of. Trainers that log every few steps, such as the Hugging Face `Trainer`, log their own step and tell wandb to plot against it:

```python
run.define_metric("train/global_step")
run.define_metric("*", step_metric="train/global_step", step_sync=True)
for step in range(16, 1601, 16):
    run.log({"train/global_step": step, "train/loss": loss})
```

Flock reads these definitions from the run's config, where the SDK records them. Dashboards then plot each metric against its step metric, here at x = 16, 32, 48 and so on, rather than at `_step` 0, 1, 2. The rules are wandb's:
- A metric's own definition takes precedence over globs. A glob is a name ending in `*`, and it matches every key that starts with what comes before the `*`.
- A metric without a step metric, such as `train/global_step` above, is plotted against `_step`.
- A step metric still gets a chart of its own. Pass `hidden=True` to `define_metric` to leave a metric out of the charts.

A chart of several runs has one x-axis. The first run on the page that logged the metric and defines it decides its step metric. Runs that never logged the metric don't decide, even if they have a `*` glob. If some run that logged the metric didn't log that step metric, the chart uses `_step` instead, so every run's line still shows.

The [snapshot](api/dashboard#metric-definitions) and the [run detail](api/projects#get-run) carry each run's definitions, and the snapshot carries what a dashboard needs to switch the x-axis without fetching again. The [query endpoints](api/queries) still return curves against `_step` unless you pass `x`.

## System metrics

The SDK samples the machine every 15 seconds by default, in a stream separate from what `run.log` records. The samples hold CPU, memory, disk, network and GPU readings, with keys such as `system.cpu`, `system.memory_percent` and `system.gpu.0.gpu`. They carry `_runtime` and `_timestamp` but no `_step`, so you plot them against time.

To sample more often, set the SDK's `x_stats_sampling_interval` setting, in seconds:

```python
run = wandb.init(project="llm-pretrain", settings=wandb.Settings(x_stats_sampling_interval=5))
```

Read them with `stream=system` on the [curve endpoints](api/queries#curves).

## Run states

| State | Meaning |
|---|---|
| `running` | The client is logging. |
| `preempting` | The client reported that it is being preempted. |
| `finished` | The run ended with exit code 0. |
| `failed` | The run ended with another exit code. An uncaught exception ends it with exit code 1, and `run.finish(exit_code=2)` with exit code 2. |
| `crashed` | Flock heard nothing from the client for `FLOCK_CRASH_TIMEOUT` seconds (600 by default). |

`running` and `preempting` runs are **active**. While a run is active, you can't move it or rename its project, and deleting it needs `force`. Its client addresses it by `entity/project/run` in every request, so moving it would split it in two.

A crashed run isn't final. If its client sends data again, for example after a network outage, the run returns to `running`. [Crash detection](operations#crash-detection) explains the timing.

## Resuming runs

| Resume mode | Behavior |
|---|---|
| `allow` | Continues the run if it exists, and creates it otherwise. |
| `must` | Fails if the run does not exist. |

A resumed run keeps its config and summary, and its history continues after the last stored line. Members can resume only runs they own; organization admins can resume any run in their organization.

A moved or deleted run no longer exists at its old path. Resuming there with `allow` starts a new run, while `must` fails. See [Resume a run](log-runs#resume-or-sync-a-run) for the procedure and [Run mutation rules](run-management) for deleted-run handling.

## Offline runs

Offline runs remain on disk until `wandb sync` uploads them. Sync needs the same host and wandb key as a live run. Syncing into a project that no longer exists creates it again. See [Resume or sync a run](log-runs#resume-or-sync-a-run) for commands.

## Unsupported features

Flock doesn't implement artifacts, sweeps, reports or the `wandb.Api()` client. SDK calls that need them fail. Use Flock's [query API](api/queries) instead of `wandb.Api()`: its filters use the same syntax. [wandb compatibility](wandb-compatibility) has the full list.
