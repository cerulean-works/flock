# Managing projects and runs

Runs are created only by the `wandb` SDK, and their config, summary, history and logs only change through it. Everything else is managed through the HTTP API: creating, renaming and deleting projects, and editing, moving and deleting runs. This page walks through each operation on the project `flock/llm-pretrain`.

Members create projects and change the runs they logged. Updating or deleting projects, and changing other people's runs, takes an org admin. See [Permissions](permissions).

The examples use `curl` with your API key or access token in `$TOKEN` and Flock's URL in `$FLOCK`. Every endpoint is described in [Projects and runs](api/projects).

## Projects

`wandb.init` creates a project the first time a run is logged to it. To create one ahead of time, for example to give it a description:

```bash
curl -s --json '{"entity": "flock", "name": "llm-finetune", "description": "Instruction tuning of the pretrained models"}' -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/projects
```

It answers 409 if the project exists. Names given through the API are 1 to 128 characters, with no spaces around them, no control characters and none of `/ \ # ? % :`. Other names answer 422.

`GET /api/v1/projects` lists the projects of your organizations, empty ones included, with their run counts. Add `?entity=flock` to list one organization.

### Renaming a project

An org admin renames a project, or changes its description, with `PATCH`:

```bash
curl -s -X PATCH --json '{"name": "llm-pretrain-v1"}' -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/projects/flock/llm-pretrain
```

Fields you leave out don't change, and `"description": null` clears the description. A rename is refused with 409 while the project has active runs, because their clients address them by the old name. Wait for them to finish, or for crash detection to mark them `crashed`.

After a rename, the old name is free. A client that logs or resumes at the old path creates a new project there. Dashboards that stream the project get a `project` event and their stream ends.

### Deleting a project

```bash
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/projects/flock/llm-pretrain
```

This deletes the project with all its runs, their history, logs and files. While the project has active runs, it answers 409 unless you add `?force=true`.

> [!CAUTION]
> Deletes are permanent. There is no trash and no undo: restoring a deleted run or project takes a database backup.

## Editing runs

You can edit these attributes of a run: `display_name`, `notes`, `tags`, `group`, `job_type` and `description`, and correct its GPUs with `gpu_type` and `gpu_count` (see [Correcting the GPUs](home-and-budgets#correcting-the-gpus)). Patch one run by its id:

```bash
curl -s -X PATCH --json '{"display_name": "gpt-small-baseline", "tags": ["baseline", "v1"], "notes": null}' -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/projects/flock/llm-pretrain/runs/9fc2v9ee
```

Only the fields you give change, and `null` clears one. The response is the updated run. Run ids never change.

To edit many runs at once, select them and say what to change. This tags every run with a learning rate below `1e-3` and moves them into a group:

```bash
curl -s --json '{"filter": {"config.lr": {"$lt": 1e-3}}, "set": {"group": "low-lr"}, "add_tags": ["low-lr"]}' -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/projects/flock/llm-pretrain/runs/update
```

```json
{"ids":["9fc2v9ee"],"count":1}
```

`set` takes the same attributes as a patch. `add_tags` and `remove_tags` change each run's tags without replacing them.

Edits and the SDK change the same attributes, and the last write wins. If a running client later sends tags or notes, they replace your edit.

## Selecting runs

The bulk endpoints (`runs/update`, `runs/delete` and `runs/move`) take a selection in their body:

| Field | Selects |
|---|---|
| `runs` | These run ids. When given, the other fields are ignored. |
| `filter` | The runs matching a filter, in the [filter syntax](api/queries#filters). |
| `running` | With `true`, the active runs (combined with `filter` if both are given). |
| `all` | With `true`, every run in the project, unless `filter` or `running` narrows the selection. |

A body that selects nothing answers 400, so a mistake never selects every run. An unknown id in `runs` answers 404 and lists it in `ids`.

Add `"dry_run": true` to see which runs an operation would change, without changing them:

```bash
curl -s --json '{"filter": {"state": "crashed"}, "dry_run": true}' -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/projects/flock/llm-pretrain/runs/delete
```

```json
{"ids":["e97s97iv","k3m9x2pq"],"count":2,"dry_run":true}
```

Bulk operations are all or nothing. If one selected run can't be changed, the request fails, lists the run ids that are in the way in `ids`, and changes nothing:
- **403**: a member selected runs that someone else logged. Members who want only their own runs add `{"owner": <their user id>}` to the filter. `GET /api/v1/me` returns that id.
- **409**: a run is active, or the target project of a move already has a run with the same id.

## Deleting runs

Delete one run:

```bash
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/projects/flock/llm-pretrain/runs/9fc2v9ee
```

Or delete a selection with `POST .../runs/delete`. A delete removes the run with its history, system metrics, logs and files.

Deleting an active run answers 409 unless you add `force`: `?force=true` on a single delete, or `"force": true` in the body. A run's client doesn't learn that its run was deleted. It keeps logging and finishes normally, and Flock drops what it sends, so the run stays deleted. Until that client has been quiet for `FLOCK_CRASH_TIMEOUT`, Flock also drops any new run logged with the same id at the same path.

## Moving runs

Move finished runs to another project of the same organization:

```bash
curl -s --json '{"runs": ["9fc2v9ee"], "to": "llm-pretrain-archive", "create": true}' -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/projects/flock/llm-pretrain/runs/move
```

- `to` names the target project. Without `"create": true`, a target that doesn't exist answers 404.
- A moved run keeps everything it logged, and its owner. In the target project it sorts by its creation time, not as the newest run.
- Active runs can't move (409), and neither can runs whose id the target already uses (409).
- Runs can't move to another organization.

Dashboards that stream the source project get a `deleted` event with `moved_to`, and those that stream the target get a `created` event. A client that later resumes the run at its old path with `resume="allow"` starts a new run there.
