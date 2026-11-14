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

1. Open **Datasets** and navigate to a dataset, such as `examples/questions` from the [dataset guide](../hub/use-datasets#write-a-dataset).
2. Read its card, then inspect the rows and schema. Open a large cell to see its full value.
3. Open **Versions** to choose an earlier version or tag. A version is a snapshot of the table, so rows and schema are read from that version rather than mixed across writes.
4. Add a tag to a useful version, then use the version selector to verify it resolves to that snapshot. A tag is a name for a version; it does not create another copy of the table.

Writers can append to the dataset from Lance, producing new versions. A `README.md` object in the dataset folder supplies its card. The [dataset reference](s3-behavior#datasets) describes discovery and version metadata.

## Select datasets and folders

In a folder of **Datasets**, members and admins can act on several datasets and folders at once. Loose files can't be selected. Manage them in **Storage**.

- **Check a row.** Hover it to show the checkbox on its left, and click the checkbox. Once anything is checked, every row shows its checkbox. Clicking a row's name still opens it.
- **Check a range.** Shift-click another checkbox to check every row between it and the last one you clicked.
- **Check every row.** Click the checkbox in the header. It checks the rows loaded so far. **Load more** adds rows you can then check too.
- **Clear.** Click **Clear** in the bar, or press Escape. Opening another folder clears it too.

While rows are checked, the header shows how many, with **Move** and **Delete** for all of them.

## Move datasets and folders

1. Check the datasets and folders to move, and click **Move**.
2. Type the destination folder, or browse to it in the list below. Leave it empty for the top of `flock-datasets`.
3. Click **Move**.

Each item keeps its last name. `eval/mmlu` moved to `archive` becomes `archive/mmlu`. A folder moves with everything in it, datasets and loose files. Nothing may exist at an item's new path, and a folder can't move into itself.

Each item moves on its own. If one fails, for example because its new path is taken, the rest still move. The dialog then lists the items that didn't move, each with the reason.

Stop writers first. A move copies the objects to the new path and then deletes the old ones. If a dataset is written to during the move, that item doesn't move.

## Rename, delete and restore

Each dataset and folder row has a **⋯** menu, shown on hover, with **Rename…** and **Delete…**. A dataset's own page has the same actions.

**Rename** asks for the new full path, such as `archive/mmlu-v2`, so it can move an item to another folder as well. A folder is renamed with everything in it. Stop writers before renaming. Rename copies the objects to the new path, and a writer continuing at the old path can create a dataset there again.

**Delete** moves datasets and folders to the trash:

1. Check the items and click **Delete**, or use **Delete…** in one row's menu.
2. Confirm. For one item, type its path, such as `eval/mmlu`. For several, the dialog lists them and asks for their number.
3. Click the delete button.

A deleted folder is one entry in the trash, with everything in it. An organization admin can restore a dataset or a folder under **Deleted datasets** in the organization's settings for 14 days. A folder comes back as a whole. After restoring, check that the rows and versions reappear.

Every type-to-confirm prompt has a **Copy** button next to the text to type. Click it and paste the text into the field.

> [!CAUTION]
> Dataset trash lasts 14 days. Deleting objects through Storage or an S3 client bypasses it. Permanently purging trash has no undo.

The [Dataset API](api/datasets) and [S3 behavior](s3-behavior) specify rename/delete limits, managed buckets and tag operations. Use [the dataset guide](../hub/use-datasets) to publish a new dataset or [S3 access](../cli/s3-access) to manage ordinary buckets.
