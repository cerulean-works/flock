# Importing from W&B

When you create an organization, Flock can copy every project and run of a W&B entity into it, so your team keeps its history when it moves to Flock. This page covers what you need, how to start an import, what it copies and how to resume one.

The examples use `curl` with a super admin's access token in `$TOKEN` and Flock's URL in `$FLOCK`, as in the [Quickstart](quickstart). The endpoints are listed in [Organizations and invites](api/orgs#imports).

## What you need

- **A super admin account** in Flock. Only super admins create organizations and start imports.
- **A W&B API key** that can read the entity. Get one at `https://wandb.ai/authorize`. Flock uses it for the import only and never stores it.
- **The entity's name**: the W&B team or user whose projects you want, as in `wandb.ai/<entity>`. The organization can have a different name.

## Starting an import

1. Create the organization with a `wandb` field:
   ```bash
   curl -s --json '{"name": "acme", "wandb": {"entity": "acme-ml", "api_key": "'"$WANDB_API_KEY"'"}}' -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/orgs
   ```
   Flock first checks that W&B accepts the key and that the entity exists. If either check fails, the request is refused and no organization is created. Otherwise it answers 201, with the import's progress in `import`.
2. Follow the import while it runs:
   ```bash
   curl -s -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/orgs/acme/import
   ```
   `state` is `running` until it ends, and `runs_done` counts the runs copied so far.
3. Check that it worked: `state` is `finished` and `runs_failed` is 0. The projects appear in the organization as they are copied.

To read from W&B Dedicated Cloud or a self-managed W&B server, add its API URL as `base_url` in `wandb`, or set [`FLOCK_WANDB_BASE_URL`](configuration) for every import.

To import into an organization that already exists, send the same `wandb` fields to `POST /api/v1/orgs/acme/import`.

## What is copied

Each W&B project becomes a project of the same name, with its description and creation time. Each run keeps its id, so links and scripts that use run ids still work in Flock.

| Copied | Not copied |
|---|---|
| Display name, config, summary, tags, notes, group, job type, sweep name, commit | Media, tables, model checkpoints and other run files |
| Every row of logged metrics | Artifacts |
| System metrics (CPU, GPU, memory) | Reports, sweeps and W&B workspaces |
| The console log (`output.log`) | |
| `wandb-metadata.json`, `config.yaml` and `requirements.txt`, up to 64 MiB each | |

**Times.** A run keeps the time it was created and the time of its last heartbeat. Its rows in the run table and its entries in the [home feed](home-and-budgets) appear at those times, not at the time of the import.

**States.** Imported runs are never active in Flock, because their clients log to W&B:

| State in W&B | State in Flock |
|---|---|
| `finished` | `finished` |
| `failed`, `killed` | `failed` |
| `crashed` | `crashed` |
| `running`, or any other | `crashed` |

**Owners.** A run belongs to the Flock user whose email matches its W&B author. W&B shows the emails of an entity's members only to some keys, such as a team admin's. Runs whose author has no matching account, or whose email W&B doesn't show, belong to the super admin who started the import. Invite your team before importing so their runs stay theirs.

**GPU costs.** Flock reads each run's GPUs from its `wandb-metadata.json`, and counts the time from its creation to its last heartbeat as [run time](home-and-budgets#how-run-time-is-counted). Imported runs therefore count towards the monthly spend of the months they ran in.

## When runs fail or an import stops

- **A run that can't be copied** is counted in `runs_failed` and skipped, and `error` names the last one. The import goes on with the other runs.
- **An import that can't go on**, for example because W&B now rejects the key, ends with `state` set to `failed` and the reason in `error`.
- **Restarting Flock** during an import stops it, and its `state` becomes `interrupted`. Flock doesn't store the API key, so it can't continue on its own.
- **Cancelling** with `DELETE /api/v1/orgs/acme/import` stops it, and its `state` becomes `cancelled`.

In each case, the runs copied so far stay. To resume, start the import again with the key:

```bash
curl -s --json '{"entity": "acme-ml", "api_key": "'"$WANDB_API_KEY"'"}' -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/orgs/acme/import
```

It skips every run the organization already has, by project and run id, and copies the rest. `runs_skipped` counts the runs it skipped. Running an import again also copies runs that were added in W&B since the last one.

> [!NOTE]
> A run that was copied isn't updated again. To copy a newer version of a run, delete it in Flock and start the import again.
