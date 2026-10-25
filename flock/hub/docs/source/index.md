# The dataset hub

`flock.hub` keeps training and evaluation data as versioned [Lance](https://lancedb.github.io/lance/) datasets in Flock's own S3, and reads them through Hugging Face `datasets`. A dataset loaded from the hub behaves like an HF dataset downstream: you iterate it, `map` it, shuffle it and hand it to a PyTorch `DataLoader`.

## What it does

- **Pins a version at load.** Every commit to a dataset makes a new version. A loaded dataset keeps the version it was loaded at, so a training run that takes days never sees a commit made after it started.
- **Streams by default.** Rows are read from S3 as you iterate, one HF shard per Lance fragment. Streaming resumes from a checkpoint and splits across `DataLoader` workers and nodes the way HF's streaming does.
- **Reads any row directly.** `ds[i]`, slices and lists of indices read the rows from Lance, so random access doesn't need a local copy.
- **Writes exactly once.** An upload keeps its progress in a staging directory. Rerun a crashed upload and it skips the shards already written and never commits twice.
- **Versions its card.** Each version points to the README that describes it.

## Concepts

| Term | Meaning |
|---|---|
| dataset | A Lance dataset named `org/name`, such as `flock/fineweb-sample`. |
| version | One commit of a dataset, numbered from 1. Versions never change once made. |
| tag | A name for a version, such as `v1.0`. You can load a dataset by tag. |
| fragment | One part of a version's rows in Lance. Streaming reads one HF shard per fragment. |
| card | The README of a version, in Markdown. |
| filter | A SQL predicate that Lance applies while reading, such as `split = 'test'`. |

The hub has no splits. A dataset with several splits has a `split` column, and you load one split with a filter.

## Where to go next

- Load and write your first dataset: [Quickstart](quickstart).
- Versions, streaming, filters and random access: [Loading datasets](loading).
- Uploads, edits, tags and cards: [Writing datasets](writing).
- Every class and argument: [Python API](api).
