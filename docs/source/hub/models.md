# Models API

Python functions and CLI commands for saving, loading, and managing Hub models. For metadata, dedupe keys, background uploads, and recovery, see [Model storage and uploads](model-storage). For examples, see [Store and load models](store-models).

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

## Model references and names

Use `org/name@12` for a numbered version, `org/name@release` for a tag, or `org/name` (also `org/name@primary`) for the primary version. Use a numbered reference to keep loading the same version if a tag or the primary version moves.

Model and tag names are up to 100 ASCII characters. They start with a letter or digit and may also contain `.`, `-`, and `_`. Model names use lowercase letters. Tags reserve `primary` in any case and names made entirely of digits.

A `checkpoint-{step}` tag uses the optimizer step without leading zeros. Checkpoint tags cannot move, and versions with only checkpoint tags are eligible for retention. [Manage models and retention](../app/browse-models)
