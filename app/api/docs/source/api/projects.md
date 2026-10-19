# Projects and runs

Endpoints for projects, single runs, bulk changes to runs, and run logs and files. Paths are relative to `/api/v1`, and `.../` stands for `/projects/{entity}/{project}/`. Projects and runs of organizations you don't belong to answer 404. For a walkthrough, see [Managing projects and runs](../managing-runs). Queries over the run table and curves are in [Queries and filters](queries).

## Projects[[projects]]

A project is returned as:

```json
{"entity": "flock", "name": "llm-pretrain", "description": null, "created_at": 1792282202.83, "updated_at": 1792282202.83, "run_count": 1, "active_run_count": 0, "column_count": 21}
```

| Field | Meaning |
|---|---|
| `entity` | The organization's name. |
| `name` | The project's name. |
| `description` | Free text, or `null`. |
| `created_at`, `updated_at` | When the project was created, and when its name or description last changed. |
| `run_count`, `active_run_count` | How many runs it has, and how many of them are active. |
| `column_count` | How many columns its run table has. |

| Endpoint | Who | Meaning |
|---|---|---|
| `GET /projects?entity=` | members | `{"projects": [...]}`: the projects of your organizations, empty ones included. `entity` keeps one organization's. |
| `POST /projects` | members | Creates an empty project from `{"entity", "name", "description"}`. `description` is optional. Answers 201, or 409 if it exists. |
| `GET /projects/{entity}/{project}` | members | One project. |
| `PATCH /projects/{entity}/{project}` | org admins | Changes `name` and `description`. Fields you leave out don't change, and `"description": null` clears it. Returns the project. |
| `DELETE /projects/{entity}/{project}` | org admins | Deletes the project with all its runs, their history, logs and files. Answers 204. |

Project names given here are 1 to 128 characters, with no spaces around them, no control characters and none of `/ \ # ? % :`. Other names answer 422. Names that arrive from the `wandb` SDK aren't checked.

**Renaming** is refused with 409 while the project has active runs, which `ids` lists. A name another project of the organization uses answers 409. Dashboards streaming the project get a `project` event with `renamed_to`, and their streams end.

**Deleting** is refused with 409 while the project has active runs, listed in `ids`, unless you add `?force=true`. Dashboards streaming the project get a `project` event with `"deleted": true`.

## Runs[[runs]]

### Run attributes[[run-attributes]]

