# Track an experiment

Log an experiment with the wandb SDK, find it in Cerulean's dashboard, and continue it after an interruption.

## Organizations, projects and runs

A **project** groups related experiments within an organization, such as `llm-pretrain`. A **run** is one experiment: its configuration, logged measurements, console output and uploaded files. The SDK creates the project on the first logged run if it does not already exist.

Use **config** for choices that describe the experiment, such as learning rate or batch size. Log **metrics** for measurements over time, such as loss at each training step. A run also has a **summary**, normally containing each metric's latest value, so you can compare experiments in a table without opening every curve.

A run's stable **ID** identifies it when resuming. Its display name and tags help you find it. The SDK's logging step indexes history; if you log less often than every training step, record your own training-step metric and [define the x-axis](logging-behavior#step-metrics).

## Log a run

[Install Flock](../installation), [join your organization](accounts), and run `uv run flock login`. Accept its offer to configure the wandb SDK. Cerulean's host and your default organization are then used automatically.

1. Save this as `train.py` in the repository root:

   ```python
   import math
   import wandb

   with wandb.init(
       project="llm-pretrain",
       name="gpt-small-baseline",
       config={"lr": 3e-4, "batch_size": 256},
       tags=["baseline"],
   ) as run:
       for step in range(20):
           run.log({"loss": 4.0 * math.exp(-step / 8) + 1.5}, step=step)
       print(f"Run ID: {run.id}")
   ```

2. Run it:

   ```bash
   uv run python train.py
   ```

3. Open [the dashboard](https://flock.cerulean.works), choose your default organization and `llm-pretrain`, then find `gpt-small-baseline`. Check that its loss curve contains the logged points and the run is finished.

A run belongs to the account whose wandb key created it. Use a different `entity` only when deliberately logging to another organization you belong to; pass that organization's ID, rather than its display name.

## Resume or sync a run

For an interrupted experiment, keep its organization, project and printed run ID. Restore your model, optimizer and data state in the training code, then resume SDK logging with that ID:

```python
import wandb

run = wandb.init(project="llm-pretrain", id="9fc2v9ee", resume="must")
run.log({"loss": 1.7})
run.finish()
```

Replace the example ID with yours and use the original organization. `resume="must"` verifies that the run already exists. Open that run and check that history continues there. Resuming SDK logging does not restore training state for you; [Logging behavior](logging-behavior#resuming-runs) describes the other resume modes.

To log while disconnected, run the script offline:

```bash
WANDB_MODE=offline uv run python train.py
```

When connectivity returns, sync the actual offline directory printed by wandb:

```bash
uv run wandb sync wandb/offline-run-20261030_120000-9fc2v9ee
```

Use your directory in place of the example. Check the dashboard for the uploaded run. Sync uses the SDK's configured Flock host and wandb key.

## Configure a shared machine

If a process needs its own logging configuration, inject a wandb key through its secret store as `WANDB_API_KEY`, and set:

```bash
export WANDB_BASE_URL=https://flock-api.cerulean.works
```

This gives the process an explicit destination without relying on the machine's SDK settings. A CLI `FLOCK_TOKEN` cannot replace `WANDB_API_KEY`; the [credential overview](../cli/sign-in#what-your-login-does) explains their roles.

[Explore experiments](explore-runs) covers dashboard comparisons, logs and files. Use [Logging behavior](logging-behavior) and [wandb compatibility](wandb-compatibility) for supported records, numeric edge cases and run-state rules.
