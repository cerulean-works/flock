# Loading datasets

[`~hub.load.load_dataset`] returns a [`~hub.dataset.Dataset`]: one version of a hub dataset, wrapped around an HF `IterableDataset` (streaming, the default) or an HF `Dataset`. This page covers which version you get, the two modes, filters, random access and distributed training.

## Versions

Every commit to a dataset makes a version, numbered from 1. `version` takes a number, a tag or `None`:

```python
from flock.hub import load_dataset

latest = load_dataset("flock/fineweb-sample")  # the latest version
v3 = load_dataset("flock/fineweb-sample", version=3)  # a version number
release = load_dataset("flock/fineweb-sample", version="v1.0")  # a tag
print(release.version)  # the number the tag pointed to at load
```

The version is resolved once. A dataset loaded with `version=None` keeps the version that was latest at load, and every read goes through it: iteration, random access, `num_rows` and the card. A commit that lands while you train is invisible to the loaded dataset. To see newer versions, load again or call [`~hub.dataset.Dataset.checkout`]:

```python
print(latest.latest_version)  # the dataset's latest version now
newer = latest.checkout()  # same columns, filter and mode, at the latest version
older = latest.checkout(2)
```

An unknown version or tag raises [`~hub.load.VersionNotFound`], whose message gives the latest version and the tags. A missing dataset raises [`~hub.client.DatasetNotFound`], unless it is on Hugging Face (see below).

## Falling back to Hugging Face

When there is no hub dataset of the name you give, `load_dataset` takes it as a Hugging Face dataset and loads its mirror, in this order:

1. the hub dataset `repo_id`;
2. its mirror, `hf/<owner>--<name>[--<config>]`: at the import of `hf_revision` if you give one, else at its latest version;
3. if there is no mirror, or none of that revision, the server imports it first, and `load_dataset` waits, logging the import's progress.

```python
mmlu = load_dataset("cais/mmlu", config="all", filter="split = 'test'")
print(mmlu.repo_id)  # hf/cais--mmlu--all
```

| Argument | Default | Meaning |
|---|---|---|
| `config` | `None` | The Hugging Face config, which becomes part of the mirror's name. |
| `hf_revision` | `None` | A branch, tag or commit to import. `None` takes the mirror's latest import, or `main` when there is none. |
| `auto_import` | `True` | `False` raises [`~hub.client.DatasetNotFound`] instead of falling back. |

A `version` you pass applies to the mirror. A dataset too large for the server to import raises [`~hub.load.ImportTooLarge`], whose message holds the `flock-hub import` command to run instead. [Importing from Hugging Face](importing) covers mirrors and imports in full.

## Streaming or not

| | `streaming=True` (default) | `streaming=False` |
|---|---|---|
| Wraps | HF `IterableDataset` | HF `Dataset` |
| Reads | From S3 as you iterate | Once into HF's cache, then from disk |
| Shards | One per Lance fragment | n/a |
| Resume | `state_dict()` and `load_state_dict()` | Index into it |
| Random access after `map` | No | Yes, HF's own |

Without streaming, the first load copies the version into HF's cache (`cache_dir`, or HF's default). The copy is keyed by the dataset, version, columns and filter. Versions never change, so the copy never goes stale, and a second load reads no data from S3. A damaged copy is detected and built again.

`num_rows` and `features` are known in both modes as soon as the dataset loads. On a streaming dataset, `num_rows` becomes `None` after an operation that can drop rows, such as `filter`, `batch` or a batched `map`.

## Columns and filters

`columns` reads only some columns, in the order you give. `filter` is a SQL predicate that Lance applies while reading, so rows that don't match are never sent:

```python
ds = load_dataset(
    "flock/fineweb-sample",
    columns=["text", "url"],
    filter="language = 'en' AND length(text) > 200",
)
```

The hub has no splits, and `load_dataset(..., split=...)` raises a `TypeError`. A dataset with several splits has a `split` column instead:

```python
test = load_dataset("flock/mmlu", filter="split = 'test'")
```

`filter` is different from [`~hub.dataset.Dataset.filter`], which runs a Python function on every row after it is read, as in HF.

## Random access

`ds[i]`, slices and lists of indices read the rows from Lance in both modes, without a local copy:

```python
row = ds[0]  # a row, as a dict
last = ds[-1]
batch = ds[100:110]  # a batch: a dict of lists, as in HF
picked = ds.take_rows([42, 7, 7])  # any order, with repeats
```

Positions follow iteration order. With a `filter`, they count matching rows only. An index out of range raises `IndexError`.

On a streaming dataset, random access works until you apply an HF operation such as `map`, and then raises `TypeError`. Index the dataset you loaded instead, or load it with `streaming=False`, where HF's own indexing takes over after a transform.

## HF operations

`map`, `filter`, `shuffle`, `batch`, `select_columns`, `remove_columns`, `rename_column`, `rename_columns`, `cast_column`, `with_format`, `skip`, `take` and `shard` work as in HF and return a `flock.hub.Dataset` with the same `repo_id` and `version`. [`~hub.dataset.Dataset.to_hf`] returns the HF object itself, for code that needs one.

`take(n)` is HF's: the first `n` rows. For rows by index, use `take_rows`.

## Resuming a stream

A streaming dataset records its position like HF's. Save the state with your checkpoint and restore it into a freshly loaded dataset at the same version:

```python
ds = load_dataset("flock/fineweb-sample", version=3)
for step, row in enumerate(ds):
    ...
    if step % 1000 == 0:
        save_checkpoint(model, data_state=ds.state_dict())

# After a restart:
ds = load_dataset("flock/fineweb-sample", version=3)
ds.load_state_dict(checkpoint["data_state"])
```

Pin the version (rather than `None`) when you resume, so the state refers to the same rows.

## Workers and nodes

A streaming dataset is a PyTorch `IterableDataset`. In a `DataLoader` with workers, HF hands each worker whole fragments, so every row is read once. Workers beyond the number of fragments get nothing.

For several nodes, split the dataset first with [`~hub.dataset.split_dataset_by_node`]:

```python
from flock.hub import split_dataset_by_node

ds = load_dataset("flock/fineweb-sample", version=3)
ds = split_dataset_by_node(ds, rank=rank, world_size=world_size)
```

When `world_size` divides the number of fragments, each node reads whole fragments. Otherwise each node reads every row and keeps one in `world_size`. To shuffle across nodes, call `shuffle` with the same `seed` on every node, before splitting.
