# Import datasets from Hugging Face

Turn a dataset on the Hugging Face Hub into Lance datasets in your organization's `flock-datasets` bucket, one per split, so the trainer and the **Datasets** tab can use them. You can import in one step, or download on one machine and convert on another, which suits clusters whose compute nodes have no internet.

## Before you start

[Install the workspace](../installation) and sign in to Flock on the machine that converts:

```bash
uv run flock login
```

For gated or private datasets, also sign in to Hugging Face on the machine that downloads, with `hf auth login` or by setting `HF_TOKEN`. Gated datasets need their terms accepted on huggingface.co first.

## Import in one step

On a machine that reaches both the Hub and Flock, give the repo and a destination folder in `flock-datasets`:

```bash
uv run flock datasets import HuggingFaceFW/fineweb-edu web/fineweb-edu --config sample-10BT
```

This downloads the files to a temporary folder, converts them, and deletes the folder. Each split becomes a Lance dataset, here `web/fineweb-edu/train`. The command prints each split's path, Lance version and row count:

```text
✓ web/fineweb-edu/train: version 1, 9,672,101 rows
```

Pass `--keep DIR` to download to `DIR` and keep the files, for example to convert them again later.

## Download and convert separately

On a cluster, download on a login node, which reaches the Hub but needs no Flock sign-in:

```bash
uv run flock datasets download HuggingFaceFW/fineweb-edu --config sample-10BT --to /scratch/fineweb-edu
```

Then convert on a compute node, which needs Flock but no internet:

```bash
uv run flock datasets convert /scratch/fineweb-edu web/fineweb-edu --workers 64
```

Running `download` again resumes an interrupted download. The folder holds the files, the dataset card (`README.md`) and a `flock-import.json` that records the repo, the commit, the config and each split's files in order. `convert` reads only that, so the folder can be copied to another machine.

For unattended conversions, such as a Slurm job, sign in with [`FLOCK_TOKEN`](../cli/authenticate-jobs).

## From Python

The same steps are functions in `flock.hub.datasets`:

```python
from flock.hub import datasets


def main():
    folder = datasets.download_from_hf(
        "HuggingFaceFW/fineweb-edu", "/scratch/fineweb-edu", config="sample-10BT"
    )
    written = datasets.convert(folder, "web/fineweb-edu", workers=64)
    for split in written:
        print(split.path, split.version, split.rows)


if __name__ == "__main__":
    main()
```

`datasets.import_from_hf(repo, dest, ...)` does both, with the same options as `flock datasets import` (`to=` in place of `--keep`). Conversion starts worker processes with `spawn`, which imports your script again in each worker, so keep the calls under an `if __name__ == "__main__":` guard.

## What gets downloaded

An import downloads one config of the repo:

- **The config** is the one you give with `--config`. Without it, the import uses the repo's only config, the one its dataset card marks as default, or the one called `default`. If none of these applies, it lists the configs and asks you to choose.
- **The splits** are all of the config's splits, or those you give with `--split` (repeat it for more).
- **The files** are the repo's own Parquet, JSON Lines, CSV and Arrow files, matched to configs and splits by the `configs:` block of the dataset card, or by the Hub's default file names when there is none (such as `data/train-00000-of-00002.parquet`, or `train` and `test` in file and folder names). JSON Lines and CSV files may be compressed (`.gz`, `.bz2`, `.zst`, `.lz4`).
- **Other repos**, such as those with a loading script, images or archives, are downloaded from the Hub's Parquet conversion (the `refs/convert/parquet` branch) instead. A split that the Hub only converted in part is refused, so an import never silently drops rows. A dataset with neither readable files nor a conversion is refused.

`--revision` downloads a branch, tag or commit other than `main`. The commit it resolves to is recorded, so the import says exactly which files it read.

## Where the data lands

| The repo has | Each split lands at |
|---|---|
| one config | `{dest}/{split}`, such as `web/fineweb-edu/train` |
| several configs | `{dest}/{config}/{split}`, such as `web/fineweb-edu/sample-10BT/train` |

The dataset card is copied to `{dest}/README.md` and to each split's folder, so the dataset page shows it. Each Lance version records where it came from in its transaction properties, under `flock.source`: the repo, the commit, the config and the split.

Rows keep their upstream order: row `i` of a split is row `i` of its files, read in order.

### Existing datasets

`--mode` decides what happens when a dataset is already at the destination:

| Mode | When a dataset exists | When none exists |
|---|---|---|
| `create` (default) | Refuses, before writing anything | Creates it |
| `overwrite` | Commits a new version holding only the imported rows | Creates it |
| `append` | Commits a new version with the rows added, if the schemas match | Creates it |

Earlier versions stay in Lance's history either way.

## How conversion works

Conversion is built for large datasets:

- **Many processes.** `--workers` defaults to the CPUs the process may use, which respects a Slurm allocation. Each worker reads source files in batches of at most about 64 MB and writes Lance fragments straight to S3, so memory stays flat. `--workers 1` converts in the command's own process.
- **One commit per split.** The command commits each split's fragments once every file is written. A split appears in full or not at all. If a worker fails or is killed, nothing is committed, and the files it wrote aren't part of any version.
- **One schema per split.** Before writing anything, the command reads each file's schema (Parquet footers, or the first block of JSON Lines and CSV files) and merges them. A column missing from some files is filled with nulls, and an integer column in one file and a float column in another become floats. Files that can't be merged stop the conversion, naming the first file that disagrees.
- **Credentials renew.** The command sets up the same renewing S3 credentials as [`s3.get_process()`](use-datasets#renew-credentials-for-long-runs), with write access to your organization's buckets, so long conversions don't outlive their keys.

On a terminal, progress is one line with each split's rows, bytes and files done. Otherwise, such as in a Slurm log, it's one line per finished file:

```text
train 3/14: data/train-00002-of-00014.parquet (690,817 rows, 1.4 GB)
```

## Check the imported dataset

List what's at a folder of `flock-datasets`, as the **Datasets** tab shows it:

```bash
uv run flock datasets list web/fineweb-edu
```

```text
web/fineweb-edu/train	version 1
```

Then open **Datasets** in the dashboard, find the split, and check its rows, schema and README. [Use datasets](use-datasets) shows how to read it from Python.

## Commands[[commands]]

All commands accept `--help`.

| Command | Does |
|---|---|
| `flock datasets import REPO DEST` | Download and convert in one step |
| `flock datasets download REPO --to DIR` | Download only. Needs the Hub, not Flock. |
| `flock datasets convert DIR DEST` | Convert a downloaded folder. Needs Flock, not the Hub. |
| `flock datasets list [PATH]` | List the datasets and folders at `PATH` (default: the bucket's root) |

| Flag | Commands | Default | Meaning |
|---|---|---|---|
| `--config` | `import`, `download` | the only config, the card's default, or `default` | The config to import. |
| `--split` | `import`, `download` | all | A split to import. Repeat for more. |
| `--revision` | `import`, `download` | `main` | A branch, tag or commit of the repo. |
| `--to` | `download` | required | The folder to download to. |
| `--keep` | `import` | a temporary folder | Download to this folder and keep it. |
| `--workers` | `import`, `convert` | the CPUs the process may use | Processes converting files at once. |
| `--mode` | `import`, `convert` | `create` | `create`, `overwrite` or `append`. See [Existing datasets](#existing-datasets). |
| `--org` | `import`, `convert`, `list` | your default organization | The organization whose `flock-datasets` to use. |

The commands exit with code `1` and a message when the Hub or Flock refuses, the config or a split doesn't exist, the files don't share a schema, or the destination exists in `create` mode.
