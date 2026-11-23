# Projects and runs

Endpoints for projects, single runs, bulk changes to runs, and run logs and files. Paths are relative to `/api/v1`, and `.../` stands for `/projects/{entity}/{project}/`. Projects and runs of organizations you don't belong to answer 404. For a walkthrough, see [Managing projects and runs](../run-management). Queries over the run table and curves are in [Queries and filters](queries).

## Projects[[projects]]

A project is returned as:

```json
{"id": 412, "entity": "flock", "name": "llm-pretrain", "description": null, "created_at": 1792282202.83, "updated_at": 1792282202.83, "run_count": 1, "active_run_count": 0, "column_count": 21, "visibility": "private", "created_by": 3}
```

| Field | Meaning |
|---|---|
| `id` | The project's number. Its datasets and models are stored under `-projects/{id}/` of `flock-datasets` and `flock-models`. |
| `entity` | The organization's ID. |
| `name` | The project's name. |
| `description` | Free text, or `null`. |
| `created_at`, `updated_at` | When the project was created, and when its name or description last changed. |
| `run_count`, `active_run_count` | How many runs it has, and how many of them are active. |
| `column_count` | How many columns its run table has. |
| `visibility` | `private`, for the organization's members only, or `public`, readable by anyone with its URL. See [Share projects and datasets publicly](../share-publicly). |
| `created_by` | The ID of the user who created it, or `null` if unknown. Its creator may change its visibility. |
| `access` | `GET` and `PATCH` of one project only: what you can do with it. `admin`, `member`, or `public` (read only, as an outsider). |

| Endpoint | Who | Meaning |
|---|---|---|
| `GET /projects?entity=` | anyone | `{"projects": [...]}`: the projects of your organizations, empty ones included. `entity` keeps one organization's, and gives outsiders that organization's public projects. Without `entity`, outsiders get an empty list. |
| `POST /projects` | members | Creates an empty project from `{"entity", "name", "description"}`. `description` is optional. Answers 201, or 409 if it exists. |
| `GET /projects/{entity}/{project}` | members, anyone if public | One project. |
| `GET /projects/{entity}/{project}/owners` | members, anyone if public | `{"owners": [{"id", "name", "email", "avatar_url"}]}`: the users who logged its runs, which the run table's `owner` column names by `id`. Outsiders get them without `email`. |
| `PATCH /projects/{entity}/{project}` | org admins, its creator for `visibility` | Changes `name`, `description` and `visibility`. Fields you leave out don't change, and `"description": null` clears it. Returns the project. Its creator, if a member, may send only `visibility`, and other fields answer 403. |
| `DELETE /projects/{entity}/{project}` | org admins | Deletes the project with all its runs, their history, logs and files, and its datasets and models, for good, without the trash. Answers 204. |
| `GET /projects/{entity}/{project}/storage` | members | What deleting the project deletes with it: `{"datasets", "models", "bytes"}`, the number of its datasets, the number of its models, and the bytes of both together. |

Project names given here are 1 to 128 characters, with no spaces around them, no control characters and none of `/ \ # ? % :`. Other names answer 422. Names that arrive from the `wandb` SDK aren't checked.

**Renaming** is refused with 409 while the project has active runs, which `ids` lists. A name another project of the organization uses answers 409. Dashboards streaming the project get a `project` event with `renamed_to`, and their streams end.

**Changing visibility** records who changed it and when. Dashboards streaming the project get a `project` event with the new `visibility`. When it becomes `private`, outsiders' streams end after that event, and members' streams carry on. Moving runs into a public project makes them public.

**Deleting** is refused with 409 while the project has active runs, listed in `ids`, unless you add `?force=true`. It's also refused with 409, even with `force`, while a fork or blend outside the project reads from one of its datasets. Delete those datasets or make them independent first. The project's own forks and blends are deleted with it. Dashboards streaming the project get a `project` event with `"deleted": true`.

## Runs[[runs]]

### Run attributes[[run-attributes]]

