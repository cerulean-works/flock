# 014: a model registry, with immutable safetensors versions in each org's `flock-models` bucket

| | |
| --- | --- |
| Status | proposed |
| Date | 2026-10-30 |
| Builds on | [009](009-s3-proxy.md), [013](013-datasets-tab.md) |
| Amends | D54 (writes to `flock-models` go only through upload sessions), D85 (a second managed bucket kind, `'models'`) |
| Keeps | D53, D55–D62, D86–D96 |

## 1. Goal

Flock stores models as numbered, immutable versions with movable tags. A version is a folder of
safetensors shards and a few JSON files in S3, and Postgres is the registry that numbers them,
tags them and links them to runs and evals. This stage adds:

1. **A managed bucket.** Every org gets `flock-models`, created and owned by Flock as
   `flock-datasets` is (D97).
2. **A registry** in Postgres of models, versions, tags and files (D98).
3. **Upload sessions** that write each upload to its own folder, numbered only when it's
   committed, so a crash never leaves a half-written version (D99 to D101).
4. **Resumable uploads and downloads,** from a local journal and S3's own record of what landed
   (D102, D103).
5. **`save_to_hub` and `load_from_hub`** in `flock.hub.models`, and a `flock models` CLI (D104,
   D105).
6. **Links** from a version to the training run that made it and the eval runs that scored it
   (D106).
7. **Checkpoints from the trainer,** uploaded in the background (D107).
8. **A Models tab** with versions, tags and links (D108).

Saving and loading a model is one call each:

```python
from flock.hub import models

version = models.save_to_hub(model, "acme/olmo2-1b", tags=["primary"])
model = models.load_from_hub("acme/olmo2-1b@primary", device="cuda")
```

**Why not git.** Hugging Face stores models in git, with large files in LFS or Xet. That gives
branches, merges and diffs, which matter for text and little for weights. A model's history is a
straight line of large binary files, and a movable tag already does what a branch would. Git LFS
is slow for files of many gigabytes, which is why Hugging Face built Xet. And Flock's storage is
the S3 proxy, so a git server would be a second protocol to authenticate and run.

**Out of scope:**
- formats other than safetensors (other files can ride along in a version, but `load_from_hub`
  reads only safetensors);
- sharded saves from FSDP or tensor parallelism (rank 0 saves the full state dict for now);
- deduplicating files across versions;
- presigned URLs straight to Ceph, skipping the proxy (D101 says when to add them);
- uploading from the web app;
- model cards beyond a README in the version.

---

## 2. Design decisions

The numbering continues from 013.

### D97. The managed `flock-models` bucket

- **One per org,** upstream `{prefix}{org pk:05}-flock-models`, a row in `s3_buckets` with
  `managed = 'models'`. Everything D85 says about `flock-datasets` applies: it's ensured on org
  creation, at startup and when the Models tab loads, it can't be deleted, and an existing
  bucket with the name is adopted.
- **Read like any bucket.** Members read it with any key, through the proxy, the Storage tab or
  any S3 client.
- **Written only through upload sessions.** The proxy refuses every write and delete in the
  bucket, except PUT and the multipart calls under the prefix of an open upload that belongs to
  the caller (D99). This amends D54 for this bucket only. Refusals are `AccessDenied` with the
  message "flock-models is written through flock models push".
- **Deletes go through the API** (D109), never S3, so a committed version can't change under an
  eval that scored it.

### D98. The registry

