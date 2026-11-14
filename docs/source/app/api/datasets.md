# Datasets

Endpoints for the Lance datasets in an organization's `flock-datasets` bucket, which the dashboard's Datasets tab uses. Paths are relative to `/api/v1/orgs/{org}/datasets`. Every endpoint checks that you belong to the organization, and answers 404 otherwise, as it does when the server has no S3. The exception is [public datasets](#public), whose reads answer anyone. For how datasets work, see [Datasets](../s3-behavior#datasets).

A dataset's `path` is its folder in `flock-datasets`, such as `eval/mmlu`, without slashes at either end. A path that isn't a dataset answers 404 everywhere except `GET at`. When Lance can't read a dataset (a corrupt manifest, a missing data file), the answer is 502 with Lance's message in `error`.

## Reading[[reading]]

`version` takes a version number or a tag. Without it, the latest version is used, asked of the S3 server each time.

| Endpoint | Meaning |
|---|---|
| `GET at?path&cursor` | One level of a folder, 100 entries a page: `{"kind": "folder", "path", "datasets": [{"name", "path", "version", "updated_at"}], "folders": [{"name", "path"}], "files": [{"name", "key", "size", "last_modified"}], "cursor"}`. `cursor` is the next page's, `null` on the last. A dataset's `version` is `null` when its manifests use Lance's old naming. When `path` is a dataset, the answer is `{"kind": "dataset", "path", "version", "updated_at"}`. Without `path`, the bucket's root is listed, and `flock-datasets` is created first if the organization has none (502 if the S3 server refuses). |
| `GET info?path&version` | The dataset at that version: `public` (whether it's [public](#public)), `version` (resolved), `timestamp`, `latest_version`, `rows`, `columns`, `fragments`, `data_size` (bytes of its data files), `schema` (fields with `name`, `type`, `nullable`, `metadata` and `children`), `metadata` (the schema's), `binary`, `details` (`data_storage_version`, `stable_row_ids`, `fragments`, `indices`) and `tags` (`[{"name", "version"}]`). |
| `GET rows?path&version&offset&limit&columns` | A page of rows: `{"version", "offset", "limit", "total", "columns", "rows"}`. `limit` defaults to 50, or 10 when a column holds binary data, and is at most 100. Repeat `columns` to read only those columns. Each cell is rendered for display, as below. |
| `GET cell?path&version&row&column` | One cell in full, up to 1 MiB of text: `{"row", "column", "type", "cell"}`. |
| `GET versions?path&cursor` | The history, newest first, 50 a page: `{"versions": [{"version", "timestamp", "tags", "operation", "rows", "rows_delta", "properties"}], "latest", "cursor"}`. `operation` is Lance's (`Append`, `Overwrite`, `Delete` and the rest). `rows_delta` compares with the version before, when it still exists. `properties` are the `transaction_properties` the writer set. |
| `GET readme?path` | `{"text", "truncated"}`: the first 1 MiB of `README.md` in the dataset's folder. 404 when there's none. |

A cell is an object whose `k` says its kind:

| `k` | Fields | For |
|---|---|---|
| `null` | | null values |
| `num` | `v`, and `label` for an HF `ClassLabel` | numbers. Integers beyond 2^53 and non-finite floats come as strings. |
| `bool` | `v` | booleans |
| `str` | `v`, and `len` when cut | strings, cut at 500 characters in a page |
| `time` | `v` | timestamps and dates, in ISO 8601 and the column's time zone |
| `image` | `mime`, `size`, `data` (base64) | PNG, JPEG, GIF and WebP bytes up to 1 MiB, also inside an HF `Image` struct of `{bytes, path}` |
| `bin` | `size` | other binary values, and images once a page has sent 64 MiB of them |
| `blob` | `size` | Lance blob columns, whose bytes aren't read |
| `vec` | `dtype`, `len`, `head` | fixed-size lists of floats, such as embeddings, with their first 8 values |
| `json` | `v`, and `len` when cut | other lists and structs, as JSON |

## Public datasets[[public]]

Datasets are public unless an org admin makes them private, by exact path. Anyone can call `info`, `rows`, `cell`, `versions` and `readme` on a public dataset, at any version or tag, without credentials. `at`, the trash and every change stay members-only, so outsiders can't list datasets, and the bucket's objects stay out of reach without an S3 key. Private paths answer outsiders 404, as missing datasets do.

| Endpoint | Who | Meaning |
|---|---|---|
| `PUT public?path` | org admins | Makes the path public again. Answers `{"path", "public": true}`. |
| `DELETE public?path` | org admins | Makes the path private, whether or not a dataset is there yet. Answers 204. |

Renaming a private dataset keeps it private at its new path, and renaming any dataset onto a private path makes it private. Deleting a private dataset keeps its path private, so it comes back private when restored, at its path or another. `PUT public` and `DELETE public` answer 409 in an organization whose existing datasets Flock hasn't marked private yet, a few minutes after an upgrade from a version where datasets started private.

## Changing[[changing]]

Members and admins can delete, rename, move and tag datasets. Delete, rename and move also take a folder, such as `eval`, which acts on everything under it as one item, datasets and loose files alike.

| Endpoint | Meaning |
|---|---|
| `POST delete` | Moves datasets and folders to the trash, where admins can restore them for 14 days. A folder is one trash entry. The body has `path` or `paths`, not both. With `{"path"}`, it answers with that trash entry. With `{"paths": [...]}` (up to 1,000), see [Many items](#many-items). |
| `POST rename` | Moves `{"path", "to"}`, a dataset with all its versions, tags and indices, or a folder with everything in it. `to` is made of segments of letters, digits, `.`, `-` and `_`, joined by `/`, at most 512 characters. A `to` where something exists answers 409 with `"taken": true`. A `to` inside the item or inside another dataset answers 422. A write that lands during the copy answers 409, and the copy is removed. For a folder, a write to any dataset in it does the same, and nothing moves. |
| `POST move` | Moves `{"paths", "to"}` into the folder `to`, each keeping its last name. `eval/mmlu` moved to `archive` becomes `archive/mmlu`. `to` is `""` for the top of the bucket. Each item moves as `rename` would. See [Many items](#many-items). |
| `PUT tags/{tag}` | Puts the tag on `{"path", "version"}`. If the tag is on another version, it answers 409 with that `version`, unless the body has `"move": true`. Tag names are up to 100 ASCII letters, digits, `.`, `-` and `_`, starting with a letter or digit, and not only digits. |
| `DELETE tags/{tag}?path` | Removes the tag. Answers 204. |

Deleting, renaming and moving copy every object on the S3 server, in the request. More than 10,000 objects or 100 GiB answer 409 with a `command` for the AWS CLI to run instead, before anything is touched. With `paths`, the limits count every item together, and `command` has one line per path.

### Many items[[many-items]]

`POST move` and `POST delete` with `paths` handle each item in turn. One that fails doesn't stop the rest, and the answer is 200 with `{"results": [...]}`, one per path, in order:

| Route | Each result |
|---|---|
| `POST move` | `path`, `to` (its new path), `moved`, `error` (the reason, or `null`) and `taken` (`true` when something already exists at `to`) |
| `POST delete` | `path`, `deleted`, `error` (the reason, or `null`) and `entry` (its trash entry, or `null`) |

An item fails when nothing exists at its path, when its new path is taken, when it would move into itself or stay where it is, or when it's written to during the copy.

## The trash[[trash]]

Only organization admins can use these. Members get 403.

| Endpoint | Meaning |
|---|---|
| `GET trash` | `{"entries": [...]}`, newest first. An entry has `id`, `kind` (`dataset` or `folder`), `path`, `deleted_by`, `deleted_at`, `moved_at` (`null` while the move is unfinished), `expires_at`, `latest_version` (`null` for a folder), `objects` and `bytes`. |
| `POST trash/{id}/restore` | Copies the dataset or folder back to its path, or to `{"to"}`. A folder comes back as a whole. Something now at the path answers 409 with `"taken": true`. |
| `DELETE trash/{id}` | Deletes the dataset or folder for good. Answers 204. |
