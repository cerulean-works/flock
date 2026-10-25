# 007: the hub's datasets, S3 keys and S3 proxy

| | |
| --- | --- |
| Status | planned |
| Date | 2026-10-25 |
| Builds on | [003](003-organizations-and-auth.md) |
| Keeps | D2 (one API server per database, small state in memory), D18 (orgs are entities) |
| Result | n/a |

## 1. Goal

Flock gets a hub: an HF-Hub-like home for datasets (models later), with all bytes in our own
S3-compatible store (Ceph RGW). Datasets are [Lance](https://lance.org) datasets; Lance's own
versions are the hub's versions. The client side is `flock.hub` (its stage doc is
[`flock/hub/docs/agents/001-hub-core.md`](../../../../flock/hub/docs/agents/001-hub-core.md)).
This stage is the app side:

1. **Datasets in the API.** CRUD, version history with authors, a row viewer, README cards,
   tags, restore, and admin-only optimize and prune.
2. **S3 keys.** Long-lived keys for external tools, and short-lived credentials that
   `flock.hub` gets with a flock API key.
3. **An S3 proxy.** Upstream S3 has one global key. The proxy, a separate process, checks each
   request's signature and permissions, then forwards it re-signed with the global key.
4. **Datasets in the web app.** List, card, viewer, versions, settings; S3 keys on the account
   page.
5. **Hugging Face mirrors.** Server-side imports of small HF datasets, which `flock.hub`'s
   `load_dataset` falls back to.

**Out of scope:** models; Lance branches; dataset-level ACLs beyond org roles; aws-chunked
uploads; virtual-hosted-style S3 addressing; billing for storage.

---

## 2. Design decisions

The numbering continues from 006.

### D41. One virtual bucket, uuid prefixes upstream

Clients see one bucket, `datasets`, addressed path-style:
`<proxy>/datasets/<org>/<dataset>/<lance path>`, so a dataset's Lance URI is
`s3://datasets/<org>/<dataset>`. Upstream, the same object is
`s3://$FLOCK_HUB_BUCKET/datasets/<uuid>/<lance path>`. The proxy maps `<org>/<dataset>` to the
uuid on every request, including `Key` and `Prefix` in XML responses.

- Renaming a dataset only changes Postgres.
- A deleted dataset's prefix is purged in the background; a new dataset with the same name
  gets a new uuid, so it never sees old objects.
- The proxy refuses writes to a dataset that doesn't exist in Postgres, and refuses listings
  above a dataset (`ListObjectsV2` with a prefix shorter than `<org>/<dataset>/`). It answers
  `ListBuckets` with just `datasets`.

### D42. Lance versions are the hub's versions; Postgres keeps a summary

Every Lance commit is a hub version, numbered by Lance (v1 is the first write). Tags are Lance
tags (`_refs/tags/<name>.json`), which don't create versions. `dataset_commits` holds one row
per version:

- **Author.** The proxy inserts `(dataset_pk, version, user_pk, created_at)` when a manifest
  write succeeds (D45), so a client can't claim someone else wrote a version.
- **Summary.** The API fills in `op` (the Lance operation class name: `Overwrite`, `Append`,
  `Delete`, `Update`, `Merge`, `Project`, `UpdateConfig`, `Rewrite`, `Restore`, …), `message`,
  `rows`, `bytes` and `fragments` from `ds.read_transaction(v)` and `ds.versions()`, the first
  time it lists a version that has no summary. `message` is the transaction property
  `__lance_commit_message`.
- **Why copy it.** `cleanup_old_versions` deletes old manifests and their transaction files. A
  pruned version keeps its row with `pruned = true`, so the history stays complete.
- **Ops without properties.** `LanceDataset.delete()` can't carry transaction properties, so
  `PATCH .../versions/{v}` sets a message once, while it is empty, for the version's author or
  an org admin.

### D43. History can't be rewritten through S3

- Members can read and write (PUT, multipart, tags). Only org admins can DELETE
  (`DeleteObject`, `DeleteObjects`), prune, rename, change visibility or delete a dataset.
- Nobody, admins included, can overwrite an existing key under `_versions/` or
  `_transactions/`: the proxy refuses such a PUT with 403 (`_transactions/` is checked with a
  HEAD; manifests with D45).
- Lance 13 runs no automatic cleanup unless `auto_cleanup_options` is set at creation, and the
  hub never sets it. Pruning is the explicit admin action `POST .../prune`.

### D44. Permissions

