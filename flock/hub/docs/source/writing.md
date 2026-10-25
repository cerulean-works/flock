# Writing datasets

Every write to a hub dataset makes one new version, and older versions stay readable. This page covers one-call pushes, resumable uploads, edits, tags and cards.

## Push data in one call

[`~hub.write.push`] writes data you have in memory as one version and returns the dataset at that version:

```python
from flock.hub import push

ds = push(hf_dataset, "flock/arc-sample", message="import from the ARC release", private=False)
```

| Argument | Default | Meaning |
|---|---|---|
| `data` | | An HF `Dataset` or `IterableDataset`, an Arrow table, record batch or reader, a pandas `DataFrame`, a list of row dicts, or a dict of columns |
| `repo_id` | | `org/name` |
| `mode` | `"append"` | `"append"` adds rows; `"overwrite"` replaces every row and the schema |
| `message` | `None` | The version's message |
| `properties` | `None` | More transaction properties, such as `{"flock.source": "hf:allenai/ai2_arc@<sha>"}` |
| `private` | `None` | For a new dataset, `False` makes it `internal` |
| `visibility` | `None` | For a new dataset, `"private"` or `"internal"`, instead of `private` |

When you append, columns whose types differ from the dataset's (for example pandas `large_string` columns appended to a `string` column) are cast to the dataset's types.

On the server, `push` creates the dataset if it doesn't exist yet, as `push_to_hub` does on Hugging Face. A new dataset is `private` (its organization's members only) unless you pass `private=False` or `visibility="internal"` (anyone signed in can read it). Neither argument changes a dataset that exists. To give a new dataset a description, create it first with [`~hub.write.create_dataset`]. In direct mode the first commit creates the dataset, and there is no visibility.

## Resumable uploads

For data that doesn't fit in memory or takes long to write, use a [`~hub.write.DatasetWriter`]. It writes shards as you go, keeps its progress in a staging directory, and commits them all as one version at the end:

```python
from flock.hub import DatasetWriter

writer = DatasetWriter("flock/fineweb-sample", mode="append", staging_dir="/scratch/fineweb-upload")
for i, path in enumerate(shard_paths):
    writer.write_shard(f"{i:05d}", read_shard(path))
version = writer.commit(message="add the October crawl")
```

If the process dies, run the same script with the same `staging_dir`:

- A shard that was fully written is skipped, so `write_shard` returns `False` for it.
- If the commit already landed, `commit` finds it by the upload's id and returns its version instead of committing again.

Shards are committed in the order of their ids, so give them ids that sort, such as `00042`. Every shard must have the same columns.

### Other writers

Appends that other writers commit after your upload started are kept: your rows are added on top of theirs. If the dataset was overwritten or restored in the meantime, `commit` raises [`~hub.write.CommitConflict`], because your shards were written against data that no longer exists. Rerun the upload with a new staging directory, or with `mode="overwrite"` to replace the dataset.

An upload with `mode="overwrite"` always replaces what is there when it commits, including rows appended after it started.

## Edits

Edits act on the latest version, whichever version your `Dataset` is pinned to, and return the dataset at the version they made:

```python
ds = load_dataset("flock/arc-sample")
ds = ds.delete("label IS NULL", message="drop unlabeled questions")
ds = ds.update({"question": "trim(question)"}, where="question LIKE ' %'")
ds = ds.add_columns({"question_length": "length(question)"})
```

| Method | What it does |
|---|---|
| `delete(where)` | Deletes the rows matching a SQL predicate |
| `update(values, where=None)` | Sets columns to SQL expressions, in matching rows or all rows |
| `add_columns(transforms)` | Adds columns from SQL expressions, Arrow fields (all null) or a function of each batch, as Lance's `add_columns` |
| `restore()` | Makes the pinned version the latest again, as a new version |
| `set_card(text)` | Sets the README, as a new version |

Lance writes updated rows to new fragments, so they move to the end of the iteration order.

These Lance operations can't record a commit message themselves, so the library sends `message` to the server after the edit. In direct mode (a [`~hub.config.HubConfig`] with `root`) there is no server, and the message is dropped.

## Tags

A tag names a version, and `load_dataset` accepts it in place of a number:

```python
ds = load_dataset("flock/arc-sample", version=4)
ds.tag("v1.0")  # tags the pinned version
ds.tag("baseline", version=2)
print(ds.tags)  # {"v1.0": 4, "baseline": 2}
ds.untag("baseline")
```

Tags make no version, so `tag` and `untag` return the same `Dataset`.

## Cards

A card is the dataset's README, in Markdown. `set_card` makes a new version that points to it, so each version keeps the card it had:

```python
ds = ds.set_card("# ARC sample\n\nTwo hundred ARC-Easy questions for smoke tests.\n")
print(ds.card)
print(ds.checkout(3).card)  # the card version 3 had, or None
```

The card is stored once beside the dataset, under `_flock/cards/`, and the version only records its hash.
