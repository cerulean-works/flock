# Datasets and S3 keys

Endpoints for the hub's datasets, their versions and cards, and for S3 credentials. Paths are relative to `/api/v1`, `{org}` is an organization's ID and `{name}` a dataset's name. Every endpoint here answers 503 while the hub is off (`FLOCK_HUB_S3_ENDPOINT` unset). For how datasets work, see [Datasets](../datasets).

## Datasets[[datasets]]

A dataset is returned as:

```json
{"id": "6f1c2a9e-0d4b-4c55-9a51-1f0c9f6f3b8e", "org": "flock", "name": "fineweb-sample", "description": "", "visibility": "private", "uri": "s3://datasets/flock/fineweb-sample", "created_by": {"id": 1, "name": "Ada", "email": "ada@flock.test", "avatar_url": null}, "created_at": 1792282194.3, "updated_at": 1792282194.3, "latest_version": 4, "rows": 1000, "bytes": 123456, "role": "admin", "mirror": null}
```

| Field | Meaning |
|---|---|
| `id` | The dataset's uuid. It never changes, and a new dataset with a deleted one's name gets a new one. |
| `visibility` | `private`: the organization's members only. `internal`: anyone signed in can read it. |
| `uri` | Its Lance URI through the S3 proxy. |
| `created_by` | Its creator, or `null` once their account is deleted. |
| `latest_version`, `rows`, `bytes` | The latest version, and its row count and size. `null` before the first write. |
| `role` | Your role in its organization, or `null` when you read an internal dataset of another organization. |
| `mirror` | The Hugging Face dataset it mirrors, `{"repo", "config", "sha"}`, or `null`. |

| Endpoint | Who | Meaning |
|---|---|---|
| `GET /datasets/{org}` | readers | `{"datasets": [...]}`, sorted by name. Members get every dataset, others the internal ones. |
| `POST /datasets/{org}` | members | Creates an empty dataset from `{"name", "description"?, "visibility"?}`. Answers 201, or 409 if the name is taken. |
| `GET /datasets/{org}/{name}` | readers | One dataset. |
| `PATCH /datasets/{org}/{name}` | members, org admins | Changes `description` (members), or `name` and `visibility` (org admins). Returns the dataset. A taken name answers 409. |
| `DELETE /datasets/{org}/{name}` | org admins | Deletes it with every version. Answers 204. The name is free at once, and the data is deleted in the background. |

Names follow the rules of project names: 1 to 128 characters, with no spaces around them, no control characters and none of `/ \ # ? % :`. Bodies with other fields answer 422.

## Versions[[versions]]

A version is returned as:

```json
{"version": 3, "created_at": 1792282194.3, "author": {"id": 1, "name": "Ada", "email": "ada@flock.test", "avatar_url": null}, "op": "Append", "message": "add shard 7", "rows": 1000, "bytes": 123456, "fragments": 8, "tags": ["v1.0"], "pruned": false}
```

| Field | Meaning |
|---|---|
| `author` | Who wrote it, or `null` when unknown (written around the S3 proxy, or by a deleted account). |
| `op` | The Lance operation: `Overwrite`, `Append`, `Delete`, `Update`, `Merge`, `Project`, `UpdateConfig`, `Rewrite`, `Restore` and others. `null` for operations pylance doesn't name. |
| `message` | The commit message, from the transaction property `__lance_commit_message`, or set afterwards. |
| `rows`, `bytes`, `fragments` | The dataset's rows, size and fragments at this version. |
| `pruned` | `true` once a prune removed the version. Its row stays, but its data can't be read. |

| Endpoint | Who | Meaning |
|---|---|---|
| `GET .../versions` | readers | `{"versions": [...], "total"}`, newest first, pruned versions included. Takes `offset` and `limit` (1 to 500, default 50). |
| `GET .../versions/{version}` | readers | One version, plus `schema` (`[{name, type, nullable}]`), `features` (Hugging Face `Features.to_dict()`) and the table `metadata`. These three are `null` for pruned versions. |
| `PATCH .../versions/{version}` | the author, org admins | Sets the message of a version that has none, with `{"message"}`. Returns the version. 409 if it already has a message. |