| Action | Who |
| --- | --- |
| Read a dataset (REST reads, S3 GET/HEAD/LIST) | org members; any signed-in user if `visibility = 'internal'` |
| Create a dataset, commit versions, edit the card, tag, restore, edit description | org members |
| Optimize (compaction, a new version) | org members |
| Rename, change visibility, prune, delete a dataset, S3 DELETE | org admins (super admins count as admins, as today) |

A read-only key (or read-only temporary credentials) can only read. A key scoped to an org
(`hub_keys.org_pk`) can only reach that org's datasets. Outsiders get 404 on private datasets
over REST, as with projects. The proxy answers `AccessDenied` (403) for any dataset the caller
can't read, whether it exists or not, so names don't leak.

### D45. Commits are made atomic by the proxy

Lance commits on `s3://` with a conditional PUT (`If-None-Match: *`) of the next manifest, and
treats 412 as "someone else committed; rebase and retry". **The Ceph RGW we test against
(v19.2 and v20.2, dbstore backend) accepts that header and ignores it**: 8 concurrent appends
left 2 versions and lost 6 silently. Production Ceph is untested. So the proxy enforces it
whatever the upstream does:

- A PUT whose key is under `_versions/` and ends in `.manifest` must carry
  `If-None-Match: *` (otherwise 403).
- The proxy buffers the manifest (they are small; refuse over 64 MiB), takes
  `pg_advisory_xact_lock(hashtextextended('hub:' || uuid, 0))`, HEADs the upstream key, answers
  412 `PreconditionFailed` if it exists, and otherwise PUTs it (forwarding `If-None-Match` too)
  and inserts the `dataset_commits` row, all in that one transaction. Lance then rebases and
  retries as usual.
- The version number is parsed from the manifest name: Lance's V2 names are
  `{u64::MAX - version:020}.manifest`; V1 names are `{version}.manifest`.

Every write must therefore go through the proxy, including the API's own writes (D49).

### D46. S3 keys and temporary credentials

Derivation and tokens are in `src/hub_credentials.py` (added in step 0), shared by the API and
the proxy.

- **Long-lived keys** (`hub_keys`): access key id `FLKH` + 16 random base32 characters;
  secret = base64url of `HMAC-SHA256(hub_key_secret, "key:" + access_key_id)`, unpadded. The
  secret is never stored and is shown once, at creation. Deleting the row revokes the key.
- **Temporary credentials**, `POST /api/v1/hub/credentials`, for `flock.hub`. Authenticated as
  any API request (an API key, as `flock.hub` sends). They last `FLOCK_HUB_CREDENTIALS_TTL`
  (default 12 h):
  - `session_token` = base64url(orjson `{"u": user_pk, "e": expires_at, "r": read_only,
    "a": access_key_id, "m": mirror_writer}`) + `.` + base64url(HMAC-SHA256(hub_key_secret,
    that first part)).
  - access key id `FLKT` + 16 random base32 characters; secret = base64url of
    `HMAC-SHA256(hub_key_secret, "temp:" + access_key_id)`.
  - The proxy verifies the token, its expiry and that `a` matches the request's access key id,
    without touching the database. Lance sends it as `X-Amz-Security-Token`
    (`aws_session_token`).
  - `m` (mirror writer) is only ever set on credentials the API mints for its own mirror
    imports (D50); it grants writes to the mirror org.
- Roles and visibility are always checked live, so removing someone from an org takes effect
  at once, whatever their credentials.

### D47. The proxy is its own process

`app/api/src/hub_proxy/` is an ASGI app served by `uvicorn src.hub_proxy.app:app` from the
same image as the API, as the compose service `hub-s3` (port 9000). It doesn't take the API's
`SERVER_LOCK`, so it can run several replicas, and it keeps no state of its own:

- **Authorization data** (datasets by `(org, name)`, memberships, keys) is read from Postgres
  and cached for 10 s. The API sends `NOTIFY hub_invalidate` when a key is deleted, a
  membership changes or a dataset is renamed, deleted or has its visibility changed; the proxy
  `LISTEN`s and drops its cache.
- **SigV4.** It verifies `AWS4-HMAC-SHA256` in the `Authorization` header and in presigned
  query strings, accepting any region in the credential scope. `x-amz-content-sha256` may be
  `UNSIGNED-PAYLOAD` or a hex digest, which is forwarded so the upstream checks the body as it
  streams. `STREAMING-AWS4-HMAC-SHA256-PAYLOAD` gets 501 `NotImplemented`. Upstream requests are
  re-signed with the global key by a small signer in the same package (no botocore).