Every run has these attributes. They are also columns of the [run table](queries#the-run-table).

| Attribute | Meaning | Editable |
|---|---|---|
| `name` | The run id, such as `9fc2v9ee`. It never changes. | no |
| `display_name` | The run's display name (wandb's `run.name`). | yes |
| `state` | `running`, `preempting`, `finished`, `failed` or `crashed`. See [Run states](../logging#run-states). | no |
| `created_at`, `updated_at` | When the run was created, and when it last changed. | no |
| `heartbeat_at` | When its client last sent data. | no |
| `tags` | A list of strings. | yes |
| `group`, `job_type` | wandb's `group` and `job_type`. | yes |
| `notes`, `description` | Free text. | yes |
| `host`, `program`, `commit` | The machine, script and git commit the run came from, as the SDK reports them. | no |
| `sweep` | The wandb sweep, if the SDK sent one. | no |
| `exitcode` | The exit code, once the run ended. | no |
| `owner` | The id of the user who logged the run, or `null` if that account was deleted or the run predates accounts. | no |
| `gpu_type`, `gpu_count` | The run's GPUs, such as `H200` and `8`: what you set, else what was read from its `wandb-metadata.json`. `gpu_count` is `null` until known, and `0` without a GPU. See [GPUs on runs](home#run-gpus). | yes |
| `gpu_hours` | `gpu_count` times the hours the run was alive, or `null` while `gpu_count` is unknown. | no |

### `GET .../runs/{run}`[[get-run]]

Returns a run's attributes and everything about it except its curves:

| Field | Meaning |
|---|---|
| `id`, `entity`, `project` | The run id, its organization and its project. |
| the [attributes](#run-attributes) | One field each. |
| `config` | The config, with nested values as nested objects. wandb's internal `_wandb` key is left out. |
| `summary` | The summary, as the SDK sent it, without `_wandb`. |
| `metric_defs` | Its metric definitions from `run.define_metric`, as the [snapshot](dashboard#metric-definitions) describes them. |
| `history_keys`, `system_keys` | The names of its logged metrics and of its system metrics, sorted. |
| `history_line_count`, `system_line_count`, `log_line_count` | How many history lines, system samples and console lines it has. |
| `gpu_detected`, `runtime`, `cost` | The GPUs read from its metadata, the seconds it was alive, and its cost at the organization's current prices. See [GPUs on runs](home#run-gpus). |

### `PATCH .../runs/{run}`[[patch-run]]

Edits the editable attributes: `display_name`, `notes`, `tags`, `group`, `job_type`, `description`, `gpu_type` and `gpu_count`. Only the fields you give change, and `null` clears one (for `gpu_type` and `gpu_count`, `null` goes back to the detected value). Other fields answer 422. Returns the run as [`GET .../runs/{run}`](#get-run) does.

Members can edit only runs they logged. Other runs answer 403, with the run's id in `ids`.

### `DELETE .../runs/{run}`[[delete-run]]

Deletes the run with its history, system metrics, logs and files. Answers 204. An active run answers 409 unless you add `?force=true`. Members can delete only runs they logged.

## Bulk operations[[bulk-operations]]

Three endpoints change many runs at once. Each takes a selection in its body:

| Field | Type | Meaning |
|---|---|---|
| `runs` | list of run ids | These runs. When given, `filter`, `running` and `all` are ignored. An unknown id answers 404 and is listed in `ids`. |
| `filter` | filter | The runs matching a [filter](queries#filters). |
| `running` | boolean | Only the active runs. With `filter`, the active runs that match it. |
| `all` | boolean | Every run in the project. `filter` and `running` still narrow it. |
| `dry_run` | boolean | Report the runs that would change, and change nothing. |

A body that sets none of `runs`, `filter`, `running` and `all` answers 400. Unknown fields answer 422.

The response lists the runs that changed:

```json
{"ids": ["9fc2v9ee", "e97s97iv"], "count": 2}
```

With `dry_run`, it lists the runs that would change, and adds `"dry_run": true`.

The operations are all or nothing. If a member selects runs they didn't log, the request answers 403, lists those runs in `ids`, and changes nothing. Conflicts answer 409 the same way.

### `POST .../runs/update`[[update-runs]]

| Field | Meaning |
|---|---|
| `set` | Attributes to set on every selected run: the same fields as [`PATCH .../runs/{run}`](#patch-run). `null` clears one. |
| `add_tags` | Tags to add to each run. |
| `remove_tags` | Tags to remove from each run. |

Tags in `set.tags` replace each run's tags first. `add_tags` and `remove_tags` then apply.

### `POST .../runs/delete`[[delete-runs]]

Deletes the selected runs. With any of them active, it answers 409 listing them, unless the body has `"force": true`.

### `POST .../runs/move`[[move-runs]]

| Field | Meaning |
|---|---|
| `to` | The target project, in the same organization. |
| `create` | With `true`, creates the target project if it doesn't exist. Otherwise a missing target answers 404. |

Moved runs keep everything they logged, and their owner. Active runs answer 409, and so do runs whose id the target project already uses. Moving runs to their own project changes nothing. Dashboards streaming the source get a `deleted` event with `moved_to`, and those streaming the target get a `created` event per run.

## Run logs[[run-logs]]

`GET .../runs/{run}/logs?offset=0&limit=1000` returns the run's console output, as the SDK captured it:

```json
{"total": 2, "lines": [{"line": 0, "text": "..."}, {"line": 1, "text": "..."}]}
```

| Parameter | Default | Meaning |
|---|---|---|
| `offset` | `0` | First line number to return. |
| `limit` | `1000` | Maximum number of lines, up to 100,000. |

`total` is the run's number of console lines.

## Run files[[run-files]]

| Endpoint | Meaning |
|---|---|
| `GET .../runs/{run}/files` | The files the run uploaded, as `{"files": [...]}` sorted by name. Each has `name`, `size` in bytes and `updated_at`. |
| `GET .../runs/{run}/files/{name}` | The file's contents. `name` may contain `/`, as in `media/images/sample.png`. A missing file answers 404. |

The SDK uploads files such as `config.yaml`, `requirements.txt`, `wandb-metadata.json` and `wandb-summary.json`. Files can't be deleted one at a time: they go when the run is deleted.
