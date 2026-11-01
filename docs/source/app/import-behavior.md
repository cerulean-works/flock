# W&B import behavior

For the import procedure, see [Import W&B runs](import-wandb).

## What is copied

Each W&B project becomes a project of the same name, with its creation time. Project descriptions aren’t copied. Each run keeps its id, so links and scripts that use run ids still work in Flock.

| Copied | Not copied |
|---|---|
| Display name, config, summary, tags, notes, group, job type, sweep name, commit | Media, tables, model checkpoints and other run files |
| Every row of logged metrics | Artifacts |
| System metrics (CPU, GPU, memory) | Reports, sweeps and W&B workspaces |
| The console log (`output.log`) | |
| `wandb-metadata.json`, `config.yaml` and `requirements.txt`, up to 64 MiB each | |

**Times.** A run keeps the time it was created and the time of its last heartbeat. Its rows in the run table and its entries in the [home feed](budget-behavior) appear at those times, not at the time of the import.

**States.** Imported runs are never active in Flock, because their clients log to W&B:

| State in W&B | State in Flock |
|---|---|
| `finished` | `finished` |
| `failed`, `killed` | `failed` |
| `crashed` | `crashed` |
| `running`, or any other | `crashed` |

**Owners.** A run belongs to the Flock user whose email matches its W&B author. W&B shows the emails of an entity's members only to some keys, such as a team admin's. Runs whose author has no matching account, or whose email W&B doesn't show, belong to the super admin who started the import. Invite your team before importing so their runs stay theirs.

**GPU costs.** Flock reads each run's GPUs from its `wandb-metadata.json`, and counts the time from its creation to its last heartbeat as [run time](budget-behavior#how-run-time-is-counted). Imported runs therefore count towards the monthly spend of the months they ran in.


## When runs fail or an import stops

- **A run that can't be copied** is counted in `runs_failed` and skipped, and `error` names the last one. The import goes on with the other runs.
- **A file that can't be downloaded** is left out, and the run is copied without it. `error` names it, as `<project>/<run>: skipped <file>: ...`.
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
