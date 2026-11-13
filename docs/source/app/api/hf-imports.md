# Hugging Face imports

Endpoints for importing a Hugging Face dataset or model from the dashboard, which the server runs in the background. Paths are relative to `/api/v1/orgs/{org}/imports/hf`. Members and admins of the organization can use them, and others get 404. For the dashboard's side, see [Import from Hugging Face](../import-hf).

A Hugging Face access token, for gated and private repositories, goes in the `X-HF-Token` header of a preview and in the `token` field of a start. The server uses it for that request or import only, and never writes it to the database or the logs.

## Endpoints

| Endpoint | Meaning |
|---|---|
| `GET preview?kind&repo&revision` | What the dialog shows of a repository, read from the Hub. `kind` is `dataset` or `model`, and `revision` defaults to `main`. See [Previews](#previews). |
| `POST` (the imports path itself) | Starts an import. Answers 202 with the import. See [Starting an import](#starting). |
| `GET` (the imports path itself) | `{"imports": [...]}`: the organization's imports of the last 7 days, newest first, at most 100. |
| `GET {id}` | One import. |
| `DELETE {id}` | Cancels a running import, and answers it. Only whoever started it, or an admin, can cancel (403 otherwise). An import that already ended is answered as it is. |

## Starting an import[[starting]]

The body has these fields:

| Field | Default | Meaning |
|---|---|---|
| `kind` | required | `dataset` or `model`. |
| `repo` | required | The repository, as `owner/name`. |
| `revision` | `main` | A branch, tag or commit. |
| `token` | none | A Hugging Face access token. |
| `dest` | required for datasets | The folder of `flock-datasets` to write to. Each split lands at `{dest}/{split}`, or `{dest}/{config}/{split}` when the dataset has several configs. |
| `config` | the only one, or `default` | A dataset's config. |
| `splits` | all | A dataset's splits. |
| `name` | required for models | The model to add a version to, created if it's new. |
| `tags` | `[]` | The new version's tags. |
| `primary` | `false` | Make the new version primary once it's committed. |

The server asks the Hub before it answers, so these are refused at once:

| Status | When |
|---|---|
| 404 | The repository or revision isn't on the Hub, or it's private and the token can't read it. |
| 401, 403 | The Hub refused the token, or the repository is gated. |
| 409 | A dataset already exists at a split's path, an import into the same folder or model is running, or the repository is larger than `FLOCK_HF_IMPORT_MAX_BYTES`. The last answers `too_large: true`, its `size` in bytes and `command`, the `flock datasets import` or `flock models import` command that does the same import. |
| 422 | A model with only pickled weights, one whose config needs code from its repository (`auto_map`), or a `model_type` that isn't a causal LM in transformers. A dataset with no files Flock reads. An invalid model name or tag. |

## Imports[[imports]]

| Field | Meaning |
|---|---|
| `id`, `kind`, `repo`, `revision` | What was asked for. |
| `sha` | The commit the files come from. |
| `dest` | The dataset folder, or the model's name. |
| `config`, `splits` | A dataset's config and splits. |
| `tags`, `primary` | A model version's tags, and whether it becomes primary. |
| `state` | `running`, `finished`, `failed`, `cancelled` or `interrupted`. |
| `phase` | While running: `downloading` then `converting` for a dataset, `uploading` then `committing` for a model. |
| `bytes_total`, `bytes_done` | Bytes to download, and downloaded so far. |
| `files_total`, `files_done`, `rows_done` | A dataset's source files, those converted, and the rows written. |
| `progress` | From 0 to 1. For a dataset, the download is the first half and the conversion the second. |
| `result` | Once finished: `{"datasets": [{"split", "path", "version", "rows"}]}` for a dataset, `{"model", "version"}` for a model, with `existing: true` when the model already had a version of that commit. |
| `error` | Why it failed. |
| `command` | The `flock` command that does the same import. |
| `started_by`, `started_at`, `finished_at` | Who started it (a user brief, or `null`), and when it started and ended. |

The server that runs an import saves its progress at most once a second, and every 10 seconds while it waits. An import whose server stops is marked `interrupted`, at once on a clean shutdown and within a minute otherwise. Start it again with a new request, since the token isn't kept.

A model's files go through an upload session, as any client's do (see [Uploads](models#uploads)), so a failed or cancelled import leaves an aborted upload and no version. A dataset's splits are committed when every file is converted.

## Previews[[previews]]

A dataset's preview:

| Field | Meaning |
|---|---|
| `configs` | `[{"name", "size", "splits"}]`, with `splits` as `[{"name", "size", "rows"}]`. Read from the Hub's dataset viewer. When it has nothing for the repository, only the default config's splits are listed, from the files an import would download, and other configs have `splits: null`. |
| `default_config` | The config an import takes without `config`. |
| `max_bytes` | `FLOCK_HF_IMPORT_MAX_BYTES`. |

A model's preview has `sha`, `model_type`, `size` (the bytes an import downloads), `too_large`, `max_bytes`, and `refusal`, the reason it would be refused with 422, or `null`.
