# Models API

`flock.hub.models` and `flock models` store models as numbered versions in your organization's `flock-models` bucket. Start with [Store and load models](store-models).

## Model references

Every function and command that reads a version takes a reference:

| Reference | Resolves to |
|---|---|
| `org/name` | the primary version |
| `org/name@primary` | the primary version |
| `org/name@12` | version 12 |
| `org/name@checkpoint-12000` | the version tagged `checkpoint-12000` |

A reference is resolved once, when it's used. Everything after uses the version number it resolved to.

## Naming rules

| Name | Rules |
|---|---|
| Model | Lowercase ASCII letters, digits, `.`, `-` and `_`. Starts with a letter or digit. At most 100 characters. |
| Tag | ASCII letters, digits, `.`, `-` and `_`. Starts with a letter or digit. At most 100 characters. Can't be only digits, so `@12` always means version 12. Can't be `primary`. |
| Checkpoint tag | `checkpoint-{step}`, where `step` is the number of optimizer steps taken before the save, with no leading zeros. A convention that training code follows, not added by Flock. |

## Primary version

| Behavior | Rule |
|---|---|
| Default | A reference with no `@` resolves to the primary version. |
| Count | Exactly one per model, once it has a version. |
| First version | Becomes primary when it's saved. |
| Who changes it | Organization members and admins. |
| How | **Make primary** in the **⋯** menu of a version in the **Versions** tab, `flock models primary`, `set_primary`, or `primary=True` when saving. |
| When `primary=True` applies | When the upload finishes, not when it starts. |
| Deleting | The primary version can't be deleted until another is made primary. |
| Tags | `primary` isn't a tag. `tag` and `untag` refuse it. |

## Tags

| Behavior | Rule |
|---|---|
| Uniqueness | A tag names one version of a model. |
| Adding a tag already on another version | Refused, unless you pass `move=True` (`--move` on the CLI). |
| Checkpoint tags in the dashboard | Sorted by their number, and shown alone by the **Checkpoints** filter. |
| Deleting a tagged version | Refused while it has a tag other than a checkpoint tag. Checkpoint tags are deleted with their version. |
| Retention | **Model retention** in the organization settings, off by default. Checkpoint tags count as no tag, so retention can remove old checkpoints. The primary version, versions with other tags and versions with eval runs are always kept. Deleted eval runs don't count. |

## What a version holds

```text
model.safetensors                     or shards, with model.safetensors.index.json
config.json                           the model's config
flock.json                            model class, config class, dtype, flock commit, run, step
training_state/optimizer.safetensors  only if saved with training_state
manifest.json                         every file's path, size and sha256, written by Flock
```

The version's digest is the sha256 of its file list, so two versions with the same digest hold the same bytes. `manifest.json` stays in the version's folder in `flock-models`. Downloads don't fetch it, since the registry answers the same list.

`training_state` values that aren't flat state dicts, such as an optimizer's, keep their structure in the safetensors file's metadata, so `load_training_state` returns them as they were saved.

## Local files

| Path | Holds |
|---|---|
| `~/.cache/flock/models/{org}/{name}/{digest}/` | Downloaded versions, by digest. A file is renamed into place only after its sha256 matches. |
| `…/{digest}/{path}.partial` | A file being downloaded. The next download continues from its size. |
| `…/{digest}/.complete` | The files already checked, which later loads don't ask the server for. |
| `…/{digest}/.lock` | The lock that processes downloading the same version share. |
| `~/.cache/flock/uploads/{ulid}/journal.json` | An upload's journal, written before any byte is sent. |
| `~/.cache/flock/uploads/{ulid}/model/` | The files `save_to_hub` wrote from a model. A pushed folder stays where it is. |

The upload's folder is deleted once the version exists, unless `keep_local=True`, which keeps `model/` and deletes the journal.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `FLOCK_CACHE` | `~/.cache/flock` | Where downloads and upload journals are kept. |
| `FLOCK_MODELS_CONCURRENCY` | `32` | Requests in flight at once, for every file of an upload or a download together. |

