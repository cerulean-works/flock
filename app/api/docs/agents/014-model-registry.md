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
7. **A primary version** per model, the one loaded when no version is given, and a
   `checkpoint-{step}` tag convention (D107).
8. **A Models tab** with versions, tags and links (D108).

Saving and loading a model is one call each:

```python
from flock.hub import models

version = models.save_to_hub(model, "acme/olmo2-1b", tags=["checkpoint-12000"], primary=True)
model = models.load_from_hub("acme/olmo2-1b", device="cuda")  # the primary version
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
- model cards beyond a README in the version;
- the trainer saving checkpoints on its own. Training code calls `save_to_hub` itself, and
  wiring that into `Trainer` is a separate change.

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
       primary_version_pk → model_versions NULL, primary_set_by → users SET NULL,
       primary_set_at, deleted_at NULL, UNIQUE (org_pk, name) WHERE deleted_at IS NULL)

model_uploads(pk, ulid UNIQUE, model_pk → models CASCADE, user_pk → users SET NULL,
              state CHECK (state IN ('open', 'committed', 'aborted', 'expired')),
              files jsonb,              -- [{path, size}] as declared when opened
              metadata jsonb, tags text[], make_primary bool, run_pk → runs SET NULL NULL,
              step bigint NULL, dedupe_key text NULL, created_at, last_activity_at)

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
  `primary` is reserved for the primary version (D107) and can't be a tag.
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
  named in `model.safetensors.index.json`. Anything else (a README, tokenizer files, D104's
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

- **Opening** takes `files: [{path, size}]`, `metadata`, `tags`, `primary` (whether the new
  version becomes primary, default false), and optionally `run`, `step` and `dedupe_key`. If a
  live version of the model already has the `dedupe_key`, it answers 200 with that version and
  no upload, so a training job that restarts doesn't push the same step twice.
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
     files and its tags (moving tags already on other versions), makes it primary if the upload
     asked or the model has no primary yet (D107), and marks the upload `committed`.
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
| A training job restarts and saves a step it already pushed | `dedupe_key` returns the existing version (D100). |
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
- **`models.resume_uploads(name)`** finishes every journal left for a model, so a restarted job
  can clear what an earlier process left before saving anything new.

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

- **Resolving.** `GET orgs/{org}/models/{name}/resolve?ref=` takes a number, a tag or
  `primary`, and no `ref` means `primary`. It answers with the number, digest, files (path,
  size, sha256), metadata and the folder's prefix. A tag or `primary` is resolved once, and
  everything after uses the number.
- **The cache** is `~/.cache/flock/models/{org}/{name}/{digest}/`, keyed by digest, so a moved
  tag never serves stale bytes and two names for one version share files.
- **Each file** downloads to `{path}.partial` with up to 32 concurrent ranged GETs. After a
  crash, the download resumes from the partial file's size. A file is renamed into place only
  after its sha256 matches. A mismatch deletes the partial file and fails with the path.
- **Several processes** downloading the same version (one per rank) share one download through a
  file lock on the version folder. A `.complete` marker means every file has been checked.
- **Read-only keys** are enough, so jobs that only load models never hold a key that writes.
- **Skipping files.** `allow` and `ignore` patterns choose which files download.
  `training_state/` is skipped unless asked for (D104).

### D104. `save_to_hub` and `load_from_hub`

`flock.hub.models` holds both, behind the `models` extra (`flock-hub[models]`, adding torch and
safetensors), so installing `flock-hub` for datasets stays light.

```python
def save_to_hub(
    model: BaseModel | str | os.PathLike,  # a model, or a folder that already has the files
    name: str,  # "org/name"
    *,
    tags: Sequence[str] = (),
    primary: bool = False,
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
    ref: str,  # "org/name@12", "org/name@primary" or "org/name"
    *,
    device: str | torch.device | None = None,
    dtype: torch.dtype | None = None,
) -> BaseModel: ...


def download(ref: str, *, allow=None, ignore=None, include_training_state=False) -> Path: ...


def set_primary(ref: str) -> ModelVersion: ...  # "org/name@12" or "org/name@checkpoint-12000"
def tag(ref: str, tag: str, *, move: bool = False) -> ModelVersion: ...
def untag(name: str, tag: str) -> None: ...
def versions(name: str) -> list[ModelVersion]: ...
def resume_uploads(name: str) -> list[ModelVersion]: ...
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
  - `training_state` is a mapping of names to state dicts, such as `{"optimizer": ...}`. Each
    goes in `training_state/{name}.safetensors` in the same version. It's there to resume
    training, so `load_from_hub` and `download` skip it unless `include_training_state=True`.
    Plain values (a step, RNG seeds) go in `metadata`.
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
- **`ModelVersion`** has `org`, `name`, `number`, `digest`, `tags`, `primary`, `step`, `files`
  and `metadata`, and prints as `acme/olmo2-1b@12`.