## Rows[[rows]]

`GET .../rows` returns a page of rows:

| Query | Meaning |
|---|---|
| `version` | The version to read. Defaults to the latest. |
| `offset` | The first row, from 0. |
| `limit` | Rows to return, 1 to 500. Defaults to 100. |
| `columns` | Column names, comma-separated. Defaults to every column. Unknown names answer 400. |

```json
{"version": 4, "offset": 0, "total": 1000, "columns": [{"name": "text", "type": "string"}, {"name": "image", "type": "binary"}], "rows": [["Once upon a time", {"$binary": 20480}]], "truncated": []}
```

Cells are JSON:
- Strings longer than 2000 characters are cut, and their `[row, column]` is listed in `truncated`.
- Binary values are `{"$binary": <size in bytes>}`.
- Timestamps and dates are ISO 8601 strings.
- Structs, lists and maps are JSON objects and arrays.

A dataset without versions answers 404, as does a version that doesn't exist or was pruned.

## The card[[readme]]

| Endpoint | Who | Meaning |
|---|---|---|
| `GET .../readme` | readers | `{"version", "sha", "content"}`: the card at `version` (default: the latest). `sha` and `content` are `null` without a card, and `version` is `null` before the first write. |
| `PUT .../readme` | members | Replaces the card with `{"content", "message"?}`, as a new version. Returns `{"version"}`. 409 before the first write. |

## Tags, restore, optimize and prune[[writes]]

| Endpoint | Who | Meaning |
|---|---|---|
| `PUT .../tags/{tag}` | members | Points the tag at `{"version"}`, creating or moving it. Returns every tag, `{"tags": {"v1.0": 4}}`. 404 for an unknown version. |
| `DELETE .../tags/{tag}` | members | Deletes the tag. Returns the remaining tags, or 404 for an unknown tag. |
| `POST .../restore` | members | Makes `{"version", "message"?}` the latest again, as a new version. Returns `{"version"}`, the new one. |
| `POST .../optimize` | members | Compacts small fragments. Returns `{"version"}`: the new latest version, unchanged when there was nothing to compact. |
| `POST .../prune` | org admins | Deletes versions older than `{"older_than_days"}`, except the latest and tagged ones, and the files only they use. Returns `{"versions_removed", "bytes_removed"}`. |

Tag names are 1 to 128 letters, digits, `.`, `_` and `-`, starting with a letter or digit. These endpoints write through the S3 proxy as you, and answer 503 while `FLOCK_HUB_PROXY_URL` is unset.

## S3 keys and credentials[[s3-keys]]

A key is returned as:

```json
{"pk": 3, "name": "duckdb", "access_key_id": "FLKHMZXW6YTBOI4DEMRQ", "read_only": true, "org": "flock", "created_at": 1792282194.3, "last_used_at": null}
```

| Endpoint | Who | Meaning |
|---|---|---|
| `GET /me/hub-keys` | anyone | Your S3 keys, as `{"keys": [...]}`. |
| `POST /me/hub-keys` | anyone | Creates a key from `{"name", "read_only"?, "org"?}`. `org` limits it to one of your organizations. Answers 201 with the key plus `secret_access_key`, `endpoint` and `region`. The secret is shown only here. |
| `DELETE /me/hub-keys/{pk}` | anyone | Revokes one of your keys at once. Answers 204. |
| `POST /hub/credentials` | anyone | Temporary credentials, as `flock.hub` asks for with an API key. The body, `{"read_only"?}`, is optional. |

Temporary credentials are returned as:

```json
{"endpoint": "https://s3.flock.example", "region": "us-east-1", "access_key_id": "FLKTB3J7Q2KXN5VYDWPA", "secret_access_key": "...", "session_token": "...", "expires_at": 1792325394.3}
```

They last `FLOCK_HUB_CREDENTIALS_TTL` seconds. Pass `session_token` as the S3 session token (`aws_session_token` in Lance's `storage_options`). Keys and credentials act with your roles at the time of each request, so removing you from an organization takes effect at once.
