# Models API

Python functions and CLI commands for saving, loading, and managing Hub models. For metadata, dedupe keys, background uploads, and recovery, see [Model storage and uploads](model-storage). For examples, see [Store and load models](store-models).

## Saving and loading

[[autodoc]] hub.models.save_to_hub

[[autodoc]] hub.models.PendingUpload

[[autodoc]] hub.models.resume_uploads

[[autodoc]] hub.models.load_from_hub

[[autodoc]] hub.models.download

[[autodoc]] hub.models.load_training_state

## Importing from Hugging Face

[[autodoc]] hub.models.import_from_hf

[[autodoc]] hub.models.headers.rename_header

[[autodoc]] hub.models.headers.rename_stream

[[autodoc]] hub.models.headers.rename_file

[[autodoc]] hub.models.headers.rename_index

[[autodoc]] hub.models.headers.read_header

## Versions, tags and the primary

[[autodoc]] hub.models.versions

[[autodoc]] hub.models.set_primary

[[autodoc]] hub.models.tag

[[autodoc]] hub.models.untag

[[autodoc]] hub.models.list_models

[[autodoc]] hub.models.move

[[autodoc]] hub.models.ModelVersion

[[autodoc]] hub.models.ModelFile

[[autodoc]] hub.models.ModelError

## flock models

```bash
flock models COMMAND [ARGS]
```

| Command | Does |
|---|---|
| `flock models push DIR REF [--tag TAG]… [--primary]` | Pushes a folder as a new version and prints it, such as `acme/olmo2-1b@12`. Hidden files are skipped. Resumes if an earlier push of the same folder stopped partway and no file changed since. |
| `flock models import REPO REF [--revision REV] [--tag TAG]… [--primary]` | Imports a Hugging Face model, from the Hub or a local folder, as a new version and prints it. See [Import from Hugging Face](import-from-hf). |
| `flock models pull REF[@VERSION] [--to DIR] [--include-training-state]` | Downloads a version into the cache, or also copies it to `DIR`, and prints the folder. With no `@REF`, the primary version. `training_state/` is skipped unless asked for. |
| `flock models list [FOLDER]` | Lists one level of a folder: its models, with their latest and primary versions and the primary's size, then its folders with how many models each holds. `FOLDER` is `ORG` or `ORG/PATH`, `PROJECT:PATH`, or a path with `--project`. Without it, the root of your organization's models. |
| `flock models versions REF` | Lists versions, newest first, with tags, steps, sizes and which is primary. |
| `flock models primary REF@VERSION` | Makes a version primary. |
| `flock models tag REF[@VERSION] TAG [--move]` | Adds a tag, or moves it from another version with `--move`. With no `@VERSION`, tags the primary version. |
| `flock models untag REF TAG` | Removes a tag. |
| `flock models move REF NEW_PATH [--to-project PROJECT \| --to-global]` | Renames a model, moves it to another folder, or to another project or the organization's models. Instant, whatever its size. The old name stops resolving. |
| `flock models uploads list` | Lists the interrupted uploads on this machine, with how many files and bytes of each have been sent. |
| `flock models uploads resume ULID` | Resumes an upload from its local files. The start of the ULID is enough when no other upload shares it. |
| `flock models uploads abort ULID [--model REF]` | Abandons an upload, deletes what it sent and its local folder. `--model` aborts an upload that has no journal on this machine. |

`push`, `import` and `pull` show the bytes moved and the rate while they run. Every command that takes a model also takes `--project PROJECT` and `--org ORG`, described below. Every command exits with code `1` when the server refuses, saying why, and `2` when a reference isn't valid.

## Model references and names

Use `org/path@12` for a numbered version, `org/path@release` for a tag, or `org/path` (also `org/path@primary`) for the primary version. Use a numbered reference to keep loading the same version if a tag or the primary version moves.

A project's model is named by its path inside the project:

| Reference | Means |
|---|---|
| `acme/eval/olmo2-1b@12` | Version 12 of the organization's model `eval/olmo2-1b`. |
| `sweeps/lr-3e-4@12` with `project="pretrain"` (`--project pretrain`) | Version 12 of `sweeps/lr-3e-4` in the project `pretrain` of your default organization. |
| `sweeps/lr-3e-4@12` with `project="acme/pretrain"` | The same, in the organization `acme`. |
| `pretrain:sweeps/lr-3e-4@12` | A shorthand for the second row. Add `org=` (`--org`) for another organization. |

A reference is a shorthand when a `:` comes before any `/`, and everything before that `:` is the project. Giving both the shorthand and `project=` is refused.

A model's path is up to 255 characters, in parts separated by `/`. Each part is up to 100 lowercase ASCII characters, starts with a letter or digit, and may also contain `.`, `-`, and `_`. Tag names follow the same rule for one part, with uppercase letters allowed. Tags reserve `primary` in any case and names made entirely of digits.

A `checkpoint-{step}` tag uses the optimizer step without leading zeros. Checkpoint tags cannot move, and versions with only checkpoint tags are eligible for retention. [Manage models and retention](../app/browse-models)
