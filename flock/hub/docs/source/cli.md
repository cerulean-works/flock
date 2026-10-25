# Command line

`flock-hub` is installed with the workspace. Run it with `uv run flock-hub` from the repository root. It reaches the hub as `flock.hub` does: with `FLOCK_API_URL` and `FLOCK_API_KEY`, or `~/.config/flock/hub.json`.

## `flock-hub import`

Imports a Hugging Face dataset at one commit as a new version of a hub dataset, and prints the dataset, its version and its row count. [Importing from Hugging Face](importing) explains the two modes and how a staged import resumes.

```bash
uv run flock-hub import \
  --repo allenai/dolma-sample \
  --target flock/dolma-sample \
  --workdir /scratch/dolma-sample \
  --num-proc 16
```

| Flag | Default | Meaning |
|---|---|---|
| `--repo` | required | The Hugging Face dataset, such as `cais/mmlu`. |
| `--revision` | `main` | A branch, tag or commit, pinned to a commit sha at the start. A resumed staged import keeps the commit of its first run. |
| `--config` | the default config | The config to import. Required when the README defines several configs and none is the default. |
| `--data-files` | the config's files | A glob of repo files to import instead, such as `data/v1_7/*.json.gz`. Repeat it for several. Needs `--workdir`. |
| `--urls-file` | off | A file of URLs to download instead of repo files, one per line: a local path, or else a path in the repo. Needs `--workdir`. |
| `--target` | `hf/<owner>--<name>[--<config>]` | The hub dataset, as `org/name`. Created if it doesn't exist. |
| `--workdir` | off (small mode) | The staged import's work directory. Rerun with the same one to resume. Without it, the dataset is loaded into memory and written in one go. |
| `--stage` | `all` | `download`, `process` or `upload` runs that stage alone, once the stages before it are done. `all` runs what is left. Needs `--workdir` unless `all`. |
| `--num-proc` | `1` | Processes converting files in the process stage. |
| `--mode` | `overwrite` | `overwrite` replaces the target's rows, `append` adds to them. |

The command runs [`~hub.importer.import_hf_dataset`], and every flag maps to one of its arguments. It exits with status 1 and a message for an unknown repo, revision or config, and for options that need a work directory.