- **Operations:** GetObject (with Range), HeadObject, ListObjectsV2 (keys in lexical order,
  upstream `LastModified` kept, since Lance's cleanup judges age by it), PutObject,
  CreateMultipartUpload / UploadPart / CompleteMultipartUpload / AbortMultipartUpload /
  ListParts, DeleteObject and DeleteObjects (admins), CopyObject within one dataset, ListBuckets.
  Anything else gets 501.
- **Streaming.** Bodies stream both ways (`request.stream()` into httpx, `aiter_raw()` into a
  `StreamingResponse`), passing `Content-Length`, `Content-Range`, `ETag`, `Last-Modified`
  and `Accept-Ranges` through. Only manifests are buffered (D45).
- **Errors** are S3 XML (`<Error><Code>AccessDenied</Code>...`), never the API's JSON.
- `hub_keys.last_used_at` is updated at most once a minute per key.

### D48. Datasets in the API

- `core/datasets.py` holds a `Datasets` subsystem on `Store`, loaded at startup like the
  others (datasets by org and name, in memory). It is not called "hub": `core/live.py` already
  has a `Hub` class. `core/hub_keys.py` holds keys and credentials.
- Routers: `api/datasets.py` and `api/hub.py` (keys, credentials, mirrors).
- **Reading Lance** (`pylance`, in `asyncio.to_thread`) goes straight to the upstream with the
  global key, through an LRU of open datasets keyed by `(uuid, version)`.
- Deleting a dataset sets `deleted_at` and frees the name; a background task deletes the
  objects under its prefix (directly upstream, with the global key) and then the row.

### D49. The API writes through the proxy, as the user

Card edits, tags, restore, optimize and prune are Lance writes, so they go through the proxy
(D45) with temporary credentials the API mints for the signed-in user. Authorship and
permissions then work exactly as for any client. `FLOCK_HUB_PROXY_URL` is where the API reaches
the proxy; `FLOCK_HUB_PROXY_PUBLIC_URL` is what clients are told.

Until the proxy exists (phase 1), the API's tests set `FLOCK_HUB_PROXY_URL` to the test S3
server itself with the global key; the code path is the same.

### D50. Hugging Face mirrors

- Mirrors live in the org `FLOCK_HUB_MIRROR_ORG` (default `hf`), which `Datasets.load()`
  creates if it doesn't exist. Its datasets are `internal`. Names are
  `<owner>--<name>` plus `--<config>` when a config is given: `cais/mmlu` with config `all` is
  `hf/cais--mmlu--all`.
- `POST /api/v1/hub/mirrors {repo, config?, revision?}` resolves `revision` (default `main`) to a
  commit sha with the HF API, then:
  - if a finished mirror of `(repo, config, sha)` exists, returns it (200);
  - if one is running, returns it (202), so concurrent requests share one import;
  - if the data files are larger than `FLOCK_HUB_AUTO_IMPORT_MAX_BYTES` (default 5 GB), refuses
    with 422 and a `command` field holding the `flock-hub import ...` command line to run instead;
  - otherwise starts `python -m flock.hub.importer` as a subprocess with mirror-writer
    credentials (D46) and `FLOCK_HUB_HF_TOKEN`, and returns 202.
- `GET /api/v1/hub/mirrors/{id}` reports its state. A server restart marks running mirrors
  failed; asking again starts over.

### D51. Web routes are HF-style

`/datasets/[entity]` and `/datasets/[entity]/[dataset]` (in the `(app)` group), so they never
clash with `/[entity]/[project]`. `datasets` joins `RESERVED` org names.

---

## 3. REST API

All paths are under `/api/v1`. Times are seconds since the epoch, as elsewhere.

**Dataset** (`DatasetInfo`):

```json
{
  "id": "6f1c...-uuid", "org": "flock", "name": "fineweb-sample",
  "description": "", "visibility": "private",
  "uri": "s3://datasets/flock/fineweb-sample",
  "created_by": {"email": "a@b.c", "name": "A"}, "created_at": 0.0, "updated_at": 0.0,
  "latest_version": 4, "rows": 1000, "bytes": 123456,
  "role": "admin",
  "mirror": {"repo": "cais/mmlu", "config": "all", "sha": "..."}
}
```

`latest_version`, `rows` and `bytes` are `null` before the first write; `role` is the caller's
role in the org (`null` for outsiders reading an internal dataset); `mirror` is `null` for
datasets that aren't mirrors.

**Version** (`DatasetVersion`):

```json
{
  "version": 3, "created_at": 0.0, "author": {"email": "a@b.c", "name": "A"},
  "op": "Append", "message": "add shard 7", "rows": 1000, "bytes": 123456,
  "fragments": 8, "tags": ["v1.0"], "pruned": false
}
```

| Endpoint | Body / query | Answer |
| --- | --- | --- |
| `GET /datasets/{org}` | | `{"datasets": [DatasetInfo]}` |
| `POST /datasets/{org}` | `{name, description?, visibility?}` | 201 `DatasetInfo` |
| `GET /datasets/{org}/{name}` | | `DatasetInfo` |
| `PATCH /datasets/{org}/{name}` | `{name?, description?, visibility?}` | `DatasetInfo` |
| `DELETE /datasets/{org}/{name}` | | 204 |
| `GET .../versions` | `offset`, `limit` (default 50) | `{"versions": [DatasetVersion], "total"}`, newest first |
| `PATCH .../versions/{v}` | `{message}` | `DatasetVersion` (D42) |
| `GET .../versions/{v}` | | `DatasetVersion` + `{"schema": [{name, type, nullable}], "features": Features.to_dict(), "metadata": {...}}` |
| `GET .../rows` | `version?`, `offset`, `limit` (≤ 500), `columns?` (comma list) | `{"version", "offset", "total", "columns": [{name, type}], "rows": [[cell]], "truncated": [[row, col]]}` |
| `GET .../readme` | `version?` | `{"version", "sha", "content"}` (`sha`/`content` `null` without a card) |
| `PUT .../readme` | `{content, message?}` | `{"version"}` |
| `PUT .../tags/{tag}` | `{version}` | `{"tags": {name: version}}` |
| `DELETE .../tags/{tag}` | | `{"tags": {...}}` |
| `POST .../restore` | `{version, message?}` | `{"version"}` (the new one) |
| `POST .../optimize` | | `{"version"}` |
| `POST .../prune` | `{older_than_days}` | `{"versions_removed", "bytes_removed"}` |
| `GET /me/hub-keys` | | `{"keys": [{pk, name, access_key_id, read_only, org, created_at, last_used_at}]}` |
| `POST /me/hub-keys` | `{name, read_only?, org?}` | 201, the key plus `secret_access_key` and `endpoint` (shown once) |
| `DELETE /me/hub-keys/{pk}` | | 204 |
| `POST /hub/credentials` | `{read_only?}` | `{endpoint, region, access_key_id, secret_access_key, session_token, expires_at}` |
| `POST /hub/mirrors` | `{repo, config?, revision?}` | 200/202 `{id, state, repo, config, sha, dataset, version, error}`, or 422 with `command` |
| `GET /hub/mirrors/{id}` | | as above |

Row cells: strings longer than 2000 characters are cut, and `[row, col]` is listed in
`truncated`; binary cells are `{"$binary": <size>}`; nested values are JSON; timestamps are
ISO 8601 strings. Names follow the project name rules (`projectNameError`).

## 4. Settings

`FLOCK_HUB_S3_ENDPOINT`, `FLOCK_HUB_S3_REGION` (default `us-east-1`), `FLOCK_HUB_S3_ACCESS_KEY_ID`,
`FLOCK_HUB_S3_SECRET_ACCESS_KEY`, `FLOCK_HUB_BUCKET` (default `flock-hub`), `FLOCK_HUB_PROXY_URL`,
`FLOCK_HUB_PROXY_PUBLIC_URL`, `FLOCK_HUB_CREDENTIALS_TTL` (default 12 h), `FLOCK_HUB_MIRROR_ORG`
(default `hf`), `FLOCK_HUB_AUTO_IMPORT_MAX_BYTES` (default 5 GB), `FLOCK_HUB_HF_TOKEN`. The hub
is off (its routes answer 503) while `FLOCK_HUB_S3_ENDPOINT` is unset. Each one goes in both
compose files (`tests/app/test_settings.py` checks) and in `docs/source/configuration.md`.

## 5. Plan

| Step | Branch | What |
| --- | --- | --- |
| 0 | `feat/introduce-hub` | This doc, migration `0009_hub.sql`, the Ceph RGW test fixture (`tests/conftest.py`), the empty `flock.hub` package. |
| 1 | `feat/hub-api` | D41–D44, D46 (keys and credentials), D48, D49 (pointed at the test S3 server), settings, docs pages. |
| 1 | `feat/hub-proxy` | D45, D47; compose service `hub-s3`. |
| 2 | `feat/hub-web` | D51 and the pages. |
| 2 | `feat/hub-import` | D50, with `flock.hub.importer`. |
| 3 | `feat/hub-e2e` | The API and `flock.hub` default to the proxy; end-to-end tests. |

## 6. How it was actually built

Filled in as each step lands.