- **Refs** are `org/name`, `org/name@{number}`, `org/name@{tag}` or `org/name@primary`. With no
  `@`, every function that reads a version uses the primary one.

### D105. The `flock models` CLI

| Command | Does |
| --- | --- |
| `flock models push DIR ORG/NAME [--tag T]… [--primary]` | pushes a folder (D102 for resuming) |
| `flock models pull ORG/NAME[@REF] [--to DIR]` | downloads into the cache, or copies to `DIR`; no `@REF` means the primary |
| `flock models list [--org]` | models, with their latest and primary versions |
| `flock models versions ORG/NAME` | versions, newest first, with tags, steps, sizes and which is primary |
| `flock models primary ORG/NAME@REF` | makes that version primary |
| `flock models tag ORG/NAME@REF TAG [--move]` | adds a tag, or moves it with `--move` |
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

### D107. The primary version and checkpoint tags

**The primary version** is the model's default: what `load_from_hub`, `download`, `flock models
pull` and `flock:` refs in eval use when no version is given.

- **It's not a tag.** It's `models.primary_version_pk`, so there's exactly one per model, it
  can't be removed by untagging, and `primary` can't be used as a tag name (D98). `@primary` in
  a ref names it explicitly.
- **The first version** a model gets becomes primary, so every model with a version has one.
- **Making a version primary:**
  - in the Models tab, from the **⋯** menu on the version's row: **Make primary**, with a
    confirmation that names the version it replaces ("Make version 14 primary? Loading
    acme/olmo2-1b without a version will give version 14 instead of 12.");
  - with `flock models primary acme/olmo2-1b@14`;
  - with `models.set_primary("acme/olmo2-1b@14")`;
  - at upload, with `primary=True` (`--primary` on the CLI), which takes effect at commit.

  Members and admins can do it. `PUT orgs/{org}/models/{name}/primary {version}` is the route.
- **The move is recorded,** with who made it and when (`primary_set_by`, `primary_set_at`), and
  shows on the model page.
- **The primary version can't be deleted** until another version is made primary (D109).
- **Resolved once.** A job that loads `acme/olmo2-1b` records the number it got, as eval's
  provenance does (D106), so moving the primary later doesn't change what an earlier run says it
  used.

**Checkpoint tags.** A version saved during training is tagged `checkpoint-{step}`, where `step`
is the optimizer step it was saved after, for example `checkpoint-12000`:

- **It's a convention.** Flock doesn't add the tag, the training code passes it in `tags`, along
  with the same number as `step`. The guide shows how.
- **What Flock does with it:** tags matching `checkpoint-\d+` sort by their number in the
  version picker and the Versions tab, and the Versions tab has a **Checkpoints** filter that
  shows only versions with one. Retention treats them as untagged (D109), since every training
  checkpoint has one.
- **One tag, one version.** Saving `checkpoint-12000` again answers 409, so a restarted job that
  reaches a step it already saved doesn't silently move the tag. `dedupe_key` (D100) normally
  returns the existing version before that happens.
- **Tags stay put.** Unlike the primary, checkpoint tags aren't meant to move. `--move` and
  `move=True` exist for other tags, such as `release`.

### D108. The Models tab

- **The sidebar** gets "Models" (lucide `box`) between Datasets and Storage, shown when S3 is
  on, as Datasets is (D86). `models` joins `RESERVED`, and migration 0015 refuses to run if an
  org is named `models`.
- **Routes:**

  | URL | Page |
  | --- | --- |
  | `/models/{org}` | the org's models: name, latest version, primary version, size, updated |
  | `/models/{org}/{name}` | a model, at `?version=` (a number or tag, default the primary) |

- **The model page** has a header with the version picker, size, and actions: Load (a dialog with
  the `load_from_hub` and `flock models pull` snippets for the version shown), Browse files (the
  folder in Storage) and Delete (D109). Tabs, kept in `?tab=`:

  | Tab | Shows |
  | --- | --- |
  | Overview (default) | the README if the version has one, `config.json` as a table, and `flock.json` |
  | Versions | newest first: number, a **Primary** badge, tags, size, run and step, who pushed it and when. Each row's **⋯** menu has View, Make primary (D107), Add tag and Delete. A **Checkpoints** filter shows only versions with a `checkpoint-{step}` tag |
  | Evals | the version's eval runs, each task's metrics side by side, linked to the runs |
  | Files | each file's path, size and sha256 |

- **The run page** gets a Checkpoints section listing the versions the run made, with their
  steps and tags.
- **Tags** are added, moved and removed as D96 does for datasets, by members and admins.

### D109. Deleting versions and models

- **A version** is deleted from the Versions tab, by members and admins, with the type-the-name
  dialog asking for `acme/olmo2-1b@12`. The primary version can't be deleted until another is
  made primary. A version with a tag other than `checkpoint-{step}` can't be deleted until the
  tag is moved or removed, and a checkpoint tag goes with its version. A version with eval runs
  can be deleted, after a warning that says how many.
- **What deleting does:** the row gets `deleted_at` and its files move to the trash bucket
  (D93's, at `{org pk:05}/models/{version pk}/`). Admins restore or delete it for good from a
  "Deleted models" section in org settings, and the sweep removes it after 14 days. A restored
  version keeps its number.
- **A model** is deleted the same way, with every version in it, and restored the same way.
- **Retention** is an org setting, on the org settings page: "Keep the newest N untagged versions
  of each model", off by default. When on, the sweep deletes older versions that aren't primary,
  have no tag other than `checkpoint-{step}`, and have no eval runs, as above, so they wait 14
  days in the trash.

---

## 3. Plan

Four stacked PRs. The first branches from `feat/model-registry`, which holds this doc and
branches from `feat/scaffold-training-loop`, and each next one from the one before.

### PR 1. Registry and uploads (`feat/model-registry-api`)

- Migration `0015_models.sql`: `s3_buckets.managed` accepts `'models'`, the tables of D98, the
  retention setting, and the `models` org name refused.
- `ensure_managed` for `flock-models` (D97).
- `core/models.py` and `api/models.py`: upload sessions and commits (D100), resolving (D103),
  versions, tags, the primary version (D107), deleting and the trash (D109).
- The proxy's write rule for `flock-models` and its cache of open uploads, cleared over the bus
  on commit, abort and expiry (D97, D101).
- The sweep: expiry, stray prefixes, the trash and retention (D102, D109). The startup pass for
  missing `manifest.json` files.

### PR 2. Client (`feat/model-registry-client`)

- `flock.hub.models`: uploading with the journal and resuming (D101, D102), downloading and the
  cache (D103), `save_to_hub`, `load_from_hub`, `download`, `set_primary`, `tag`, `untag`,
  `versions` and `resume_uploads` (D104, D107).
- `BaseModel.config`.
- The `flock models` CLI (D105).
- The guide and reference pages already written with this doc (`docs/source/hub/`), updated to
  whatever the code ends up doing, with `[[autodoc]]` for the docstrings.

### PR 3. Eval (`feat/model-registry-eval`)

- `flock:` refs in eval, `model_digest` in the provenance, and the API's eval links (D106).

### PR 4. Web (`feat/models-tab`)

- The sidebar item, the list and model pages, the run page's Checkpoints section, the row menu
  with Make primary, the tag and delete dialogs, the Checkpoints filter, and the "Deleted models"
  and retention sections in org settings (D107 to D109).

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
  - tags: adding, moving, digits-only names and `primary` refused, a second `checkpoint-12000`
    refused, resolving at a moved tag;
  - the primary: the first version becoming primary, `primary=True` at commit, moving it,
    resolving with no ref, and deleting the primary refused;
  - deleting and restoring versions and models, tagged versions refused, checkpoint tags going
    with their version, retention skipping the primary and treating checkpoint tags as untagged;
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
- **Manual:** follow the guide's checkpoint loop with a tiny `Olmo2`, kill the process during an
  upload, restart it, see the upload resume and the `checkpoint-{step}` versions appear, make one
  primary from the row menu, load the model with no version, evaluate it with `flock:` and see
  the eval on the model page.

### Docs and status

- The guide, `docs/source/hub/store-models.md`, and the reference, `docs/source/hub/models.md`,
  are written with this doc, ahead of the code, so they describe what it should do. Check them
  against the code in PR 2 and swap the hand-written tables for `[[autodoc]]` where the
  docstrings cover them.
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

## 5. Settled before review

1. **The primary version is its own pointer,** not a tag, set from the version's **⋯** menu, and
   it's what loads when no version is given (D107).
2. **Checkpoints are tagged `checkpoint-{step}`** by convention, added by the training code
   (D107).
3. **The trainer isn't changed** in this stage. The guide shows the loop to write.

## 6. Open for review

1. **One-segment model names** (`acme/olmo2-1b`), or folders like datasets (`acme/olmo2/1b`)?
   This proposal picks one segment.
2. **Optimizer state in the same version** under `training_state/`, skipped by default, or in a
   separate model?
3. **Retention off by default** (D109), with the primary, versions with evals and versions with
   tags other than `checkpoint-{step}` always kept?
4. **7 days** before an idle upload expires. Should this be an org setting too?

## 7. How it was actually built (PR 1)

PR 1 (`feat/model-registry-api`) holds the registry, the upload sessions, the proxy's rule for
`flock-models` and the sweep, in three commits: the API with the migration, the proxy, and the
tests. The status stays `proposed` until the later PRs land. These are the places where the code
differs from the decisions above, or adds to them.

- **Tables (D98).** Beyond D98's columns: `models.last_number` hands out numbers, since
  `max(number) + 1` would reuse a number once the trash removes the newest version for good.
  `models.deleted_by` and `model_versions.deleted_by` say who deleted them for the "Deleted
  models" section. `model_versions.trashed_at` is set once a deleted version's files are all in
  the trash, `deleted_tags` keeps the checkpoint tags that went with it, and `manifest_at` is set
  once `manifest.json` is written. `upload_pk` is unique. The dedupe key is unique among live
  versions only (a partial index), so a deleted version doesn't block saving that step again.
  Retention is `orgs.model_retention` (null is off), next to the S3 key lifetimes, and is set
  with `PATCH orgs/{org}` by admins and read in the org's answer.
- **The managed bucket (D97).** `ensure_managed` takes a kind (`datasets` or `models`).
  Creating an org, the startup pass and the Models routes (listing models, opening an upload)
  ensure `flock-models` as D85 does `flock-datasets`. Every test org now has both, so the tests
  that count an org's buckets were updated again.
- **The proxy (D97, D101).** A write to `flock-models` passes only if its key is
  `{model}/u/{ulid}/{path}` of an open upload whose opener is the caller, in the caller's org,
  for that model. PUT, UploadPart, CreateMultipartUpload, CompleteMultipartUpload and
  AbortMultipartUpload pass. Deletes, DeleteObjects and copies into the bucket are refused even
  inside an upload, since a copy isn't a PUT. ListParts is a read. Only open uploads are cached
  (as keys are, never as missing), and a trigger on `model_uploads` notifies
  `flock_model_uploads` with the ULID whenever an upload's state changes, which drops it from
  every worker's cache. Each write that passes touches `last_activity_at`, at most once a minute
  per upload and worker.
- **Opening (D100).** The required files (D99) and the path rules are checked when the upload
  is opened as well as at the commit, so a push without `config.json` fails before any bytes are
  sent. Paths are relative, without `.`, `..`, empty segments, control characters or
  backslashes, at most 800 bytes, and `manifest.json` is refused since Flock writes it. An upload
  holds at most 10,000 files of at most 625 GiB each (10,000 parts of 64 MiB), and at most
  256 KiB of metadata. A `checkpoint-{step}` tag already on a version is refused when the upload
  is opened (409 with `version`), not only at the commit. `run` is `entity/project/id` and links
  only to a run of the same org; anything else is ignored, as D106 says for unknown runs.
- **Tags at upload.** D100 moves the upload's tags from other versions, and D107 refuses a
  second `checkpoint-12000`. Both hold: tags move at upload, except checkpoint tags, which answer
  409. A checkpoint tag also never moves through `PUT tags/{tag}`, even with `move`. `primary` is
  refused as a tag in any case.
- **Committing (D100).** The upload's row is locked for the whole commit, so two commits of one
  upload wait for each other and the sweep doesn't expire it meanwhile, and the model's row is
  locked only to number it. A 409 carries the paths as `missing`, `extra`, `resized`,
  `uploading` and `mismatched`, and files in the commit that aren't the declared ones are 422.
  The safetensors check reads every shard named by the index and `model.safetensors` if present,
  not `training_state/`, and checks each header's length and each tensor's dtype, shape and
  offsets. Only the opener can commit (403 for anyone else). The commit answers 200, again 200
  when repeated. If a live version got the upload's dedupe key while it was open, the commit
  answers that version and aborts its own upload, removing its folder. If the model was deleted
  while the upload was open, the upload is aborted and the commit answers 409.
- **The digest** sorts paths by bytes (`COLLATE "C"` in Postgres, as Python's `sorted`).
  `manifest.json` holds `format`, `model`, `number`, `digest`, `files` and `metadata`. Missing
  manifests are written by the startup pass and by every sweep.
- **Uploads.** `GET uploads?state=` lists the caller's uploads in any state, `open` by default.
  `GET uploads/{ulid}` shows any member the upload's state. The opener or an admin can abort an
  upload, and aborting one that's already aborted or expired answers 204. ULIDs are made by Flock
  (48 bits of milliseconds and 80 random bits), without a dependency.
- **Routes beyond D100 and D103.** `GET orgs/{org}/models` lists the models with their latest
  and primary numbers. `GET models/{name}` shows the primary with who set it and the tags.
  `GET models/{name}/versions` pages newest first, 100 at a time, with `before` as the cursor and
  `checkpoints=true` for D107's filter. `GET` and `DELETE models/{name}/versions/{number}`,
  `DELETE models/{name}`, `PUT` and `DELETE models/{name}/tags/{tag}` with `{version, move}`
  (as 013's tags), and `PUT models/{name}/primary` (which answers `previous`, the number it
  replaced, for D107's confirmation). Users come back as their briefs.
- **The trash routes** are under `orgs/{org}/models/-/trash`, since `trash` is a valid model name
  and no model name starts with `-`. The listing holds `model` entries (with the versions deleted
  with them) and `version` entries (deleted versions of live models). Admins restore and delete
  for good with `POST -/trash/{models,versions}/{pk}/restore` and `DELETE -/trash/{models,
  versions}/{pk}`.
- **Deleting (D109).** A deleted version or model is gone from every route at once, and its
  files move to `{org pk:05}/models/{version pk}/` of the trash in the background, copying every
  object before deleting any, so a move cut short is finished by the sweep. A version can be
  tens of GiB, which doesn't fit in a request. Restoring and deleting for good answer 409 until
  the move is done. Objects over 5 GiB are copied in parts with the source's `x-amz-meta-*` set on
  the copy. A deleted version's checkpoint tags go with it, and come back on restore when nobody
  has taken them since. Its dedupe key is dropped on restore if a live version has it now.
- **Deleting a model** has no checks, since its primary would always refuse. Its tags and
  primary stay on its row for a restore. Its live versions get the model's `deleted_at`, which is
  how a restore finds exactly those; versions deleted on their own before stay deleted. Its open
  uploads are aborted after the deletion commits, so a commit in flight sees the model deleted
  instead of waiting on it. A restore answers 409 with `taken` when a live model has the name
  now. A version of a deleted model can't be restored on its own.
- **Restoring is synchronous.** It copies the files back in the request, so restoring a very
  large model can outlast a proxy's timeout. The trash copies are removed after the restore
  commits, and the orphan pass removes them if that fails.
- **Retention (D109).** The newest N untagged versions are counted among those that aren't
  primary and have no tag but checkpoint ones, newest first, and versions with eval runs count
  towards N but are never deleted. Retention runs in the sweep, with the model's row locked and
  the checks made again, and its deletions have no `deleted_by`.
- **The sweep (D102)** runs every 10 minutes on every replica: expiry, then stray folders,
  retention, unfinished moves to the trash, the 14-day purge, the trash's orphans and missing
  manifests. Expiry commits the `expired` state before cleaning up, so the proxy stops taking
  writes first. The stray pass removes the folders of aborted and expired uploads whose cleanup
  failed at once, folders with no upload row after a day, and multipart uploads that aren't an
  open upload's (a row-less one after a day). The model trash's orphans are
  `{org}/models/{pk}/` prefixes whose version isn't deleted, older than a day. The dataset trash's
  own orphan pass now leaves `{org}/models/` alone; it would have removed the model trash after a
  day.
- **Deleting an org** deletes its models' rows in the org's transaction. The objects go with its
  buckets' purge, and the trash with the dataset trash's `purge_org`, which already empties
  `{org pk:05}/`.
- **Formatting.** `ruff format --check .` formats Python blocks in Markdown and failed on D104's
  block, so that block is in ruff's layout now.
- **Ceph RGW** (checked with a throwaway bucket named like the tests' own, then removed, since
  `flock-hub-test` doesn't exist on the server, and by `tests/app/test_models.py` through the
  proxy with `FLOCK_TEST_S3_*`): ListParts gives each part's ETag as the MD5 of its bytes, and a
  part with a wrong `Content-MD5` is refused with `BadDigest`. A completed multipart object
  keeps the `x-amz-meta-sha256` set on CreateMultipartUpload, and CopyObject keeps it too. D102's
  resume can rely on both, and the journal doesn't need to store part MD5s.

Not in PR 1: the eval links of D106 (PR 3, though `model_version_runs` is in the migration and
the version routes and retention read it), a route listing the versions a run made (the run
page's Checkpoints section, PR 4), the API reference page for the routes, the client and CLI
(PR 2) and the web app (PR 4). The web app's types still name only `datasets` as a managed kind;
the Storage tab shows `flock-models` as managed without a Datasets link.
