# 009: an S3 proxy with org buckets and per-user, rotating keys

| | |
| --- | --- |
| Status | proposed |
| Date | 2026-10-27 |
| Builds on | [003](003-organizations-and-auth.md), [008](008-cli-login.md) |
| Amends | D18 (orgs own buckets, and org pks stay below 100000), D24 (new `s3`, `orgs/{org}/buckets` and `me/s3-keys` routes), D51 (`flock.cli` gains S3 credentials) |
| Keeps | D2, D19 (roles), D20 (API keys), D47 (CLI tokens) |

## 1. Goal

Cerulean's object storage is a Ceph RGW server with one global access key, so anyone who
uses it holds the key to everything. This stage puts Flock in front of it as an authentication
layer: an S3-compatible proxy that checks per-user keys issued by Flock, then forwards each
request upstream signed with the global key.

1. **Optional.** The proxy is a separate service. A deployment without the `FLOCK_S3_*` settings
   has no S3 features, and nothing else changes.
2. **Buckets per org.** Each org creates and deletes as many buckets as it needs.
   Flock records them in a table, with who did what, and each is a real Ceph bucket named
   `{org pk}-{name}`. The proxy holds no state: it authenticates and remaps names.
3. **Keys rotate by default.** A signed-in user swaps their CLI token (D47) for an
   **ephemeral S3 key**. Each org sets how long those live (8 hours by default) and the longest
   any of its keys may live (30 days by default).
4. **Easy from every Flock package.** `flock.cli.s3` hands out credentials and renews them
   before they expire, in the shapes boto3, Lance, fsspec and the AWS CLI want.
   `flock s3 credentials` does the same from the shell.
5. **Long-lived keys from the UI.** For tools that can't renew (a notebook, an external
   service), the account page issues keys that last longer, up to never, within the org's
   policy.

**Out of scope:**
- datasets and the hub;
- per-prefix or per-object permissions, bucket policies and ACLs;
- several upstreams;
- keys limited to some of an org's buckets;
- quotas and usage accounting;
- presigned URLs signed by Flock keys, which come with the hub's browser downloads.

---

## 2. Design decisions

The numbering continues from 008.

### D53. Buckets: a table in Flock, `{pk}-{name}` in Ceph

The proxy still only authenticates and remaps. It has no state of its own, and reads
everything from the API's Postgres. **Flock's table is the record of which buckets exist**;
the API keeps Ceph in step with it.

- **The table.**

  ```sql
  s3_buckets(pk, org_pk → orgs CASCADE, name, upstream_name UNIQUE,
             created_at, created_by → users SET NULL, deleted_at NULL)
  -- UNIQUE (org_pk, name) WHERE deleted_at IS NULL

  s3_bucket_events(pk, bucket_pk → s3_buckets CASCADE, kind, user_pk → users SET NULL,
                   at, detail jsonb)                -- created, deleted, purged
  ```

  Every create and delete writes an event with who did it, and through what: the UI, the
  CLI or the API, and the session or key used. The org page shows each bucket's creator and its
  history.
- **The naming rule upstream** is `{org pk}-{name}`, with the org's pk zero-padded to five
  digits. Bucket `checkpoints` of org 17 is `00017-checkpoints`.
  - **Bucket names in Flock** therefore get S3's 63 characters minus the six of `00017-`: **57
    characters** of lowercase letters, digits, hyphens and dots, starting and ending with a letter
    or digit. They're unique within the org.
  - **Org pks stay below 100000**, so the prefix is always six characters and the limit always
    57. Creating an org refuses (409, "this server can't hold more organizations") when its pk
    would be 100000 or more. The check runs in the insert's transaction, so a refused org leaves
    nothing behind. That holds whether or not S3 is on, so turning S3 on later never meets an
    org it can't name.
  - **The name is stored** in `upstream_name` when the bucket is created and never changes. The
    proxy reads it rather than rebuilding it.
  - **Org IDs aren't part of it**, so renaming an org (D18) doesn't touch its buckets.
  - **`FLOCK_S3_BUCKET_PREFIX`** (default empty) goes in front of every upstream name, and the
    name limit shrinks by its length. Production leaves it empty. Tests set it so they never
    touch other buckets on the same Ceph (Step 6).
