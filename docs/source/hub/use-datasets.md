# Use datasets

Read, select and write versioned tables in Cerulean's shared storage with Lance. `flock.hub.s3` supplies authentication; the `lance` Python package handles the data.

## A brief introduction to Lance

[Lance](https://lance.org/) is a columnar data format with a Python library for reading and writing it. A dataset is a table with a typed schema, rows and a version history. Arrow tables and record batches carry the data between Lance and Python; text, numbers, nested values and vectors can live in the same table.

A dataset URI points to a directory of objects, such as `s3://flock-datasets/examples/questions`. Data files hold groups of rows called fragments. A version's manifest records which fragments and schema form its snapshot. Opening a dataset reads its metadata; selecting rows or scanning batches reads the needed data. Selecting only the columns you need reduces the data read.

Writes commit a new version, and existing readers keep their snapshot. Appending adds fragments without copying the whole table. Older versions can share files with newer ones, so deleting files by hand can break several versions at once.

Flock discovers Lance datasets in your organization's managed `flock-datasets` bucket and shows them under **Datasets** in [the dashboard](https://flock.cerulean.works). You do not need to register a dataset separately. Its path, such as `examples/questions`, identifies it within that organization.

## Before you start

[Install the workspace](../installation), which includes Lance and Arrow, then sign in:

```bash
uv run flock login
```

Run Python scripts from the workspace with `uv run python your-script.py`. The examples use Lance 14 and a small table with `id`, `question` and `answer` columns. If you need sample data, start with [Write a dataset](#write-a-dataset), then use its URI and version for the reads below.

## Load a dataset

Request temporary S3 credentials and pass their connection settings to Lance:

```python
import lance
from flock.hub import s3

creds = s3.credentials(read_only=True)
read_options = creds.storage_options()
uri = "s3://flock-datasets/examples/questions"
dataset = lance.dataset(uri, version=1, storage_options=read_options)
print(dataset.version, dataset.count_rows())
print(dataset.schema)
```

The helper uses your default organization. Pass `org="cerulean-labs"` when selecting that organization explicitly. Reading local data uses the same Lance API with a path such as `./questions.lance`, and needs no Flock authentication.

Omitting `version` opens the latest committed snapshot at that moment. A version number pins your read; a tag such as `version="baseline"` resolves to a version when opened. Record the URI and resolved `dataset.version` with your experiment, since a tag can move. An already open handle does not automatically advance when someone appends rows.

### S3 storage options

`storage_options()` supplies the endpoint, region, path-style setting and current temporary key values. You can add per-dataset connection settings, such as a request timeout:

```python
dataset = lance.dataset(
    uri, version=1, storage_options={**read_options, "timeout": "60s"}
)
```

> [!WARNING]
> Temporary S3 keys expire after **8 hours by default**, subject to your organization's policy. These options contain a fixed key pair, and an open Lance dataset does not renew it. Once the keys expire, the next S3 read or write fails and can terminate your training run. Use [credential renewal](#renew-credentials-for-long-runs) for long jobs.

Requesting new keys through `creds` does not update options already held by an open dataset. [Lance's storage options](https://lance.org/guide/object_store/) lists other connection settings.

## Read rows and batches

Inspect a small result as an Arrow table or Python dictionaries:

```python
preview = dataset.to_table(columns=["id", "question"], limit=2)
print(preview.to_pylist())
```

`to_table()` materializes its result in memory. For a large dataset, iterate batches and project only the columns you need:

```python
for batch in dataset.to_batches(columns=["question"], batch_size=1024):
    print(batch.column("question").to_pylist())
```

A `RecordBatch` is a bounded chunk of rows; its size here is measured in rows, not bytes. Large text or image cells can still make a batch large.

### Get rows by position or ID

Choose the selector that matches the identifiers you have:

| You have | Use | Meaning |
|---|---|---|
| Zero-based positions in this snapshot | `dataset.take([2, 0])` | Fetch the third row, then the first, in that order. |
| Values in your own `id` column | A filter on `id` | Match application identifiers, independent of the row's position. |
| Lance `_rowid` values returned by a scan | A filter on `_rowid` | Match Lance's native row identifiers. |

Batch positional lookups together instead of issuing one read per row:

```python
rows = dataset.take([2, 0], columns=["id", "question"])
assert rows["id"].to_pylist() == [103, 101]
```

Positions must be in range. Pin the dataset version when storing positions for later use: deleting rows or rewriting a dataset can change their meaning. A `LanceDataset` is not a Python list; use `take` for these lookups.

For application IDs, filter your explicit column:

```python
matches = dataset.to_table(
    columns=["id", "question"], filter="id IN (103, 101)"
)
assert set(matches["id"].to_pylist()) == {101, 103}
```

Filters select matching rows; they do not order results by the input ID list or enforce uniqueness of your `id` column. Use trusted filter expressions, and escape string literals rather than interpolating arbitrary text. Large, frequent ID lookups can benefit from a [scalar index](https://lance.org/guide/scalar_indexing/).

Expose Lance's native identifiers when scanning:

```python
identified = dataset.to_table(columns=["id"], with_row_id=True)
native_id = int(identified["_rowid"][0].as_py())
selected = dataset.to_table(
    columns=["id", "question"],
    filter=f"_rowid = {native_id}",
    with_row_id=True,
)
assert selected["id"].to_pylist() == [101]
```

An `_rowid` is not a positional index. Without stable row IDs enabled, it reflects a physical address and can change after rewrites. If you need native IDs that survive compaction, select `enable_stable_row_ids=True` when creating the dataset. Keep an explicit application ID column for identities you share outside Lance, and pin the version when retaining native IDs. See [Lance row identifiers](https://lance.org/guide/row_ids/) for their lifecycle.

## Write a dataset

Start a writer script with read/write access and an unused destination. This example creates the data used above:

```python
import lance
import pyarrow as pa
from flock.hub import s3

creds = s3.credentials()
write_options = creds.storage_options()
uri = "s3://flock-datasets/examples/questions"
schema = pa.schema([
    ("id", pa.int64()),
    ("question", pa.string()),
    ("answer", pa.string()),
])
table = pa.Table.from_pylist([
    {"id": 101, "question": "2 + 2?", "answer": "4"},
    {"id": 102, "question": "3 + 3?", "answer": "6"},
    {"id": 103, "question": "4 + 4?", "answer": "8"},
], schema=schema)
written = lance.write_dataset(
    table, uri, mode="create", storage_options=write_options
)
first_version = written.version
check = lance.dataset(uri, version=first_version, storage_options=write_options)
assert check.to_table().equals(table)
```

`mode="create"` refuses an existing dataset. Choose another path to repeat the example. For a local experiment, replace the S3 URI with an unused local directory and omit the credential setup and `storage_options` arguments.

Set the schema explicitly so empty or all-null batches do not infer unintended types. Keep the same schema when appending. Lance also accepts Arrow record-batch readers, so a source larger than memory can be written incrementally. For example, with an existing `schema` and an iterator named `batches` that yields compatible `pa.RecordBatch` objects:

```python
reader = pa.RecordBatchReader.from_batches(schema, batches)
written = lance.write_dataset(
    reader, uri + "-streamed", mode="create", storage_options=write_options
)
```

This commits one dataset version from the stream. Prefer a single streamed write or reasonably sized append batches over one commit per row. The writer's file-size settings control file boundaries; record-batch boundaries do not each become a dataset version.

### Append and name a snapshot

Append new rows to create another version:

```python
more = pa.Table.from_pylist([
    {"id": 104, "question": "5 + 5?", "answer": "10"},
], schema=schema)
updated = lance.write_dataset(
    more, uri, mode="append", storage_options=write_options
)
assert updated.count_rows() == 4
assert check.count_rows() == 3  # the earlier handle keeps its snapshot

updated.tags.create("baseline", first_version)
pinned = lance.dataset(uri, version="baseline", storage_options=write_options)
assert pinned.version == first_version
```

Each successful append adds those rows again; it does not deduplicate by `id`. Create the tag once. Record the resolved version number for a reproducible run.

Use `mode="overwrite"` only when you intend to replace the current table contents with a new snapshot. It is not an append. Older snapshots remain readable only while their required files and manifests are retained. Do not run version cleanup while experiments still need that history; tags protect versions from Lance cleanup. The [dataset behavior reference](../app/s3-behavior#datasets) explains Flock's version and tag handling.

## Renew credentials for long runs

For reads or writes that can outlast the temporary keys, call `s3.get_process()` before creating the dataset. This is the setup used by the [trainer's S3 dataset helper](../training/trainer):

```python
import lance
from flock.hub import s3

s3.get_process(org="cerulean-labs", read_only=True)
dataset = lance.dataset("s3://flock-datasets/examples/questions", version=1)
```

The call configures this process's AWS profile, endpoint and region, removes conflicting credentials, and applies the environment itself. Lance's native AWS provider runs a local credential process to fetch ephemeral keys and renew them as storage requests need fresh credentials. You do not need shell exports or a separate `flock s3 configure` step.

Omit access keys from `storage_options` when using this flow. Passing the static options from the earlier examples selects fixed-key authentication instead of native renewal. You can still pass non-credential options such as `{"timeout": "60s"}`.

For a writer, use `s3.get_process()` without `read_only=True` and open or write datasets without the earlier `write_options`. Omit `org` to use your default organization. Configure access before opening clients; existing handles retain their configuration. Setup affects this process and children it starts, rather than its parent shell.

The [Python S3 reference](../cli/python-s3#native-renewal-from-python) explains environment variables, temporary-file lifetime and renewal behavior. For unattended jobs, supply [FLOCK_TOKEN](../cli/authenticate-jobs).

## Read from worker processes

For PyTorch, use `SafeLanceDataset` to lazily open a reader inside each worker, and use `spawn` so workers start fresh processes. Put setup in the parent before starting them:

```python
from lance.torch.data import SafeLanceDataset
from torch.utils.data import DataLoader
from flock.hub import s3


def main():
    s3.get_process(read_only=True)
    dataset = SafeLanceDataset(
        uri="s3://flock-datasets/examples/questions",
        dataset_options={"version": 1},
    )
    loader = DataLoader(
        dataset,
        batch_size=2,
        num_workers=2,
        multiprocessing_context="spawn",
        persistent_workers=True,
    )
    for batch in loader:
        print(batch["question"])


if __name__ == "__main__":
    main()
```

Save this as a script and run it with `uv run python your-script.py`. The `main` guard prevents workers from repeating setup when importing it. `SafeLanceDataset` batches positional reads with `take`; it does not by itself make `fork` safe with Lance's native threads.

Spawned workers inherit the credential-process environment and can renew their own keys. Keep the parent alive while workers use its temporary profile files. For separately launched jobs or another machine, configure authentication in that process too. If passing an explicit subprocess environment, take `os.environ.copy()` after `get_process()` so it includes both the applied settings and removal of conflicting keys.

The [trainer](../training/trainer) already sets up native credentials for its S3 dataset helper and uses spawned workers.

## Check the published dataset

1. Open **Datasets** for the intended organization and find `examples/questions`.
2. Check the schema and rows, then open **Versions** to verify the append and `baseline` tag.
3. Upload a `README.md` to the dataset folder through **Storage** to describe the schema, source and intended use. Its card is shared by every version.

Use the dashboard's dataset actions to [rename, delete or restore datasets](../app/storage-and-datasets#rename-delete-and-restore). Deleting individual data files through Storage or an S3 client bypasses dataset trash and can make snapshots unreadable.
