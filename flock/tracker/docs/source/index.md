# Introduction

The flock tracker is an experiment tracking server that speaks the wandb protocol. Your training code logs with the regular `wandb` Python SDK, the tracker stores everything in PostgreSQL, and dashboards read curves and filtered run tables from its low-latency API.

## What it does

- **Takes logs from the unmodified `wandb` SDK.** `wandb.init`, `run.log`, `run.summary`, `run.config`, resumed runs, offline runs and system metrics all work. You point the SDK at the tracker with a URL and an API key. See [Logging from training code](logging).
- **Answers dashboards in milliseconds.** Curves of the newest 50 runs come back in about 2 ms, and a filter with a sort over 10,000 runs and 1,500 columns in about 3 ms. See [How it works](how-it-works#performance).
- **Manages runs after the fact.** You can rename projects, and edit, move and delete runs, one at a time or by filter. See [Managing projects and runs](managing-runs).
- **Has accounts and organizations.** People sign in with a password or with Google, join organizations through invitation links, and log with their own API keys. See [Accounts, organizations and roles](accounts).

The tracker has no web interface of its own. A dashboard, such as flock-ui, reads it through the HTTP API. See [Building a dashboard](dashboards). It also doesn't implement wandb artifacts, sweeps or reports: [wandb compatibility](wandb-compatibility) lists what is supported.

## Concepts

| Term | Meaning |
|---|---|
| organization | A wandb entity, such as `flock`. `wandb.init(entity="flock")` logs into the organization `flock`. An organization owns projects and has members. |
| project | A named set of runs in an organization, such as `flock/llm-pretrain`. |
| run | What one `wandb.init` records: config, summary, history, console output and files. A run is identified by its run id (wandb's `run.id`, for example `9fc2v9ee`) and also has a display name (wandb's `run.name`). |
| member, org admin, super admin | The roles a user can have. Members log runs and change their own. Org admins manage an organization. Super admins manage the whole server. See [Roles](accounts#roles). |
| API key | A per-user secret that the `wandb` SDK and scripts use instead of a password. |

These docs use one example throughout: an organization `flock`, which the tracker creates for its first user, with a project `llm-pretrain`.

## Where to go next

- Run a tracker and log a first run: [Quickstart](quickstart).
- Put a tracker into production: [Deploying and operating](deploying).
- Look up an endpoint: [HTTP API overview](api/index).