- **What clients see.** A key covers one org (D55), so clients use the short name, and the proxy
  looks it up in the key's org: with an `acme` key, `s3://checkpoints/run-1/…` is
  `00017-checkpoints/run-1/…`. A name that isn't in the table is `NoSuchBucket`, whatever exists
  upstream. ListBuckets lists the org's rows.
- **Creating.** Members create buckets with `POST orgs/{org}/buckets {name}`, from the org page,
  or with `flock s3 buckets create` (D59). Flock inserts the row and the event, then creates the
  upstream bucket. If Ceph refuses, it removes both and passes the error on. S3's `CreateBucket`
  and `DeleteBucket` through the proxy answer `NotImplemented`, saying to use Flock, because the
  proxy never writes state.
- **Deleting.** Admins delete buckets with `DELETE orgs/{org}/buckets/{name}`. The row is
  soft-deleted, which frees the name in Flock at once. A background task then empties and
  removes the upstream bucket, and resumes after a restart. Creating the same name again before
  the purge finishes would collide upstream, so the API refuses it (409, "still being deleted").
- **No renames.** S3 has no rename call, and renaming in place needs Ceph's Admin Ops API
  (`bucket link --bucket-new-name`). On 2026-10-27 the global key was checked against the real
  Ceph: S3 calls work, but every Admin Ops call (`/admin/bucket`, `/admin/user`,
  `/admin/metadata/bucket`) is `AccessDenied`. Copying a whole bucket and switching would be
  slow and block writes, so buckets can't be renamed. To get a new name, create a bucket and
  copy into it.
