# Browse storage and datasets

Use Cerulean's dashboard to transfer objects and inspect versioned Lance datasets stored in your organization.

## Objects and datasets

The **Storage** browser exposes buckets and objects. An object can be a checkpoint, a document or any other file; its name is an S3 key inside a bucket. Folder views group keys that share a prefix.

A **Lance dataset** is a versioned table stored as a group of objects. **Datasets** understands that structure and can show rows, schema and versions. Flock discovers datasets in the managed `flock-datasets` bucket rather than requiring a separate registration step.

These views reach the same underlying storage, but their operations have different meaning. Deleting a whole dataset through **Datasets** gives it a recovery window. Deleting its objects through **Storage** bypasses that protection. For scripts, [S3 access](../cli/s3-access) explains keys and authentication before you connect a client.

## Browse and transfer objects

1. Open **Storage** for the intended organization and choose a bucket. The Connect panel shows how to reach it from the CLI or Python.
2. Open the destination folder and pick files to upload, or drop files and folders onto the listing. Wait for their progress to finish.
3. Open an object to check its name, size and preview. Select **Download** and verify the downloaded file. Use **Copy S3 URI** when passing its location to a script.
4. To remove objects or folders, select them and review the deletion confirmation. Folder deletion includes the objects under that prefix.

Storage transfers use the browser's signed-in session to obtain scoped access; you do not need to paste a persistent S3 secret into the dashboard. [S3 behavior](s3-behavior#the-storage-tab) contains upload limits, pagination, signed-link behavior and supported previews.

## Browse datasets and versions

1. Open **Datasets** and navigate to a dataset, such as `examples/questions` from the [Python storage example](../cli/use-python-storage#write-a-lance-dataset).
2. Read its card, then inspect the rows and schema. Open a large cell to see its full value.
3. Open **Versions** to choose an earlier version or tag. A version is a snapshot of the table, so rows and schema are read from that version rather than mixed across writes.
4. Add a tag to a useful version, then use the version selector to verify it resolves to that snapshot. A tag is a name for a version; it does not create another copy of the table.

Writers can append to the dataset from Lance, producing new versions. A `README.md` object in the dataset folder supplies its card. The [dataset reference](s3-behavior#datasets) describes discovery and version metadata.

## Rename, delete and restore

Stop writers before renaming a dataset. Rename copies its objects to a new path; a writer continuing at the old path can create a dataset there again. Use the dataset's rename action and verify the new path.

To remove a dataset with a recovery window, use its delete action in **Datasets**. An organization admin can restore it under **Deleted datasets** in the organization's settings, then check that its rows and versions reappear.

> [!CAUTION]
> Dataset trash lasts 14 days. Deleting objects through Storage or an S3 client bypasses it. Permanently purging trash has no undo.

The [Dataset API](api/datasets) and [S3 behavior](s3-behavior) specify rename/delete limits, managed buckets and tag operations. Use [Python storage](../cli/use-python-storage) to publish a new dataset or [S3 access](../cli/s3-access) to manage ordinary buckets.
