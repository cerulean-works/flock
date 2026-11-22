# Blend datasets

Blend two or more datasets into a new one, such as 100,000 rows of web text and 200,000 rows of code, or 300,000 rows split 66% and 33%. A blend copies no rows: it reads the rows it takes from the sources' own files. The same sources, amounts and seed always take the same rows, so you can make variations of a mix and compare them.

## Before you start

- The sources are datasets in your organization's `flock-datasets` bucket.
- For Python and the CLI, [install the workspace](../installation) and sign in with `uv run flock login`.

The examples blend `web/fineweb-edu/train`, which has a `text` column, with `code/stack-v2/train`, which has the same text in a `content` column.

## Blend from the Datasets tab

1. Open the folder where the blend should go, and click **New blend**. You can also check datasets in a folder and click **Blend** to start with them as the sources.
2. **Sources.** Pick each source's dataset and **Version**, and use **Add a source** for more, up to 16. Choose how to give the amounts:
   - **Row counts**: each source's rows, such as `100000` or `100k`.
   - **Percentages of a total**: each source's percentage, such as `66%`, and the **Total rows**. The percentages must add up to 100%, within 0.1 points, so 66.6% and 33.3% work.
3. Pick the folder under **In folder**, type a **Name**, and keep or change the **Seed**. It starts at 42.
4. Check **Allow taking a source more than once** if a source has fewer rows than you ask of it. See [Oversampling](#oversampling).
5. Click **Next**. Problems with the sources, such as a source with too few rows, show on this step, and **Next** stays disabled until there are none.
6. **Columns.** Check how each source's columns become the blend's. See [Map columns](#map-columns). Click **Next**.
7. **Confirm.** Read the summary: the rows taken from each source and the share of it that is, the columns, and what the blend writes. Click **Create blend**.

The blend runs as a job. Its progress shows in the folder's list, and the blend opens from there when it's done. Its **About** tab shows the seed, how it was split, and each source with its version and rows.

## Blend from Python or the CLI

```python
from flock.hub import datasets

made = datasets.blend(
    {"web/fineweb-edu/train": 0.66, "code/stack-v2/train": 0.34},
    "mixes/edu-code",
    total=300_000,
    seed=42,
    columns={"text": {"code/stack-v2/train": "content"}},
)
print(made["path"], made["version"], made["rows"])
```

Give each source a row count as an `int`, or a fraction of `total` as a `float`. Write a source as `path@version`, such as `web/fineweb-edu/train@12` or `web/fineweb-edu/train@baseline`, to pin its version. Without one, the blend takes the latest version, and records which.

`datasets.blend_preview` takes the same arguments, writes nothing, and returns the rows each source gives, the column map and every problem. `blend` waits and prints the job's progress. Pass `wait=False` to return the job at once.

The CLI takes the same options:

```bash
uv run flock datasets blend mixes/edu-code \
  --source web/fineweb-edu/train=66% \
  --source code/stack-v2/train=34% \
  --total 300000 --seed 42 --columns columns.json
```

Add `--preview` to print the column map and the problems without writing anything.

## Choose the seed

The seed decides which rows each source gives. The same seed always takes the same rows:

- Two blends with the same sources, versions, amounts and seed are identical.
- Changing one source's amount doesn't change the rows the other sources give.
- With the same seed, a smaller amount takes some of the rows a larger amount takes. 100,000 rows of a source are among the 200,000 rows the same seed takes from it.

To make another sample of the same mix, change only the seed. **Blend again…** on a blend's **About** tab opens the dialog with its settings and the next seed.

## Oversampling

A source with fewer rows than you ask of it is refused, unless you allow taking it more than once (`replace=True`, or `--replace`). Then the blend takes every row as many whole times as fit, and samples the rest. Asking 60 rows of a source with 25 takes every row twice, and 10 of them a third time.

## Map columns

A blend has one schema. The column map says where each of the blend's columns comes from in each source:

| From a source | Meaning |
|---|---|
| a column | the source's column, under the blend's column name. A different name renames it. |
| a column, cast | the source's column converted to the blend's type, when no value can be lost, such as `int32` to `int64` or `float` to `double` |
| a value | the same value in every row from that source, such as `"stack-v2"` |
| null | no value for the rows from that source. The blend's column must be nullable. |

Flock starts from an automatic map: the first source's columns, each taken from the other sources by the same name. A column another source lacks becomes null for it, and a column of a type that converts safely is cast. What the automatic map can't decide shows as a problem to fix.

### In the Datasets tab

The **Columns** step shows a row for each column in the blend and a column for each source:

- Edit a column's name, its type and whether it can be null in the first column. Reorder columns with the arrows, and remove them with the trash icon.
- Pick each source's cell: one of its columns, **Null**, or **Value…**.
- **Not in the blend** lists the sources' columns no blend column takes. Click one to add it, null for the other sources.
- **Add a column naming each row’s source** adds a `source` column filled with each source's path.

### In Python and the CLI

`columns` changes the automatic map. Each key is a blend column, mapping sources to a column name, `datasets.value(...)` or `datasets.null()`:

```python
columns = {
    "text": {"code/stack-v2/train": "content"},
    "source": {
        "web/fineweb-edu/train": datasets.value("fineweb-edu"),
        "code/stack-v2/train": datasets.value("stack-v2"),
    },
}
```

`text` now takes `content` from `code/stack-v2/train`, instead of the automatic map's null. `source` isn't in the automatic map, so it's added. To change a column's type or nullability, pass the whole map as a list instead, in the form `blend_preview` returns it. The CLI's `--columns` file holds either form as JSON.

### What the blend writes

Columns taken straight from a source cost nothing: the blend reads the source's file. A cast, a value, or a source column used for a second blend column is written into small fill files in the blend's folder, with one value per row of the source fragments it reads. Null costs nothing.

## Use and edit the blend

A blend is an ordinary Lance dataset. Read it, filter it, and train on it like any other ([Use datasets](use-datasets)). You can also edit it, fork it, or use it as a source of another blend. Edits write to the blend's own folder and never change the sources.

The blend starts at version 2, tagged `blend`. Version 1 lists every row of the source fragments it reads, and version 2 deletes the rows it didn't take. Lance doesn't allow deletions in the commit that creates a dataset, so they need a second version.

While a blend exists, its sources can't be renamed, moved or deleted, and cleaning up their old versions keeps the files it reads. **Make independent…** on its **About** tab copies its rows into its own files and releases the sources, as for a [fork](fork-datasets#what-the-original-can-no-longer-do). Reading a blend that takes a small share of each source is faster once it's independent, since Lance then reads only the rows it has.

## Check it worked

- The job finishes with the rows you asked for.
- The blend's **About** tab shows the seed and each source's version and rows.
- Blending again with the same settings and seed gives the same rows.

For how blends sample rows and store them, and for every rule and limit, see [Forks and blends](forks-and-blends).