- **Deleting an org** deletes its buckets the same way.
- **Path-style only.** Clients address `<endpoint>/<bucket>/<key>`. Every client Flock cares
  about supports it (boto3, the AWS CLI, Lance's object_store, s3fs, DuckDB), and `flock.cli.s3`
  sets it (D58).

### D54. Who can do what

Permissions are checked on every request, against live memberships (the proxy reads them, D61).
Removing someone from an org cuts off their keys for its buckets at once.

| Caller | Read (GET, HEAD, List) | Write (PUT, multipart, Copy), delete objects | Create bucket | Delete bucket |
| --- | --- | --- | --- | --- |
| org admin | yes | yes, with a read-write key | yes | yes |
| org member | yes | yes, with a read-write key | yes | no |
| anyone else | no (`NoSuchBucket`, so other orgs' buckets aren't revealed) | no | no | no |

Creating and deleting buckets happen in Flock (the UI, the CLI or the API), with the
caller's Flock credential, not through the proxy.

Members can delete objects. Buckets are shared working storage (checkpoints, shards, scratch),
where members need to clean up after themselves. Deleting a whole bucket is an admin's job.
Stricter rules, such as members writing and admins destroying objects, belong to the hub's
datasets.

### D55. S3 keys

All S3 keys are rows in a new table, `s3_keys`. There is one kind of key, ephemeral or
long-lived depending only on its expiry and on what issued it.

```sql
s3_keys(pk, access_key_id UNIQUE, user_pk → users CASCADE, org_pk → orgs CASCADE,
        name, read_only, created_at, expires_at NULL,       -- NULL: never
        session_pk → sessions CASCADE NULL,                 -- issued for a CLI token
        api_key_pk → api_keys CASCADE NULL,                 -- issued for an API key
        last_used_at)
```

- **Scoped to one org.** Each key covers all of one org's buckets, and bucket names resolve in
  that org (D53). The org's policy (D56) then decides the key's lifetime, and a key can't reach
  another org's data by accident. The CLI and the Python API default to the user's default org.
- **Access key ID:** `FLK` plus 17 random base32 characters (20 in all, like AWS's).
- **The secret is derived, never stored:** `HMAC-SHA256(s3_key_secret, access_key_id)`, as
  URL-safe base64. `s3_key_secret` is a random row the migration writes to `meta`, like
  `jwt_secret`. SigV4 needs the plain secret to check a signature, so it couldn't be stored
  hashed anyway. Deriving it means a database dump without `meta` leaks no keys.
- **Shown once**, when the key is created.
- **Revoked with the credential that issued it.**
  - An ephemeral key issued for a CLI token belongs to that CLI session. `flock logout`,
    revoking the machine on the account page, a password change or a reset all delete it, by
    cascade.
  - One issued for an API key goes when the key is deleted.
  - Long-lived keys from the UI belong to the user. Like API keys (D20), they survive password
    changes, and they're revoked on the account page.
- **Expired rows** are deleted by an hourly sweep in the API. The proxy refuses expired keys
  whether or not their rows have been deleted yet.
- **Migration:** `0011_s3.sql` adds `s3_buckets` and `s3_bucket_events` (D53), `s3_keys`, the
  `meta` row and the two org columns of D56.

### D56. Rotation: two settings per org

Each org has two settings, both editable by its admins with `PATCH orgs/{org}` and on the org
settings page:

| Setting | Column | Default | Meaning |
| --- | --- | --- | --- |
| Ephemeral key lifetime | `orgs.s3_ephemeral_ttl` | 8 hours | how long a key from `POST s3/credentials` (D57) lives when the caller doesn't ask for less |
| Maximum key lifetime | `orgs.s3_key_max_ttl` (NULL is never) | 30 days | the longest any key for this org may live, ephemeral or long-lived |

- **Ephemeral keys** live `min(requested, ephemeral lifetime)`. They can't outlive the
  ephemeral lifetime, so `flock.cli.s3` (D58) renews them before they expire.
- **Long-lived keys** (D60) take any expiry up to the maximum, including never when the maximum
  is never.
- **The ephemeral lifetime can't exceed the maximum.** A `PATCH` that would make it longer is a
  422, and lowering the maximum below it lowers it too.
- **So by default, keys from the CLI and Python rotate every 8 hours**, while keys made in the UI
  can last up to 30 days. Admins change either: the maximum to 1 day, 7, 30, 90 or 365 days, or
  never, and the ephemeral lifetime to anything up to the maximum. Lifetimes are stored in
  seconds, and the UI offers those presets.
- **Lowering the maximum applies to existing keys too.** The proxy refuses a key once it's older
  than the org's current maximum, so tightening the policy takes effect at once and no rows are
  rewritten.

### D57. Exchanging a Flock credential for an ephemeral key

`POST /api/v1/s3/credentials` `{org?, ttl?, read_only?}` returns `{endpoint, region, org,
access_key_id, secret_access_key, expires_at}`.

- **Who can call it:** a CLI token (the main case), or an API key, which is what CI uses through
  `FLOCK_TOKEN`. A browser session may call it too, but the UI uses D60 instead.
- **Defaults.** `org` defaults to the user's default org. The caller must be a member, or gets
  404 as for any org they can't see.
- **The key** is named `<hostname or key name> (ephemeral)`, linked to the CLI session or API
  key that asked for it, and appears on the account page under the machine that issued it.
- **No reuse.** Each call makes a new key. `flock.cli.s3` caches it per process and renews it
  5 minutes before it expires (D58), so a training job makes one call per process per ephemeral
  lifetime.
  Keys pile up only as fast as the hourly sweep clears them.
- **When the proxy isn't configured,** the route answers 404 `{"error": "S3 isn't enabled on
  this server"}`. `GET auth/config` gains `s3: {endpoint, region} | null`, so clients can tell
  before asking.

### D58. `flock.cli.s3`, the one way Flock packages get S3 access

A new module next to `flock.cli.credentials`. It uses httpx, which `flock-cli` already depends on.

```python
from flock.cli import s3

creds = s3.credentials(org="acme")       # S3Credentials, renewed in the background
creds.boto3_client()                     # boto3 S3 client, path-style (boto3 imported lazily)
creds.storage_options()                  # Lance / object_store options, refreshing
creds.fsspec_options()                   # s3fs.S3FileSystem(**…)
creds.env()                              # AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_ENDPOINT_URL, …
creds.uri("checkpoints", "run-1")        # "s3://checkpoints/run-1", resolved in acme
s3.create_bucket("checkpoints", org="acme")  # POST orgs/{org}/buckets
```

- **Renewal.** `S3Credentials` holds the current key and renews it once fewer than 5 minutes are
  left. Every accessor returns current values. For boto3, the client is built on botocore's
  `RefreshableCredentials`, so a long-lived client renews by itself. Lance gets a
  `storage_options_provider`-style callable where the installed version supports one, and
  otherwise current options on every call.
- **Pickling.** It pickles without its key, so DataLoader workers and subprocesses fetch their
  own. That's the pattern the hub branch's `HubConfig` used.
- **Credentials.** The Flock credential comes from `credentials.resolve()` (D51), so
  `flock login` or `FLOCK_TOKEN` is all a job needs.
- **Optional dependencies.** No S3 library is a dependency: each accessor imports its library
  when called and says what to install if it's missing.

### D59. `flock s3`: credentials and buckets

`flock s3 credentials [--org ORG] [--ttl DURATION] [--read-only] [--format env|json|process]`
issues an ephemeral key and prints it.

| Format | Prints | For |
| --- | --- | --- |
| `env` (default) | `export AWS_ACCESS_KEY_ID=…` lines, plus the endpoint and region | `eval "$(flock s3 credentials)"` in a shell or job script |
| `json` | the D57 response | other programs |
| `process` | the AWS CLI's `credential_process` JSON (`Version: 1`, `Expiration`) | `~/.aws/config` |

- **`process` is how third-party tools rotate keys too.** A profile with
  `credential_process = flock s3 credentials --org acme --format process` and
  `endpoint_url = <proxy>` makes the AWS CLI, boto3 and DuckDB's `credential_chain` fetch a new
  key whenever the last one expires, with nothing stored on disk.
- **`flock s3 configure [--org ORG] [--profile NAME]`** writes that profile into `~/.aws/config`
  (it asks before replacing one), so setting it up is one command.
- **`flock s3 buckets list|create|delete [--org ORG] NAME…`** manages the org's buckets through
  D53's routes, since the proxy doesn't create or delete them. `delete` returns once the purge
  has started.
- **Errors.** Without a sign-in, or when the server has no S3, the command exits 1 with the
  reason.

### D60. Long-lived keys in the UI

- **The account page** gets an "S3 keys" section next to API keys, shown only when
  `auth/config` says S3 is on. It has two parts:
  - A **create form**: name, org, read-only, and an expiry: 8 hours, 1 day, 7, 30, 90 or 365
    days, or never. Choices longer than the org's maximum are disabled, with the reason
    shown. The secret, the endpoint and ready-to-paste snippets (AWS CLI profile, boto3,
    environment variables) are shown once.
  - A **list** of the user's keys, with org, read-only, expiry and last use. Ephemeral keys are
    folded into one line per issuing machine ("3 ephemeral keys from `gpu-node-7`"). Every key
    can be revoked.
- **The routes:** `GET`/`POST me/s3-keys` and `DELETE me/s3-keys/{id}`. Creating one requires
  a browser session (D46's `SessionUserDep`). A leaked CLI token or API key can only mint
  ephemeral keys, never one that outlives the ephemeral lifetime.
- **The org page** gets a "Buckets" section and, in its settings, the two lifetimes of D56,
  editable by admins. The section has:
  - a list of buckets with creator and creation time, and each bucket's history;
  - a create form;
  - for admins, delete, with a type-the-name dialog like the project one.

### D61. The proxy service

The proxy is a new ASGI app in `app/api/src/s3_proxy/`, run with `uvicorn
src.s3_proxy.app:app`. It ships in the API's image and is its own compose service, `s3`, under
the compose profile `s3`. It takes no server lock, so it can run as many replicas as needed.

- **Reused from the hub branch** (`feat/introduce-hub`, `app/api/src/hub_proxy/`), trimmed of
  everything about datasets:
  - `sigv4.py`: verifies header-signed SigV4 requests (and presigned queries, kept for the
    hub), and re-signs upstream requests without botocore;
  - `chunked.py`: decodes `aws-chunked` bodies with their trailing checksums. Recent boto3 and
    the AWS CLI send these by default;
  - `s3xml.py`: S3's XML errors and listings;
  - the streaming request and response handling in `app.py`.
- **Operations:** GetObject (ranges), HeadObject, ListObjectsV2, ListObjects, ListBuckets,
  CreateBucket, DeleteBucket, HeadBucket, PutObject, the multipart calls (Create, UploadPart, Complete, Abort, ListParts), DeleteObject,
  DeleteObjects, CopyObject and UploadPartCopy (between buckets of the same org), and
  GetBucketLocation. Anything else answers 501 `NotImplemented`.
- **Rewriting.** Only bucket names change, through the table of D53: `checkpoints`, with an
  `acme` key, becomes its `upstream_name`, `00017-checkpoints`. ListBuckets answers from the
  table. Object keys, prefixes and continuation tokens pass
  through untouched. The upstream bucket name is mapped back where S3 echoes it: `<Name>` in
  listings, `<Bucket>` and `<Location>` in multipart results, and `x-amz-copy-source` on the
  way in. Conditional headers (`If-None-Match`, `If-Match`) are forwarded untouched, so Ceph
  enforces them.
- **Streaming.** Bodies stream through both ways and are never buffered, except the small XML
  ones the proxy rewrites.
- **Who's calling.** Key rows, memberships, buckets and org policies are read from the API's
  Postgres. The proxy has no tables of its own and never writes any but `last_used_at`. They're
  cached for 10 seconds, and `LISTEN s3_invalidate` drops entries as soon as the API changes a
  key, membership, bucket or policy (`NOTIFY` in the same transaction). Revoking a key takes effect
  within a round trip, and the steady state costs no database query per request.
- **`last_used_at`** is written at most once a minute per key, in the background.
- **Errors** are S3 XML: `InvalidAccessKeyId`, `SignatureDoesNotMatch`, `ExpiredToken` for an
  expired key, `AccessDenied`, `NoSuchBucket`, `NotImplemented`. Upstream errors pass through,
  with bucket names mapped back.

### D62. Settings

| Setting | Meaning |
| --- | --- |
| `FLOCK_S3_UPSTREAM_ENDPOINT` | the real S3 server, e.g. Ceph RGW. Unset means S3 is off |
| `FLOCK_S3_UPSTREAM_REGION` | default `us-east-1` |
| `FLOCK_S3_UPSTREAM_ACCESS_KEY_ID`, `FLOCK_S3_UPSTREAM_SECRET_ACCESS_KEY` | the global key |
| `FLOCK_S3_BUCKET_PREFIX` | put in front of every upstream bucket name, shortening the 57-character limit (D53); default empty, set by tests |
| `FLOCK_S3_PUBLIC_URL` | the proxy's address as clients use it, returned by D57 |

Both the API and the proxy read these. They go in both compose files and
`docs/source/configuration.md`.

---

## 3. Plan

One PR, `feat/s3-proxy`, stacked on `feat/cli-login` (#32). Run `tests/app`, `tests/cli` and the
new `tests/s3` after every step.

### Step 1. Migration and the keys subsystem

`0011_s3.sql`, then `core/s3.py`. The `S3` subsystem on `Store` covers:
- buckets: create (row and event, then upstream), soft delete and the resumable background
  purge, for a bucket or a whole org, and the events;
- issuing ephemeral and long-lived keys;
- the policy arithmetic of D56;
- revocation;
- the hourly sweep;
- `NOTIFY s3_invalidate`.

The secret derivation goes in `src/s3_credentials.py`, shared with the proxy.

### Step 2. API routes

`POST s3/credentials`, `orgs/{org}/buckets`, `me/s3-keys`, the two lifetimes on
`PATCH orgs/{org}` and in the org's details `s3` in `auth/config`. Settings per D62.

### Step 3. The proxy

Port `sigv4.py`, `chunked.py` and `s3xml.py` from `feat/introduce-hub`, with their tests. Then
write `s3_proxy/auth.py` (cache, LISTEN, policy checks) and `s3_proxy/app.py` (routing,
rewriting, streaming). Add the compose service.

### Step 4. `flock.cli`

Write `s3.py` (D58), and the `flock s3 credentials`, `flock s3 configure` and `flock s3 buckets` commands (D59).
Add docs pages for both.

### Step 5. Web

The account page's S3 keys section, and the org page's buckets section and lifetime settings
(D60), with types and specs.

### Step 6. Tests

- **The S3 fixture.** `tests/conftest.py` gets the hub branch's `S3Server` fixture. By default
  it runs moto in-process. With `FLOCK_TEST_S3_ENDPOINT`, `_ACCESS_KEY` and `_SECRET_KEY` set
  (from the repo's `.env` `S3_ENDPOINT`, `S3_ACCESS_KEY` and `S3_SECRET_KEY`), it runs against
  the real Ceph. There, the proxy runs with `FLOCK_S3_BUCKET_PREFIX=flock-test-<run id>-`, so
  tests only create, empty and delete buckets with that prefix, and a session teardown removes
  any left behind. Existing buckets on the server (`pve`, `twccimage`, `flock-hub-test`) are
  never touched.
- `tests/app/test_s3_keys.py`:
  - issuing and lifetimes, for both kinds of key;
  - both lifetimes, including the ephemeral one capped by the maximum, and lowering the maximum
    refusing existing keys;
  - cascades from logout, revoking a CLI session, a password change and deleting an API key;
  - the browser-only rule for long-lived keys;
  - the sweep;
  - 404 when S3 is off.
- `tests/s3/`, driven by boto3, the AWS CLI and Lance through the proxy:
  - every operation of D61;
  - aws-chunked uploads;
  - multipart uploads;
  - listings with prefixes and pagination;
  - `If-None-Match` races (Ceph enforces them);
  - the permission matrix of D54;
  - read-only keys;
  - an expired key;
  - a revoked key, cut off within one request through NOTIFY;
  - a removed member;
  - bucket creation, with its upstream name and its event;
  - name rules and the 57-character limit;
  - org creation refused at pk 100000 (with the sequence advanced), leaving no row;
  - `CreateBucket` and `DeleteBucket` through the proxy refused;
  - ListBuckets showing only the org's buckets;
  - two orgs with a bucket of the same name, each key seeing only its own;
  - a name that exists upstream but not in the table is `NoSuchBucket`;
  - deleting a bucket, and recreating the name refused until the purge is done;
  - deleting an org purging its buckets, resumed after a restart;
  - renaming an org leaving its buckets alone;
  - `credential_process` rotation with a short TTL.
- `tests/cli/test_s3.py`: the three formats; `configure` writing and not clobbering a profile;
  `S3Credentials` renewing before expiry and pickling without its key.
- **Real Ceph.** CI runs moto. The full `tests/s3` suite runs once against Ceph before the PR is
  opened, and the PR says so.

### Step 7. Docs and status

- Docs pages: S3 access in the API docs, and the CLI's `s3` pages.
- A README section.
- Mark this file `applied`, with a "how it was actually built" section.

---

## 4. Known gaps after this stage

- **The hub's datasets** will need their own place: either reserved bucket names in each org or a
  namespace of their own. Their design decides.
- **Keys can't be limited** to some of an org's buckets.
- **No bucket renames** (D53): the global key has no Ceph admin rights.
- **Buckets created upstream outside Flock** aren't adopted, and Flock doesn't notice buckets
  removed outside it.
- **At most 99999 orgs** can ever be created, counting deleted ones.
- **No usage accounting** per org, bucket or key, and no quotas.
- **An upstream outage looks like S3 errors**, with no health page of its own beyond the
  service's `/healthz`.
- **Upstream buckets are reachable with the global key**, outside the proxy. The proxy
  authenticates Flock's users; it can't stop someone who holds the global key.
