# Fork a dataset

Fork a dataset to experiment with your own version of it: filter it, append to it or rewrite rows, without changing the original. A fork is ready in seconds whatever the dataset's size, because it copies no rows. It reads the original's files in place, and only your edits write new files.

## Before you start

- You are a member of the organization, and the dataset is in its `flock-datasets` bucket.
- For Python and the CLI, [install the workspace](../installation) and sign in with `uv run flock login`.

The examples fork `web/fineweb-edu/train` to `exp/alice/edu`.

## Fork from the Datasets tab

1. Open **Datasets** and navigate to the dataset. You can also use **Fork…** in a dataset row's **⋯** menu, without opening it.
2. Click **Fork**.
3. Pick the **Version** to start from. The list shows each version's tags and rows, and starts on the version you are viewing.
4. Pick the folder under **In folder**, and type a **Name**. They start as the original's folder and `{name}-fork`.
5. Click **Fork**. The fork opens when it's ready.

The fork's **About** tab says where it came from: **Forked from** `web/fineweb-edu/train` **at version** 12.

## Fork from Python or the CLI

```python
from flock.hub import datasets

made = datasets.fork("web/fineweb-edu/train", "exp/alice/edu", version=12)
print(made["path"], made["version"], made["source"])
```

```bash
uv run flock datasets fork web/fineweb-edu/train exp/alice/edu --version 12
```

`version` takes a version number or a tag, and defaults to the latest version. Both refuse a destination where something already exists.

## Edit the fork

A fork is an ordinary Lance dataset. Open it with the same storage options as any other dataset ([Use datasets](use-datasets#load-a-dataset)) and write to it:

```python
import lance
from flock.hub import s3

options = s3.credentials().storage_options()
fork = lance.dataset("s3://flock-datasets/exp/alice/edu", storage_options=options)
fork.delete("language_score < 0.9")
print(fork.count_rows())
```

Deleting rows writes deletion files, and appending or updating rows writes new data files, all in the fork's own folder. The original keeps every row and every version. You can fork the fork too, and blend it with other datasets ([Blend datasets](blend-datasets)).

The fork's version numbers continue from the version it was forked at. A fork of version 12 starts at version 12, and your first edit makes version 13.

## What the original can no longer do

Flock pins the version a fork reads with a hidden tag on the original, so cleaning up the original's old versions never removes files the fork still reads. While the fork exists, the original can't be renamed, moved or deleted. Its **About** tab lists the datasets that read it under **Read by**.

To let the original move or go, do one of the following:

- Delete the fork.
- Make the fork independent. This copies the rows it reads into its own folder and removes its earlier versions. Use **Make independent…** on the fork's **About** tab, `datasets.make_independent("exp/alice/edu")`, or `uv run flock datasets make-independent exp/alice/edu`.

Making a fork independent copies its data, so it takes as long as writing the dataset again. The copy runs on the Flock server, which refuses datasets over its size limit (`FLOCK_HF_IMPORT_MAX_BYTES`, 50 GiB by default).

## Check it worked

- The fork's **About** tab names the original and its version.
- The original's **About** tab lists the fork under **Read by**.
- The original's row count and versions are unchanged after you edit the fork.

For how forks are stored and every limit, see [Forks and blends](forks-and-blends).