Every run has these attributes. They are also columns of the [run table](queries#the-run-table).

| Attribute | Meaning | Editable |
|---|---|---|
| `name` | The run id, such as `9fc2v9ee`. It never changes. | no |
| `display_name` | The run's display name (wandb's `run.name`). | yes |
| `state` | `running`, `preempting`, `finished`, `failed` or `crashed`. See [Run states](../logging-behavior#run-states). | no |
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
| `gpu_detected`, `runtime`, `cost` | The GPUs read from its metadata, the seconds it was alive, and its cost at the organization's current prices. See [GPUs on runs](home#run-gpus). `cost` is `null` for outsiders reading a public project. |
| `owner_user` | The owner as `{"id", "name", "email", "avatar_url"}`, or `null`. Outsiders get it without `email`. |

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

Files in a top-level `private/` folder, such as `private/predictions/exam.jsonl`, are for the organization's members only, whatever the project's visibility. On a public project, outsiders don't see them in the list, and reading one answers the same 404 as a missing file. A run that uploads such files keeps them members-only when its project is made public or the run is moved into a public project.

## Run models[[run-models]]

`GET .../runs/{run}/models` returns the model versions the run saved, in its own organization, as `save_to_hub` with `run` records them:

```json
{
  "made": [{"model": "flock/olmo2-1b", "number": 2, "step": 12000, "tags": ["checkpoint-12000"], "run": "flock/llm-pretrain/3k9x2", ...}]
}
```

`made` is sorted by model name, then number. A version has the fields of a version in the model routes (`model`, `number`, `digest`, `tags`, `primary`, `step`, `run`, `total_bytes`, `created_by`, `created_at` and the rest). Deleted versions and models aren't listed.

## Run groups[[groups]]

A group is a saved list of a project's runs, with the [filter](queries#filters) it was built with. Only you change its runs: runs the filter matches later are offered as *new matches*, never added for you. A run can be in several groups. A deleted run leaves its groups, and so does a run moved to another project. Members create and change groups. Outsiders on a public project can read them.

`{group}` is a group's `id`. An unknown one answers 404.

A group is returned as:

```json
{"id": 3, "name": "lr × batch_size", "color": "#2a78d6", "filter": {"group": "pretrain-1b"}, "run_count": 12, "new_count": 2, "best_key": null, "best_order": null, "best": {"run": "9fc2v9ee", "name": "pretrain-1b-lr3e-4", "value": 2.31, "std": 0.02, "n": 3}, "created_at": "2026-11-20T09:12:44.512+00:00", "loaded_at": "2026-11-22T16:03:10.008+00:00"}
```

| Field | Meaning |
|---|---|
| `filter` | The filter it was built with, or `{}`. It decides only which runs are offered as new matches. |
| `run_count` | How many runs it has. |
| `new_count` | How many new matches it has: runs the filter matches that are neither in the group nor dropped from it. A group without a filter has none. |
| `best_key`, `best_order` | The group's own "best by" column and direction (`asc`, lowest first, or `desc`), or `null` for the project's. |
| `best` | Its best run by that column, or `null` when no run has a finite value. With a repeat key, such as a `seed`, runs that differ only in it form one configuration. `value` is then the best configuration's mean, `std` its standard deviation, `n` its runs with a value, and `run` the best run in it. NaN, infinities and missing values are never best. |
| `created_at`, `loaded_at` | When it was created, and when runs were last loaded into it, as ISO 8601 times. |

| Endpoint | Who | Meaning |
|---|---|---|
| `GET .../groups` | members, anyone if public | `{"groups": [...], "ungrouped_count", "best_key", "best_order", "assistant"}`: the groups, oldest first, the number of runs in no group, the project's best by column and direction, and whether the [assistant](#group-assistant) answers you. Until someone sets the best by column, it's the first of `summary.val/loss`, `summary.eval/loss` and `summary.loss` the project has, lowest first, or `null`. |
| `PUT .../groups/settings` | members | Sets the project's best by column from `{"best_key": "summary.val/loss", "best_order": "asc"}`. A `null` `best_key` goes back to the default. Returns `{"best_key", "best_order"}`. |
| `POST .../groups` | members | Creates a group from `{"name", "filter", "runs"}`, and optionally `color`, `best_key`, `best_order`, `repeat_key`, `columns` and `view`. `runs` are its runs, by id. Runs the filter matches that aren't in `runs` are dropped, since they were left out on purpose. Answers 201 with the group as `GET .../groups/{group}` returns it, 409 if the project has a group of that name, and 404 listing unknown runs in `ids`. |
| `GET .../groups/{group}` | members, anyone if public | The group, with `runs` and `dropped` (run ids, oldest first), `added_by` (each run's `filter` or `hand`), `repeat_key` (the one set, else the one detected, or `null`), `columns` (the column kinds set by hand) and `view` (the charts' settings). |
| `PATCH .../groups/{group}` | members | Changes any of `name`, `color`, `filter`, `best_key`, `best_order`, `repeat_key`, `columns` and `view`, and `add`s, `drop`s or `restore`s runs, by id. A restored run is no longer dropped, and is offered again if it matches. `"loaded": true` moves `loaded_at` to now. A new filter never removes a run. Returns the group as `GET` does. |
| `DELETE .../groups/{group}` | members | Deletes the group. Its runs stay. Answers 204. |
| `POST .../groups/{group}/runs/query` | members, anyone if public | The [run table query](queries#the-run-table) over the group's runs. Its `filter` narrows them further. |
| `GET .../groups/{group}/new` | members, anyone if public | `{"runs": [{"id", "name", "state", "created_at"}]}`: its new matches, newest first. |
| `GET .../groups/{group}/columns` | members, anyone if public | `{"columns": [{"key", "kind", "distinct", "overridden"}]}`: each `config.*` key of its runs, with its kind, its number of distinct values, and whether its kind was set by hand. |
| `GET .../groups/{group}/primary` | members, anyone if public | `{"keys": [...], "source"}`: the few columns (at most 6) its table shows first, those that matter most for comparing its runs, always with its best by column. With the [assistant](#group-assistant) on, the model picks them (`"source": "model"`) from the `config.*` keys that vary between its runs and its summary metrics. Otherwise, or when OpenAI fails, they're the keys that vary and the best by column (`"source": "analysis"`). |
| `GET .../groups/compare` | members, anyone if public | Compare: each group's best run. See below. |
| `GET .../groups/suggestions` | members, anyone if public | Suggested groups. See below. |
| `POST .../groups/suggestions/dismiss` | members | Stops suggesting the suggestion with `{"signature"}`. Answers 204. |
| `POST .../groups/assist` | members | One turn of the [assistant](#group-assistant). |
| `POST .../groups/propose` | members | One turn of the [proposals chat](#group-proposals): the groups a model proposes for the project. |

Names are 1 to 128 characters after Flock trims the spaces around them, and colors are `#rrggbb`. A filter the query language refuses answers 422.

**Column kinds.** Each `config.*` key of a group's runs is one of four kinds. `varies` has 2 or more values, one of them shared by 2 or more runs, or is a number distinct in every run, such as a learning rate from a random search. A key missing from some runs is `varies` too. `repeat` is a key named `seed`, `*_seed`, `random_state` or `rng`. `same` has one value, in every run. `unique` is a string or list distinct in every run, such as a path. In a group of fewer than 4 runs, such strings are `varies`. `columns` overrides any of them, as `{"config.data_mix": "varies"}`.

### Compare[[groups-compare]]

`GET .../groups/compare` returns:

| Field | Meaning |
|---|---|
| `best_key`, `best_order` | The project's best by column and direction. |
| `rows` | A row per group, best first. Groups ranked by another column come last, and their row's `best_key` names it. |
| `ungrouped` | A row per run in no group, best first. |
| `ungrouped_count` | How many there are. |
| `keys` | The `config.*` keys whose values differ between the groups' best runs. Repeat keys, and keys distinct in every run of the project, are left out. |

A row is `{"group", "name", "color", "run", "run_name", "value", "std", "n", "runs", "values", "config", "summary", "best_key", "best_order"}`. For a run, `group` and `color` are `null`. `run` is the group's best run, `runs` its number of runs, `values` every member's value in the column, `config` the best run's config, and `summary` its finite `summary.*` numbers, W&B's own (`summary._step`...) left out.

### Suggestions[[groups-suggestions]]

`GET .../groups/suggestions` returns `{"suggestions": [{"signature", "source", "name", "filter", "runs"}]}`, from the runs in no group. `source` is `wandb_group` for runs sharing a W&B group, with the filter `{"group": name}`, then `config_diff` for 3 or more runs whose configs differ in at most 2 keys, with no filter. The `sweep` attribute isn't used. `signature` is the SHA-256 of the sorted run ids, in hex. A dismissed suggestion isn't offered again unless its runs change.

### The assistant[[group-assistant]]

`POST .../groups/assist` takes a request in words and the draft's current filters, as the web app's conditions, and returns an action:

```json
{"request": "only finished runs with adam", "filter": [{"key": "state", "op": "eq", "value": "running"}]}
```

```json
{"type": "filter", "conditions": [{"key": "config.optimizer", "op": "eq", "value": "adam", "probability": 0.93, "alternatives": []}], "runs": [], "best": null}
```

`type` is `filter`, `add_runs`, `drop_runs`, `set_best` or `none`. `conditions` are the conditions the request names, each with its probability and the other readings. `runs` are the runs to add or drop, as `{"id", "name", "probability"}`. `best` is `{"key", "order"}` for `set_best`.

It uses OpenAI's Decisions API. The request, the project's column names, values observed in its columns, and run names are sent to OpenAI. Run histories, files and logs are never sent. It's off unless the server has `FLOCK_OPENAI_API_KEY` set and an org admin turned it on with [`"assistant_enabled": true`](orgs#organizations). While it's off, it answers 404, and `GET .../groups` gives `"assistant": false`. Outsiders never reach it. When OpenAI fails, it answers 502. It also picks a group's [primary columns](#groups), from the group's name, filter, best by column, and its columns' names and a few of their values; outsiders get what a member's visit picked, or the analysis.

### Proposed groups[[group-proposals]]

`POST .../groups/propose` is one turn of a chat in which a model proposes how to group the project's runs. Flock keeps no conversation: each turn sends the whole of it, and the proposals on screen when there are some.

```json
{"messages": [{"role": "user", "content": "split the lr sweep by optimizer"}, ...], "proposals": [...]}
```

`messages` alternate `user` and `assistant` (the earlier replies' `message`), up to 100. With none, the model makes its first proposal. `proposals` are the ones on screen, as returned. Fields other than `id`, `name`, `why`, `runs`, `filter`, `axes`, `best_key` and `best_order` are ignored. It returns:

```json
{"message": "I split the lr sweep by optimizer and left the 3 debug runs out.", "proposals": [{"id": "lr-adamw", "name": "pretrain-1b, lr, adamw", "why": "Compares four learning rates with AdamW, everything else fixed.", "runs": ["9fc2v9ee", "..."], "filter": {"group": "pretrain-1b", "config.optimizer": "adamw"}, "axes": ["config.lr"], "values": {"config.lr": [0.0001, 0.0003, 0.001, 0.003]}, "grid": {"x": "config.lr", "y": null, "xs": [0.0001, 0.0003, 0.001, 0.003], "ys": null, "cells": [{"x": 0.0001, "y": null, "runs": 3, "best": 2.41, "run": "9fc2v9ee"}, "..."]}, "best_key": "summary.eval/loss", "best_order": "asc", "best": {"run": "k2l0c0de", "name": "pretrain-1b-lr3e-4", "value": 2.31}, "repeat_key": "config.seed", "create": {"name": "pretrain-1b, lr, adamw", "runs": ["9fc2v9ee", "..."], "filter": {"group": "pretrain-1b", "config.optimizer": "adamw"}, "best_key": null, "best_order": null}}], "ungrouped": 3, "runs": 41}
```

| Field | Meaning |
|---|---|
| `message` | The model's reply, 1 to 3 plain sentences. |
| `proposals` | Every proposal now on screen: a revision replaces the whole set. |
| `ungrouped` | Live runs in no proposal and no saved group. |
| `runs` | Live runs in the project. |

A proposal's `id` is a slug that stays the same across turns while it's the same group. The model writes its `name`, `why`, `filter` or hand-picked runs, and the order of its `axes`. Flock computes the rest from the runs it ends up with:

- `runs`: the runs `filter` matches, when it has one; else the model's picks that are live runs of the project. A proposal with fewer than 2 runs, or with the same runs as an earlier proposal or a saved group, is left out.
- `axes`: the `config.*` keys of kind `varies` among its runs ([column kinds](#groups)), the model's order first. `values`: each one's distinct values, sorted, `null` last for runs without one.
- `grid`: with 1 or 2 axes of at most 24 values each, a cell per value of `x` (and of `y`), with its number of runs and its best value and run. Cells with no runs show what the sweep didn't cover. Otherwise `null`.
- `best_key`, `best_order`, `best`: the project's best by column, unless the model picked another summary metric its runs have. `repeat_key`: the seed key, when runs repeat a configuration.
- `create`: the body of `POST .../groups` that saves it as shown: its runs, and its filter so later matches are offered as new matches. `best_key` is `null` unless it differs from the project's.

The model reads the project's live runs: their id, name, state, W&B group, creation time, the `config.*` values that vary across the project, and one summary metric. It also reads the names of the summary metrics, the saved groups' names, filters and sizes, and the [suggestions](#groups-suggestions). Above about 40,000 characters, the runs are summarized and the model proposes filters. Run histories, files and logs are never sent. It uses OpenAI's Responses API with `FLOCK_OPENAI_PROPOSAL_MODEL` (`gpt-6.2-sol` by default). It answers 403 while the [assistant](#group-assistant) is off for the org or the server has no key, and 502 when OpenAI fails or takes more than 90 seconds.
