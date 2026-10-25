# Running the hub's S3 proxy

The hub keeps datasets in an S3-compatible store (Ceph RGW in production) that has one global key. Clients never get that key. They talk S3 to `hub-s3`, a separate process that checks each request's signature and permissions, then forwards it to the store re-signed with the global key.

## What it does

- **One bucket.** Clients see a single bucket, `datasets`, addressed path-style: a dataset's Lance URI is `s3://datasets/<org>/<dataset>`. In the store, the same objects live under `datasets/<uuid>/` in `FLOCK_HUB_BUCKET`, so renaming a dataset moves nothing.
- **Credentials.** It accepts S3 keys from the account page (`FLKH…`) and the temporary credentials `flock.hub` gets with an API key (`FLKT…` plus a session token). Signatures are AWS SigV4, in the `Authorization` header or a presigned URL, for any region.
- **Permissions.** Org members read and write their org's datasets, and anyone signed in reads `internal` ones. Read-only keys only read, and org-scoped keys only reach that org. Only org admins can delete objects. Nobody can overwrite a dataset's existing versions (`_versions/`) or the transaction files (`_transactions/`) they refer to. A dataset you can't read answers `AccessDenied`, whether it exists or not.
- **Atomic commits.** Lance commits a version by writing its manifest with `If-None-Match: *`. Some stores, including the Ceph RGW Flock is tested against, accept that header without enforcing it, so concurrent writers would silently overwrite each other's versions. The proxy enforces it itself, under a PostgreSQL lock per dataset, and records who wrote each version. Every write must therefore go through the proxy.
- **Operations.** GetObject (with ranges), HeadObject, ListObjectsV2, PutObject, multipart uploads, CopyObject within a dataset, DeleteObject and DeleteObjects. Anything else, including aws-chunked uploads, answers `501 NotImplemented`.

## Running it

It runs from the API's image with a different command, and needs the database URL and the store's settings:

```bash
uvicorn app.api.src.hub_proxy.app:app --host 0.0.0.0 --port 9000
```

| Variable | Default | |
| --- | --- | --- |
| `FLOCK_DATABASE_URL` | | the API's database |
| `FLOCK_HUB_S3_ENDPOINT` | required | the store's URL |
| `FLOCK_HUB_S3_REGION` | `us-east-1` | the store's region |
| `FLOCK_HUB_S3_ACCESS_KEY_ID`, `FLOCK_HUB_S3_SECRET_ACCESS_KEY` | required | the global key |
| `FLOCK_HUB_BUCKET` | `flock-hub` | the bucket datasets are stored in |
| `FLOCK_HUB_MIRROR_ORG` | `hf` | the org Hugging Face mirrors are written to |
| `FLOCK_POOL_SIZE` | `16` | maximum database connections |

In Docker Compose it is the `hub-s3` service, in the `hub` profile so it only starts once the store is configured:

```bash
docker compose --profile hub up -d
```

Route your S3 domain (the one `flock.hub` and S3 tools are given) to `hub-s3:9000`. `GET /healthz` answers `ok` without authentication, for health checks.

The proxy keeps no state of its own, so you can run several replicas. It caches keys, roles and datasets for 10 seconds, and drops the cache whenever the API announces a change (`NOTIFY hub_invalidate`), so revoking a key or a membership takes effect at once. It needs the API's migrations applied, so start it after the API.

## Setting up the bucket

Create `FLOCK_HUB_BUCKET` in the store, private, and give the global key full access to it. Add a lifecycle rule that aborts incomplete multipart uploads after 2 days, so uploads that clients abandon don't keep their parts forever:

```bash
aws s3api put-bucket-lifecycle-configuration --endpoint-url "$FLOCK_HUB_S3_ENDPOINT" \
  --bucket flock-hub --lifecycle-configuration '{"Rules": [{"ID": "abort-multipart",
  "Status": "Enabled", "Filter": {"Prefix": ""},
  "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 2}}]}'
```

Don't add rules that expire or delete objects: old versions are pruned explicitly by org admins.