Uploads send files of up to 64 MiB in one request and larger files in 64 MiB parts, each with `Content-MD5`. Downloads fetch 64 MiB ranges. Failed requests are retried by botocore's standard retry mode. Signing in works as [Credential resolution](../cli/credentials) describes, and downloads use read-only keys.

## Saving and loading

[[autodoc]] hub.models.save_to_hub

[[autodoc]] hub.models.PendingUpload

[[autodoc]] hub.models.resume_uploads

[[autodoc]] hub.models.load_from_hub

[[autodoc]] hub.models.download

[[autodoc]] hub.models.load_training_state

## Versions, tags and the primary

[[autodoc]] hub.models.versions

[[autodoc]] hub.models.set_primary

[[autodoc]] hub.models.tag

[[autodoc]] hub.models.untag

[[autodoc]] hub.models.list_models

[[autodoc]] hub.models.ModelVersion

[[autodoc]] hub.models.ModelFile

[[autodoc]] hub.models.ModelError

## flock models

```bash
flock models COMMAND [ARGS]
```

| Command | Does |
|---|---|
| `flock models push DIR ORG/NAME [--tag TAG]… [--primary]` | Pushes a folder as a new version and prints it, such as `acme/olmo2-1b@12`. Hidden files are skipped. Resumes if an earlier push of the same folder stopped partway and no file changed since. |
| `flock models pull ORG/NAME[@REF] [--to DIR] [--include-training-state]` | Downloads a version into the cache, or also copies it to `DIR`, and prints the folder. With no `@REF`, the primary version. `training_state/` is skipped unless asked for. |
| `flock models list [--org ORG]` | Lists models, with their latest and primary versions and the primary's size. |
| `flock models versions ORG/NAME` | Lists versions, newest first, with tags, steps, sizes and which is primary. |
| `flock models primary ORG/NAME@REF` | Makes a version primary. |
| `flock models tag ORG/NAME[@REF] TAG [--move]` | Adds a tag, or moves it from another version with `--move`. With no `@REF`, tags the primary version. |
| `flock models untag ORG/NAME TAG` | Removes a tag. |
| `flock models uploads list` | Lists the interrupted uploads on this machine, with how many files and bytes of each have been sent. |
| `flock models uploads resume ULID` | Resumes an upload from its local files. The start of the ULID is enough when no other upload shares it. |
| `flock models uploads abort ULID [--model ORG/NAME]` | Abandons an upload, deletes what it sent and its local folder. `--model` aborts an upload that has no journal on this machine. |

`push` and `pull` show the bytes moved and the rate while they run. Every command exits with code `1` when the server refuses, saying why, and `2` when a reference isn't `org/name[@version]`.

## Interrupted uploads

| Behavior | Rule |
|---|---|
| When a version appears | Only once every file has finished uploading and the commit succeeded. |
| What resuming sends | Only the files and parts not already in storage. A part whose bytes differ from the local file is sent again. |
| What resuming needs | The local files the upload started from, unchanged in size and modified time. A changed file stops the resume and is named. |
| Which uploads `save_to_hub` resumes | An upload of the same folder with unchanged files, or for a model, one with the same `dedupe_key`. Otherwise it starts a new upload. |
| A crash after the commit was sent | Resuming commits again, which answers the same version. |
| An expired or aborted upload | Resuming opens a new upload and sends every file again. `resume_uploads` forgets one whose local files changed or are gone. |
| Local copy | Kept in `~/.cache/flock/uploads/` until the version exists, then deleted unless `keep_local=True`. |
| Expiry | 7 days after the upload's last activity. What it sent is then deleted. |
| Downloads | Resume each file from where it stopped, and check its sha256 before using it. A file that doesn't match is deleted and the download fails with its path. |
