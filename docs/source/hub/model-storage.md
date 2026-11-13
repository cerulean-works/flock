# Model storage and uploads

A model save becomes a numbered version when the registry commits the upload. Until then, the client can recover a transfer from its local journal and the files and parts already in storage. [Python and CLI reference](models)

## How an upload becomes a version

1. **Prepare the source.** Saving a model object serializes it to a local staging directory. A folder push uses the caller's directory and skips hidden files and directories. The client records the visible files' sizes, modification times, and SHA-256 hashes.
2. **Open the upload.** The client declares the paths and sizes, plus the version options, to the registry. The registry returns an upload ID and part size, or returns an existing version if its dedupe key matches. For a new upload, the client moves staged model files into the upload's local directory and writes `journal.json` before sending file bytes.
3. **Transfer the files.** The client uses the S3 proxy to write to the upload's prefix. Files up to the part size use a single PUT. Larger files use multipart uploads. Requests carrying file data include `Content-MD5`, and each object carries the file's SHA-256 in `x-amz-meta-sha256`.
4. **Commit.** The client sends the file hashes to the registry. The server checks the paths, sizes, object metadata, and safetensors headers, and requires all multipart uploads to be complete. It then assigns a version number and applies the tags and primary setting in the same database transaction. The version is visible from that point.

## Dedupe keys

A dedupe key identifies a logical save within one model. When both `run` and `step` are supplied, the client defaults to `{run id}:{step}`. For example, `run="acme/pretrain/abc"` and `step=12000` give `abc:12000`. An explicit `dedupe_key` overrides that default. The key can be used independently of run links or tags.

If a live version already has the key, opening an upload returns that version with its original bytes and metadata. The registry checks the key again at commit, so if two uploads open with the same key, the first to commit wins and the other returns that version. A key can be reused once its version is deleted. The registry matches the key without comparing file contents.

The client also uses the key to find a matching local journal when saving a model object with `resume=True`. A match resumes the earlier snapshot and uses the options recorded in its journal. With `resume=False`, the client skips that local lookup and the server still applies deduplication.

## Background uploads

With `blocking=False`, `save_to_hub` serializes a model object on the caller's thread before returning a `PendingUpload`. The background worker handles file hashing, registry calls, and the S3 transfer. The caller can keep training after the snapshot is written. When given a folder, the worker reads it in place. Keep that folder available and unchanged until the upload finishes.

`PendingUpload.done()` reports whether the worker finished, including failures. `result()` returns the committed version or raises the worker's exception. `result(timeout=...)` limits the wait while the worker continues. Wait on pending uploads before the job exits to check that each version was published.

## Interrupted uploads

A transfer becomes resumable when its journal is written. For each file, the journal records the initial size, modification time, and SHA-256, plus the S3 multipart upload ID (when present) and whether the file completed. Each update writes a temporary JSON file and atomically replaces the journal. A crash before the journal exists can leave a staging directory. Staging directories older than one day are cleared when the client scans for journals.

`resume_uploads(name)` scans the local cache. Repeating a folder push also looks for a journal with the same model, source path, and visible file list, with sizes and modification times still matching. If those differ, the folder push starts a new upload. For a model object, a repeated save looks up the journal by dedupe key.

For each journal being resumed, the client asks the registry for its state.

| State | What the client does |
|---|---|
| Open | Check the source files' sizes and modification times. Skip files marked complete. For each other file, check whether the full object already exists with matching size and SHA-256 metadata. For an unfinished multipart upload, list the stored parts and compare their ETags to the MD5 of each local part. Reuse matching parts and resend missing or mismatched ones. If the multipart upload ID is gone, start that file again. |
| Committed | Repeat the commit request to recover the version number, then clean up the journal. The original source files are no longer needed. |
| Expired, aborted, or unknown | If the source is unchanged, open a new upload with the recorded options and clear the old transfer progress. If the registry returns a version for the same dedupe key, use it. |

An open upload expires after seven days of inactivity, and the server cleans up its objects and multipart uploads. If a source file has changed or disappeared and the upload still needs it, explicit resume reports that file. Bulk `resume_uploads` discards stale journals with changed or missing files once their upload is expired, aborted, or unknown.

## What a version holds

```text
model.safetensors                     or shards, with model.safetensors.index.json
config.json                           the model's config
flock.json                            model class, config class, dtype, flock commit, run, step
training_state/optimizer.safetensors  only if saved with training_state
manifest.json                         every file's path, size and sha256, written by Flock
```

### Where metadata lives

| Location | What it stores |
|---|---|
| `config.json` | The values of the model's frozen config dataclass. |
| `flock.json` | The file format version, model and config classes as `module:Class`, the first floating-point parameter's dtype, the Flock commit, save time, and the supplied run path and step. Flock writes these files when saving a model object. A folder push uploads the files already on disk. |
| Registry (PostgreSQL) | The authoritative version number, file list, digest, run link, step, and JSON supplied as `metadata=` (up to 256 KiB). The registry also tracks tags and the primary version. The run link resolves only when the named run exists in the model's organization. |
| `manifest.json` | A registry-generated copy of the version's identity, digest, custom metadata, and file list. Flock writes it to the bucket after commit and can regenerate it from PostgreSQL. Downloads get the file list from the registry. |

Model files live under `flock-models/{name}/u/{ulid}/`, where the ULID identifies the upload attempt. On commit, the registry assigns a version number that points to this prefix. The files stay at the same object keys.

Each file has its own SHA-256. The version digest is the SHA-256 of the file list, encoded as one `path<TAB>size<TAB>sha256<LF>` line per file in path order. The digest identifies the bytes in a version and names its local cache directory. Registry-only metadata and tags are outside that digest.

Nested `training_state` values such as optimizer state keep their structure in the safetensors header under `flock_structure`. The JSON describes the containers, scalar values, and keys for tensors stored in the same file. `load_training_state` uses it to rebuild the original structure.

## Download cache

Downloads resume from the size of each `.partial` file. Ranges are fetched concurrently and appended in order. The client verifies the complete file's SHA-256 before renaming it into place, then records verified paths in `.complete`. A hash mismatch removes the partial file and reports its path.

Each cached version has a lock so processes sharing the cache can reuse the same download. Downloads use read-only keys. [Credential resolution](../cli/credentials)

## Local files

| Path | Holds |
|---|---|
| `~/.cache/flock/models/{org}/{name}/{digest}/` | Downloaded versions, by digest. A file is renamed into place only after its sha256 matches. |
| `…/{digest}/{path}.partial` | A file being downloaded. The next download continues from its size. |
| `…/{digest}/.complete` | The files already checked, which later loads don't ask the server for. |
| `…/{digest}/.lock` | The lock that processes downloading the same version share. |
| `~/.cache/flock/uploads/.staging-*/` | A model snapshot while its upload is being prepared. |
| `~/.cache/flock/uploads/{ulid}/journal.json` | The upload ID, source directory, version options, and file progress, recorded before transfer. |
| `~/.cache/flock/uploads/{ulid}/model/` | The files `save_to_hub` wrote from a model. A pushed folder stays where it is. |

The upload's folder is deleted once the version exists, unless `keep_local=True`, which keeps `model/` and deletes the journal.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `FLOCK_CACHE` | `~/.cache/flock` | Where downloads and upload journals are kept. |
| `FLOCK_MODELS_CONCURRENCY` | `32` | Requests in flight at once, for every file of an upload or a download together. |

The registry's default upload part size is 64 MiB. Downloads fetch 64 MiB ranges. Botocore retries transient S3 failures in standard retry mode.