```sql
models(pk, org_pk → orgs CASCADE, name, created_by → users SET NULL, created_at,
       deleted_at NULL, UNIQUE (org_pk, name) WHERE deleted_at IS NULL)

model_uploads(pk, ulid UNIQUE, model_pk → models CASCADE, user_pk → users SET NULL,
              state CHECK (state IN ('open', 'committed', 'aborted', 'expired')),
              files jsonb,              -- [{path, size}] as declared when opened
              metadata jsonb, tags text[], run_pk → runs SET NULL NULL, step bigint NULL,
              dedupe_key text NULL, created_at, last_activity_at)

model_versions(pk, model_pk → models CASCADE, number, upload_pk → model_uploads,
               digest, total_bytes, metadata jsonb, run_pk → runs SET NULL NULL,
               step bigint NULL, dedupe_key text NULL, created_by → users SET NULL,
               created_at, deleted_at NULL,
               UNIQUE (model_pk, number), UNIQUE (model_pk, dedupe_key))

model_files(version_pk → model_versions CASCADE, path, size, sha256, PRIMARY KEY (version_pk, path))

model_tags(model_pk → models CASCADE, name, version_pk → model_versions CASCADE,
           moved_by → users SET NULL, moved_at, PRIMARY KEY (model_pk, name))

model_version_runs(version_pk → model_versions CASCADE, run_pk → runs CASCADE,
                   kind CHECK (kind IN ('eval')), PRIMARY KEY (version_pk, run_pk))
```

- **Model names** are one segment: lowercase ASCII letters, digits, `.`, `-` and `_`, starting
  with a letter or digit, at most 100 characters. A model is written `org/name`.
- **Version numbers** start at 1, go up by one per commit, and are never reused, even after a
  version is deleted.
- **The digest** is the sha256 of the version's files, one line each, sorted by path:
  `{path}\t{size}\t{sha256}\n`. It names the exact bytes, as a commit hash does on the Hub.
- **Tag names** follow D96's rules, and can't be only digits, so `@12` always means version 12.
  `primary` is the tag tools load when no version is given. It's an ordinary tag otherwise.
- **Why a table.** D85 chose no table for datasets because any Lance writer must be able to
  write. Models are written only through Flock, need numbers handed out safely to concurrent
  writers, and need links that are queried both ways. None of that works from S3 alone.

### D99. Where an upload goes

Each upload attempt gets its own folder, named by a ULID that the API picks:

```
flock-models/
  olmo2-1b/
    u/01JD3KQ8.../
      model-00001-of-00003.safetensors
      model-00002-of-00003.safetensors
      model-00003-of-00003.safetensors
      model.safetensors.index.json
      config.json
      flock.json
      manifest.json                  written by the API after the commit
```

- **The number comes at commit.** A version row points at its upload's folder, and nothing is
  copied when it's committed. Numbering at the start instead would leave gaps for every crashed
  upload, and make two concurrent pushes race for a number.
- **The folder never moves.** A committed version is read from `u/{ulid}/` for as long as it
  exists. The Storage tab shows these folders as they are. The Models tab is where versions
  have numbers.
- **The prefix is the model's name.** Renaming a model isn't offered in this stage, so the name
  in the path never goes stale.
- **Required files:** `config.json`, `flock.json`, and either `model.safetensors` or the shards
  named in `model.safetensors.index.json`. Anything else (a README, tokenizer files, D107's
  `training_state/`) is allowed and listed with the version.

`flock.json` says how to rebuild the model:

```json
{
  "format": 1,
  "architecture": "flock.training.core.models.olmo2:Olmo2",
  "config_class": "flock.training.core.models.olmo2:Olmo2Config",
  "dtype": "bfloat16",
  "flock_commit": "fe37f98",
  "run": "acme/pretrain/3k9x2",
  "step": 12000,
  "datasets": {"train": "s3://flock-datasets/dolma3@41"}
}
```

### D100. Upload sessions

| Route | Does |
| --- | --- |
| `POST orgs/{org}/models/{name}/uploads` | opens one, creating the model if it's new |
| `GET orgs/{org}/models/{name}/uploads/{ulid}` | its state and declared files |
| `POST orgs/{org}/models/{name}/uploads/{ulid}/commit` | makes it a version |
| `DELETE orgs/{org}/models/{name}/uploads/{ulid}` | aborts it |
| `GET orgs/{org}/models/{name}/uploads?state=open` | your open uploads |

