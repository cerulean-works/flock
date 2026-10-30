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
| Retention | Checkpoint tags count as no tag, so retention can remove old checkpoints. The primary version, versions with other tags and versions with evaluations are always kept. |

## What a version holds

```text
model.safetensors                     or shards, with model.safetensors.index.json
config.json                           the model's config
flock.json                            model class, flock commit, run, step
training_state/optimizer.safetensors  only if saved with training_state
manifest.json                         every file's path, size and sha256, written by Flock
```

The version's digest is the sha256 of its file list, so two versions with the same digest hold the same bytes.

## Python functions

### save_to_hub

```python
models.save_to_hub(model, name, *, tags=(), primary=False, metadata=None, run=None, step=None,
                   dedupe_key=None, training_state=None, shard_size=5 * 2**30, blocking=True,
                   resume=True, keep_local=False)
```

Saves `model` as a new version of `name` and returns a `ModelVersion`, or a `PendingUpload` when `blocking=False`.

| Argument | Default | Meaning |
|---|---|---|
| `model` | | A Flock model such as `Olmo2`, or the path of a folder that already holds a version's files. |
| `name` | | The model, as `org/name`. Created if it doesn't exist. |
| `tags` | none | Tags to add to the new version. |
| `primary` | `False` | Make the new version primary when the upload finishes. |
| `metadata` | none | Extra JSON values stored with the version. |
| `run` | none | The wandb run that produced the model. Links the version to the run. |
| `step` | none | The training step. Shown with the version and stored in `flock.json`. |
| `dedupe_key` | `{run id}:{step}` when `run` and `step` are given | If a version of the model already has this key, it's returned and nothing uploads. |
| `training_state` | none | A mapping of names to state dicts, each saved as `training_state/{name}.safetensors`. |
| `shard_size` | 5 GiB | The largest weights file. Bigger models are split into shards. |
| `blocking` | `True` | Wait for the version. With `False`, return once the files are on local disk and upload in the background. |
| `resume` | `True` | Finish an interrupted upload of the same model and folder instead of starting again. |
| `keep_local` | `False` | Keep the local copy in `~/.cache/flock/uploads/` after the upload finishes. |

`PendingUpload.result()` waits for the version and returns it. `PendingUpload.done()` says whether it's finished.

### load_from_hub

```python
models.load_from_hub(ref, *, device=None, dtype=None)
```

Downloads the version `ref` names and returns the model with its weights loaded.

| Argument | Default | Meaning |
|---|---|---|
| `ref` | | A [model reference](#model-references). With no `@`, the primary version. |
| `device` | CUDA when available, else the CPU | Where to put the weights. |
| `dtype` | the version's | The weights' dtype. |

Only model classes from `flock.training` are loaded, so a version's files can't make the loader import other code.

### download

```python
models.download(ref, *, allow=None, ignore=None, include_training_state=False)
```

Downloads the version's files into the cache and returns the folder's path.

| Argument | Default | Meaning |
|---|---|---|
| `ref` | | A [model reference](#model-references). |
| `allow` | every file | Glob patterns of files to download. |
| `ignore` | none | Glob patterns of files to skip. |
| `include_training_state` | `False` | Also download `training_state/`. |

### Other functions

| Function | Does |
|---|---|
| `set_primary(ref)` | Makes the version `ref` names primary, and returns it. |
| `tag(ref, tag, *, move=False)` | Adds `tag` to the version `ref` names. With `move=True`, moves it from another version. |
| `untag(name, tag)` | Removes `tag` from the model `name`. |
| `versions(name)` | The model's versions, newest first. |
| `resume_uploads(name)` | Finishes every interrupted upload of the model that has its local files, and returns the versions made. |

### ModelVersion

| Attribute | Meaning |
|---|---|
| `org`, `name` | The organization and model. |
| `number` | The version number. |
| `digest` | The sha256 of the version's file list. |
| `primary` | Whether it's the primary version. |
| `tags` | Its tags. |
| `step` | The training step, if one was given. |
| `files` | Each file's path, size and sha256. |
| `metadata` | The `metadata` it was saved with. |

`str(version)` is `org/name@number`, such as `acme/olmo2-1b@12`.

## flock models

```bash
flock models COMMAND [ARGS]
```

| Command | Does |
|---|---|
| `flock models push DIR ORG/NAME [--tag TAG]… [--primary]` | Pushes a folder as a new version. Resumes if an earlier push of the same folder stopped partway. |
| `flock models pull ORG/NAME[@REF] [--to DIR]` | Downloads a version into the cache, or copies it to `DIR`. With no `@REF`, the primary version. |
| `flock models list [--org ORG]` | Lists models, with their latest and primary versions. |
| `flock models versions ORG/NAME` | Lists versions, newest first, with tags, steps, sizes and which is primary. |
| `flock models primary ORG/NAME@REF` | Makes a version primary. |
| `flock models tag ORG/NAME@REF TAG [--move]` | Adds a tag, or moves it from another version with `--move`. |
| `flock models untag ORG/NAME TAG` | Removes a tag. |
| `flock models uploads list` | Lists your interrupted uploads, with how much of each has been sent. |
| `flock models uploads resume ID` | Resumes an upload from its local files. |
| `flock models uploads abort ID` | Abandons an upload and deletes what it sent. |

## Interrupted uploads

| Behavior | Rule |
|---|---|
| When a version appears | Only once every file has finished uploading. |
| What resuming sends | Only the files and parts not already in storage. |
| What resuming needs | The local files the upload started from, unchanged. A changed file stops the resume and is named. |
| Local copy | Kept in `~/.cache/flock/uploads/` until the version exists, then deleted unless `keep_local=True`. |
| Expiry | 7 days after the upload's last activity. What it sent is then deleted. |
| Downloads | Resume each file from where it stopped, and check its sha256 before using it. |
