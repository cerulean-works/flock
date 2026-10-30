# Models

Endpoints for the model registry in an organization's `flock-models` bucket, which `flock.hub.models`, the `flock models` command and the dashboard's Models tab use. Paths are relative to `/api/v1/orgs/{org}/models`. Every endpoint checks that you belong to the organization, and answers 404 otherwise, as it does when the server has no S3. For saving and loading models, see [Store and load models](../../hub/store-models).

A model's `name` is one segment of up to 100 lowercase letters, digits, `.`, `-` and `_`, starting with a letter or digit, such as `olmo2-1b`. Users in answers (`created_by`, `deleted_by`, `primary_set_by`, `moved_by`, `user`) are briefs, `{"id", "name", "email", "avatar_url"}`, or `null` once the account is deleted.

## Models and versions[[models-and-versions]]

A `ref` is a version number, a tag or `primary`. Without one, the primary version is used.

| Endpoint | Meaning |
|---|---|
| `GET` (the models path itself) | `{"models": [...]}`, by name. Each has `name`, `model` (`org/name`), `created_at`, `created_by`, `versions` (how many are live), `latest` and `primary` (numbers, `null` before the first version), `size` (the primary's bytes) and `updated_at`. Creates `flock-models` first if the organization has none. |
| `GET {name}` | The model: `name`, `model`, `created_at`, `created_by`, `versions`, `latest`, `primary`, `primary_set_by`, `primary_set_at` and `tags` (`[{"name", "version", "moved_by", "moved_at"}]`). |
| `GET {name}/resolve?ref` | The version `ref` names, with its files, as below. A model with no versions answers 404. |
| `GET {name}/versions?before&checkpoints` | Live versions, newest first, 100 a page: `{"versions": [...], "cursor"}`. Pass `cursor` as `before` for the next page. `cursor` is `null` on the last. With `checkpoints=true`, only versions with a `checkpoint-{step}` tag. |
| `GET {name}/versions/{number}` | One version, with its files. |

A version has these fields:

| Field | Meaning |
|---|---|
| `model`, `name`, `number` | `org/name`, the model's name and the version's number. |
| `digest` | The sha256 of its file list (path, size and sha256 of each file, sorted by path). |
| `total_bytes`, `metadata` | Its size, and the metadata it was saved with. |
| `tags`, `primary` | Its tags, and whether it's the primary version. |
| `run`, `step` | The run that made it, as `entity/project/id`, and the step. `null` when it wasn't saved from a run of the organization. |
| `created_by`, `created_at` | Who pushed it, and when. |
| `eval_runs` | How many eval runs scored it. Deleted runs aren't counted. |
| `bucket`, `prefix` | `flock-models`, and the version's folder in it, such as `olmo2-1b/u/01JD3KQ8…/`. |
| `files` | `[{"path", "size", "sha256"}]`, sorted by path. Only in `resolve` and `versions/{number}`. |
| `evals` | The eval runs that scored it, as `entity/project/id`, leaving out deleted runs. Only in `resolve` and `versions/{number}`. |

## Tags and the primary version[[tags-and-primary]]

Members and admins can change these.

| Endpoint | Meaning |
|---|---|
| `PUT {name}/primary` | Makes `{"version"}` the primary version, recording who and when. Answers the version, with `previous`, the number it replaced (`null` when it was already primary). |
| `PUT {name}/tags/{tag}` | Puts the tag on `{"version"}`. If the tag is on another version, it answers 409 with that `version`, unless the body has `"move": true`. A `checkpoint-{step}` tag never moves, so it answers 409 even with `move`. Answers `{"tag", "version"}`. |
| `DELETE {name}/tags/{tag}` | Removes the tag. The version stays. Answers 204. |

Tag names are up to 100 ASCII letters, digits, `.`, `-` and `_`, starting with a letter or digit. A name of only digits, or `primary` in any case, answers 422.

## Deleting[[deleting]]

Members and admins can delete. A deleted version or model is gone from every other endpoint at once, and its files move to the trash in the background.

| Endpoint | Meaning |
|---|---|
| `DELETE {name}/versions/{number}` | Deletes the version. The primary version answers 409, as does a version with a tag other than `checkpoint-{step}`. Its checkpoint tags go with it. Answers its trash entry. |
| `DELETE {name}` | Deletes the model with every version in it, and aborts its open uploads. Answers its trash entry. |

## Uploads[[uploads]]

`flock.hub.models` uses these to push a version. Members and admins can open uploads.

| Endpoint | Meaning |
|---|---|
| `POST {name}/uploads` | Opens an upload, creating the model if it's new. The body has `files` (`[{"path", "size"}]`), and optionally `metadata`, `tags`, `primary`, `run` (`entity/project/id`), `step` and `dedupe_key`. Answers 201 with the upload: `ulid`, `bucket`, `prefix` (the folder to write to), `part_size` and the rest. If a live version has the `dedupe_key`, it answers 200 with that version instead. A `checkpoint-{step}` tag already on a version answers 409 with its `version`. |
| `GET {name}/uploads?state` | Your uploads of the model in `state` (`open`, the default, `committed`, `aborted` or `expired`), oldest first: `{"uploads": [...]}`. |
| `GET {name}/uploads/{ulid}` | An upload's state and declared files. |
| `POST {name}/uploads/{ulid}/commit` | Makes the upload a version, given `{"files": [{"path", "sha256"}]}`. Only the user who opened it can commit it. Answers the version. Committing again answers the same version. |
| `DELETE {name}/uploads/{ulid}` | Aborts the upload and deletes what it wrote. Its opener or an admin can. Answers 204. |

A commit that doesn't match what's in storage answers 409 with the paths, and the upload stays open:

| Field | Paths that |
|---|---|
| `missing` | were declared but aren't in storage |
| `extra` | are in storage but weren't declared |
| `resized` | have another size than declared |
| `uploading` | still have a multipart upload open |
| `mismatched` | have an `x-amz-meta-sha256` other than the committed sha256 |

A shard that isn't safetensors, or an index naming a tensor no shard holds, answers 422.

## The trash[[trash]]

Only organization admins can use these. Members get 403. The paths start with `-/`, which no model name can.

| Endpoint | Meaning |
|---|---|
| `GET -/trash` | `{"entries": [...]}`, newest first: deleted models, and deleted versions of live models. Every entry has `kind` (`model` or `version`), `id`, `name`, `model`, `deleted_by` (`null` for versions retention deleted), `deleted_at`, `expires_at`, `moved` (`false` while its files are still moving) and `bytes`. A model has `versions`, the number deleted with it. A version has `number`, `tags` (the checkpoint tags that went with it) and `eval_runs`. |
| `POST -/trash/versions/{id}/restore` | Restores the version with its number, and its checkpoint tags if nobody has taken them since. A version of a deleted model answers 409. |
| `POST -/trash/models/{id}/restore` | Restores the model with the versions deleted with it. A live model with its name answers 409 with `"taken": true`. |
| `DELETE -/trash/versions/{id}`, `DELETE -/trash/models/{id}` | Deletes it for good. Answers 204. |

Restoring and deleting for good answer 409 while the files are still moving to the trash. The sweep removes entries 14 days after they were deleted.

## Retention[[retention]]

An organization's `model_retention` keeps the newest N untagged versions of each model. Admins set it with [`PATCH /orgs/{org}`](orgs#organizations), and `null`, the default, keeps every version. When it's set, the sweep deletes older versions that aren't primary, have no tag other than `checkpoint-{step}` and have no eval runs that aren't deleted. They go to the trash like any deleted version.