- **Opening** takes `files: [{path, size}]`, `metadata`, `tags`, and optionally `run`, `step`
  and `dedupe_key`. If a live version of the model already has the `dedupe_key`, it answers 200
  with that version and no upload, so a trainer that restarts doesn't push the same step twice.
  Otherwise it answers 201 with the `ulid`, `bucket`, `prefix` and the part size to use (64 MiB).
  Members and admins can open uploads, as D54 lets them write.
- **Committing** takes each file's `sha256`. The API then:
  1. Lists the prefix upstream with the global key, and checks the objects are exactly the
     declared files with the declared sizes. A missing file, an extra one, or a multipart
     upload still open under the prefix is 409 with the paths, and the upload stays open.
  2. Checks the required files (D99), and reads each shard's safetensors header with ranged
     GETs: 8 bytes for its length, then the header. A shard that isn't safetensors, or an index
     naming a tensor no shard holds, is 422.
  3. Checks each object's `x-amz-meta-sha256` against the committed `sha256`. A mismatch is 409.
  4. In one transaction: locks the model row, takes `max(number) + 1`, inserts the version, its
     files and its tags (moving tags already on other versions), and marks the upload
     `committed`.
  5. Writes `manifest.json` into the folder: the number, digest, files and metadata. If that
     fails it's logged, and the startup pass writes any that are missing. Postgres stays the
     record.
- **Committing twice** answers with the same version, so a client that crashed waiting for the
  answer can just ask again.
- **The API doesn't hash the bytes.** Reading every byte back would double the cost of a push.
  Transport errors are caught by `Content-MD5` on every part (D101), and whoever downloads checks
  each file's sha256 (D103).

### D101. How the client uploads

- **Through the proxy,** with boto3, a key from `S3Credentials` wrapped in botocore's refreshable
  credentials, so a push longer than a key's lifetime renews it rather than failing.
- **Multipart over 64 MiB,** in 64 MiB parts. A 5 GB shard is 75 parts, and S3's 10,000-part
  limit allows files of 640 GiB. Smaller files are one PUT.
- **One pool for all parts,** 32 concurrent requests by default (`FLOCK_MODELS_CONCURRENCY`),
  so a model of three shards uploads as fast as one of thirty.
- **Each part carries `Content-MD5`,** so S3 rejects a part damaged on the way. Each object gets
  `x-amz-meta-sha256`, set on `CreateMultipartUpload` or the PUT.
- **Hashing in the same pass.** The client reads each file once, hashing sha256 for the whole
  file and MD5 for the part as it sends it. sha256 is known only after the last part, so for
  multipart files the metadata is set from a hash computed before the upload starts. That costs
  one extra local read, at disk speed rather than network speed.
- **Retries** use botocore's standard mode with backoff, per request, so a network blip resends
  one part.
- **Activity.** Each write the proxy lets through sets the upload's `last_activity_at`, at most
  once a minute, as the proxy already does for keys.
