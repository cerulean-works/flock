# Datasets

Endpoints for the Lance datasets in an organization's `flock-datasets` bucket, which the dashboard's Datasets tab uses. Paths are relative to `/api/v1/orgs/{org}/datasets`. Every endpoint checks that you belong to the organization, and answers 404 otherwise, as it does when the server has no S3. For how datasets work, see [Datasets](../s3#datasets).

A dataset's `path` is its folder in `flock-datasets`, such as `eval/mmlu`, without slashes at either end. A path that isn't a dataset answers 404 everywhere except `GET at`. When Lance can't read a dataset (a corrupt manifest, a missing data file), the answer is 502 with Lance's message in `error`.

## Reading[[reading]]

`version` takes a version number or a tag. Without it, the latest version is used, asked of the S3 server each time.

| Endpoint | Meaning |
|---|---|
| `GET at?path&cursor` | One level of a folder, 100 entries a page: `{"kind": "folder", "path", "datasets": [{"name", "path", "version", "updated_at"}], "folders": [{"name", "path"}], "files": [{"name", "key", "size", "last_modified"}], "cursor"}`. `cursor` is the next page's, `null` on the last. A dataset's `version` is `null` when its manifests use Lance's old naming. When `path` is a dataset, the answer is `{"kind": "dataset", "path", "version", "updated_at"}`. Without `path`, the bucket's root is listed, and `flock-datasets` is created first if the organization has none (502 if the S3 server refuses). |
| `GET info?path&version` | The dataset at that version: `version` (resolved), `timestamp`, `latest_version`, `rows`, `columns`, `fragments`, `data_size` (bytes of its data files), `schema` (fields with `name`, `type`, `nullable`, `metadata` and `children`), `metadata` (the schema's), `binary`, `details` (`data_storage_version`, `stable_row_ids`, `fragments`, `indices`) and `tags` (`[{"name", "version"}]`). |
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

## Changing[[changing]]

Members and admins can delete, rename and tag datasets.

| Endpoint | Meaning |
|---|---|
| `POST delete` | Moves `{"path"}` to the trash, where admins can restore it for 14 days. Answers with its trash entry. |
| `POST rename` | Moves `{"path", "to"}`, with all its versions, tags and indices. `to` is made of segments of letters, digits, `.`, `-` and `_`, joined by `/`, at most 512 characters. A `to` where something exists answers 409 with `"taken": true`. A `to` inside the dataset or inside another dataset answers 422. A write that lands during the copy answers 409, and the copy is removed. |
| `PUT tags/{tag}` | Puts the tag on `{"path", "version"}`. If the tag is on another version, it answers 409 with that `version`, unless the body has `"move": true`. Tag names are up to 100 ASCII letters, digits, `.`, `-` and `_`, starting with a letter or digit, and not only digits. |
| `DELETE tags/{tag}?path` | Removes the tag. Answers 204. |

Deleting and renaming copy every object on the S3 server, in the request. Datasets of more than 10,000 objects or 100 GiB answer 409 with a `command` for the AWS CLI to run instead, before anything is touched.

## The trash[[trash]]

Only organization admins can use these. Members get 403.

| Endpoint | Meaning |
|---|---|
| `GET trash` | `{"entries": [...]}`, newest first. An entry has `id`, `path`, `deleted_by`, `deleted_at`, `moved_at` (`null` while the move is unfinished), `expires_at`, `latest_version`, `objects` and `bytes`. |
| `POST trash/{id}/restore` | Copies the dataset back to its path, or to `{"to"}`. Something now at the path answers 409 with `"taken": true`. |
| `DELETE trash/{id}` | Deletes the dataset for good. Answers 204. |