- **When to skip the proxy.** The proxy streams (009) and runs several workers (#54). If a
  benchmark against Ceph shows it below what Ceph takes directly, a later stage adds presigned
  part URLs that go straight upstream.

### D102. Crashes and resuming

Every push starts from a local folder. `save_to_hub` writes the model to
`~/.cache/flock/uploads/{ulid}/` first, and the CLI pushes a folder that already exists. Next to
it, `journal.json` records what's needed to pick up again:

```json
{
  "model": "acme/olmo2-1b", "ulid": "01JD3KQ8...", "source": "/home/me/ckpt-12000",
  "files": {
    "model-00001-of-00003.safetensors": {
      "size": 4998012345, "mtime": 1730250000.1, "sha256": "…",
      "upload_id": "2~xYz…", "done": false
    }
  }
}
```

It's written before any byte is sent, and updated as each file finishes.

| Failure | What happens |
| --- | --- |
| A request fails | botocore retries it (D101). |
| The key expires | the refreshable credentials renew it first. |
| The process or machine dies mid-push | the upload stays `open`, with finished files and parts in S3 and the journal on disk. |
| It dies after sending the commit | committing again returns the same version (D100). |
| The trainer restarts and saves a step it already pushed | `dedupe_key` returns the existing version (D100). |
| The local files are gone | nothing can resume, so the upload expires (below). |
| Two pushes of one model at once | each has its own folder, and the commit's row lock numbers them in commit order. |

**Resuming** a journal:
1. Ask the API for the upload. If it's `expired` or `aborted`, open a new one and push the same
   files from the start.
2. Check each local file's size and modified time against the journal. A file that changed
   can't be resumed, so the push stops and says which file.
3. For each file not `done`:
   - **A whole object is there:** a HEAD shows the right size and `x-amz-meta-sha256`. Mark it
     done.
   - **A multipart upload is open:** ListParts gives each part's ETag. A part whose ETag is the
     MD5 of the same bytes on disk is kept, and the rest are sent. Then CompleteMultipartUpload.
   - **The multipart upload is gone:** start that file again.
4. Commit.

Resuming reads the local files once more but sends only what's missing.

**Where resumes start:**
- **`save_to_hub`** finishes any journal for the same model and source before starting a new
  push, unless `resume=False`.
- **`flock models push ./dir acme/olmo2-1b`** finds a journal for the same folder with the same
  sizes and modified times, and resumes it. `flock models uploads list` shows open uploads with
  their progress, and `flock models uploads resume {ulid}` and `abort {ulid}` act on one.
- **The trainer** finishes journals left by an earlier process before taking new checkpoints
  (D107).

**Cleaning up:**
- **Expiry.** An upload with no activity for 7 days becomes `expired`. The sweep (every 10
  minutes, claiming rows with `FOR UPDATE SKIP LOCKED` as D93's does) aborts its multipart
  uploads upstream with the global key and deletes its folder. An abort through the API does the
  same at once.
- **Local folders.** The client deletes the staging folder and journal once the commit answers,
  unless `keep_local=True`. Journals of committed or expired uploads are removed the next time
  the client looks.
- **Stray prefixes.** The sweep also removes `u/` folders older than a day with no upload row,
  left if a row was never inserted.

### D103. Downloading

- **Resolving.** `GET orgs/{org}/models/{name}/resolve?ref=` takes a number or a tag and answers
  with the number, digest, files (path, size, sha256), metadata and the folder's prefix. A tag
  is resolved once, and everything after uses the number.
- **The cache** is `~/.cache/flock/models/{org}/{name}/{digest}/`, keyed by digest, so a moved
  tag never serves stale bytes and two names for one version share files.
- **Each file** downloads to `{path}.partial` with up to 32 concurrent ranged GETs. After a
  crash, the download resumes from the partial file's size. A file is renamed into place only
  after its sha256 matches. A mismatch deletes the partial file and fails with the path.
- **Several processes** downloading the same version (one per rank) share one download through a
  file lock on the version folder. A `.complete` marker means every file has been checked.
- **Read-only keys** are enough, so jobs that only load models never hold a key that writes.
- **Skipping files.** `allow` and `ignore` patterns choose which files download.
  `training_state/` is skipped unless asked for (D107).

### D104. `save_to_hub` and `load_from_hub`

`flock.hub.models` holds both, behind the `models` extra (`flock-hub[models]`, adding torch and
safetensors), so installing `flock-hub` for datasets stays light.

```python
def save_to_hub(
    model: BaseModel | str | os.PathLike,  # a model, or a folder that already has the files
    name: str,                             # "org/name"
    *,
    tags: Sequence[str] = (),
    metadata: Mapping[str, Any] | None = None,
    run: wandb.Run | None = None,
    step: int | None = None,
    dedupe_key: str | None = None,
    training_state: Mapping[str, Any] | None = None,
    shard_size: int = 5 * 2**30,
    blocking: bool = True,
    resume: bool = True,
    keep_local: bool = False,
) -> ModelVersion | PendingUpload: ...

def load_from_hub(
    ref: str,                               # "org/name@12", "org/name@primary" or "org/name"
    *,
    device: str | torch.device | None = None,
    dtype: torch.dtype | None = None,
) -> BaseModel: ...

def download(ref: str, *, allow=None, ignore=None, include_training_state=False) -> Path: ...
```

- **Saving a model:**
  - `BaseModel` gains a declared `config` attribute, which must be a frozen dataclass. `Olmo2`
    already has one. `config.json` is `dataclasses.asdict(model.config)`, and `flock.json`
    records the model's and config's import paths (D99).
  - The state dict goes through safetensors' `save_model` in shards of at most `shard_size`
    bytes, with Hugging Face's index file when there's more than one. `save_model` handles
    tensors that share storage.
  - `run` and `step` go into `flock.json` and onto the version (D106). With a `run` and a
    `step`, `dedupe_key` defaults to `{run id}:{step}`.
- **`blocking=False`** returns a `PendingUpload` once the files are on local disk. Its
  `result()` waits for the version, and its `done()` says if it's there. The upload runs in a
  background thread.
- **Loading:** `load_from_hub` downloads the version (D103), imports the `config_class` and
  `architecture` from `flock.json`, builds the model on the meta device so no random
  initialization runs, and loads the weights with `load_state_dict(assign=True, strict=True)`.
  The model then goes to `device` (default CUDA when available) and `dtype` (default the
  version's).
- **Only Flock's own classes.** `architecture` and `config_class` must be under
  `flock.training`. Anything else is refused, so loading a model never imports code named by
  the files it downloaded.
- **`ModelVersion`** has `org`, `name`, `number`, `digest`, `files` and `metadata`, and prints
  as `acme/olmo2-1b@12`.

### D105. The `flock models` CLI

| Command | Does |
| --- | --- |
| `flock models push DIR ORG/NAME [--tag T]…` | pushes a folder (D102 for resuming) |
| `flock models pull ORG/NAME[@REF] [--to DIR]` | downloads into the cache, or copies to `DIR` |
| `flock models list [--org]` | models, with their latest version and `primary` |
| `flock models versions ORG/NAME` | versions, newest first, with tags and sizes |
| `flock models tag ORG/NAME@REF TAG` | adds or moves a tag |
| `flock models untag ORG/NAME TAG` | removes a tag |
| `flock models uploads list\|resume ULID\|abort ULID` | open uploads (D102) |

Each command shows progress with bytes sent and the rate, and `push` ends by printing the
version, for example `acme/olmo2-1b@12`.

### D106. Links to runs and evals

- **Training runs.** A version stores the run that made it (`run_pk`) and the `step`, from
  `save_to_hub`'s `run`. The run is resolved from the wandb run's entity, project and id. An
  unknown run is stored in `flock.json` only, and the version is still committed.
- **Eval runs.** `flock eval` accepts `flock:acme/olmo2-1b@primary` wherever it takes
  `repo@revision`. `ModelRef` grows a second kind that resolves to `flock:acme/olmo2-1b@12`,
  and the provenance's `model` records that, with the digest in a new `model_digest` field.
- **Linking.** When the API receives a run's config, as `log_to_tracker` sends it, and the
  config's `model` starts with `flock:`, it inserts a `model_version_runs` row with kind `eval`.
  A ref that doesn't resolve (another org's model, a deleted version) is ignored, and the run is
  stored as usual.
- **Queries both ways:** a version lists its training run and its eval runs, and a run's page
  lists the versions it made.

### D107. Checkpoints from the trainer

- **`TrainerConfig`** gains `checkpoint_every: int | None`, `checkpoint_model: str | None` (for
  example `acme/olmo2-1b`) and `checkpoint_tags`. Every `checkpoint_every` steps, and after the
  last one, the trainer calls `save_to_hub(..., blocking=False)` with its run and step.
- **Training waits only for the disk.** The weights are copied to the CPU and written to the
  staging folder, and training goes on while they upload.
- **At most 2 pending checkpoints** (`max_pending_checkpoints`). Saving past that waits for the
  oldest to commit, so a slow network can't fill the disk.
- **On start,** the trainer resumes any journals left for `checkpoint_model` (D102).
- **Optimizer state** goes in `training_state/` in the same version, as safetensors:
  `optimizer.safetensors`, plus `trainer.json` with the step, the scheduler's state and the RNG
  states. It's there to resume training, so `load_from_hub` and `download` skip it unless
  `include_training_state=True`. `checkpoint_training_state` (default `True`) turns it off.
- **Rank 0 saves** in this stage. Sharded saves from FSDP are a later stage.

### D108. The Models tab

- **The sidebar** gets "Models" (lucide `box`) between Datasets and Storage, shown when S3 is
  on, as Datasets is (D86). `models` joins `RESERVED`, and migration 0015 refuses to run if an
  org is named `models`.
- **Routes:**

  | URL | Page |
  | --- | --- |
  | `/models/{org}` | the org's models: name, latest version, `primary`'s version, size, updated |
  | `/models/{org}/{name}` | a model, at `?version=` (a number or tag, default `primary`, else the latest) |

- **The model page** has a header with the version picker, size, and actions: Load (a dialog with
  the `load_from_hub` and `flock models pull` snippets for the version shown), Browse files (the
  folder in Storage) and Delete (D109). Tabs, kept in `?tab=`:

  | Tab | Shows |
  | --- | --- |
  | Overview (default) | the README if the version has one, `config.json` as a table, and `flock.json` |
  | Versions | newest first: number, tags, size, run and step, who pushed it and when, with View and Add tag |
  | Evals | the version's eval runs, each task's metrics side by side, linked to the runs |
  | Files | each file's path, size and sha256 |

- **The run page** gets a Checkpoints section listing the versions the run made, with their
  steps and tags.
- **Tags** are added, moved and removed as D96 does for datasets, by members and admins.

### D109. Deleting versions and models

- **A version** is deleted from the Versions tab, by members and admins, with the type-the-name
  dialog asking for `acme/olmo2-1b@12`. A version with a tag can't be deleted until the tag is
  moved or removed. A version with eval runs can be deleted, after a warning that says how many.
- **What deleting does:** the row gets `deleted_at` and its files move to the trash bucket
  (D93's, at `{org pk:05}/models/{version pk}/`). Admins restore or delete it for good from a
  "Deleted models" section in org settings, and the sweep removes it after 14 days. A restored
  version keeps its number.
- **A model** is deleted the same way, with every version in it, and restored the same way.
- **Retention** is an org setting, on the org settings page: "Keep the newest N untagged versions
  of each model", off by default. When on, the sweep deletes older versions that have no tag and
  no eval runs, as above, so they wait 14 days in the trash.

---

## 3. Plan

Four stacked PRs. The first branches from `feat/model-registry`, which holds this doc and
branches from `feat/scaffold-training-loop`, and each next one from the one before.

### PR 1. Registry and uploads (`feat/model-registry-api`)

- Migration `0015_models.sql`: `s3_buckets.managed` accepts `'models'`, the tables of D98, the
  retention setting, and the `models` org name refused.
- `ensure_managed` for `flock-models` (D97).
- `core/models.py` and `api/models.py`: upload sessions and commits (D100), resolving (D103),
  versions, tags, deleting and the trash (D109).
- The proxy's write rule for `flock-models` and its cache of open uploads, cleared over the bus
  on commit, abort and expiry (D97, D101).
- The sweep: expiry, stray prefixes, the trash and retention (D102, D109). The startup pass for
  missing `manifest.json` files.

### PR 2. Client (`feat/model-registry-client`)

- `flock.hub.models`: uploading with the journal and resuming (D101, D102), downloading and the
  cache (D103), `save_to_hub`, `load_from_hub` and `download` (D104).
- `BaseModel.config`.
- The `flock models` CLI (D105).

### PR 3. Training and eval (`feat/model-registry-training`)

- The trainer's checkpoint options and background uploads (D107).
- `flock:` refs in eval, `model_digest` in the provenance, and the API's eval links (D106).

### PR 4. Web (`feat/models-tab`)

- The sidebar item, the list and model pages, the run page's Checkpoints section, the tag and
  delete dialogs, and the "Deleted models" and retention sections in org settings (D108, D109).

### Tests

- **`tests/app/test_models.py`,** on moto:
  - the managed bucket, created, adopted and refused deletion;
  - the proxy: writes outside an open upload refused, writes by another user refused, writes
    after commit refused, deletes refused, reads allowed;
  - opening, committing and committing twice; missing, extra and resized files; an open
    multipart upload at commit; a non-safetensors shard; an index naming a missing tensor; a
    sha256 that doesn't match the metadata;
  - two commits at once getting consecutive numbers;
  - `dedupe_key` returning the existing version;
  - expiry with a moved clock, aborting and deleting what landed; stray prefixes;
  - tags: adding, moving, digits-only names refused, resolving at a moved tag;
  - deleting and restoring versions and models, tagged versions refused, retention;
  - eval links from a run config, and unresolvable refs ignored.
- **`tests/s3/test_models.py`:**
  - a round trip of a small `Olmo2`, sharded and not, giving equal tensors;
  - resuming after a crash after each step: mid-part, between parts, between files, before the
    commit, and after the commit was sent;
  - a changed local file refusing to resume;
  - an expired upload starting over;
  - a download resumed from a partial file, a corrupted file rejected, two processes sharing one
    download;
  - `architecture` outside `flock.training` refused;
  - `training_state/` skipped unless asked for.
- **Against Ceph** with `FLOCK_TEST_S3_*`, in `flock-hub-test`: the proxy tests, the round trip,
  and a resume. These also check the two things D102 relies on: that RGW's part ETags are the
  parts' MD5, and that it keeps `x-amz-meta-*` on multipart objects. If the ETags aren't MD5s,
  the journal stores each part's MD5 as it's sent, and resuming compares against that instead.
- **A benchmark,** not in CI: push and pull a 10 GB model against Ceph through the proxy and
  straight upstream, to see whether D101's presigned URLs are worth a stage.
- **Web specs** for each page and dialog.
- **Manual:** train a tiny `Olmo2` with `checkpoint_every`, kill the process during an upload,
  restart it, see the upload resume and the run's checkpoints appear, evaluate one with
  `flock:` and see the eval on the model page.

### Docs and status

- A guide page, "Storing models", for `save_to_hub`, `load_from_hub`, the CLI and the trainer's
  checkpoints.
- An API reference page for the model routes.
- Mark this file `applied`, with a "How it was actually built" section.

---

## 4. Known gaps after this stage

- **No sharded saves.** Rank 0 gathers the full state dict, which won't fit for large models.
- **No deduplication.** Files that don't change between versions (the config, tokenizer files)
  are stored again in each.
- **The API trusts the client's sha256** at commit. Downloads catch a wrong one.
- **No renaming models** (D99).
- **Every upload goes through the proxy** until the benchmark says otherwise (D101).
- **Deleting from Storage isn't possible** in `flock-models`, since the proxy refuses it. That's
  the point, but it means the Models tab and the CLI are the only ways to remove anything.

## 5. Open for review

1. **One-segment model names** (`acme/olmo2-1b`), or folders like datasets (`acme/olmo2/1b`)?
   This proposal picks one segment.
2. **Optimizer state in the same version** under `training_state/`, skipped by default, or in a
   separate model?
3. **Retention off by default** (D109), with tagged versions and versions with evals always kept?
4. **7 days** before an idle upload expires. Should this be an org setting too?
