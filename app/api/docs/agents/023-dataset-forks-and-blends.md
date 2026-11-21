# 023: forking and blending datasets without copying them

| | |
| --- | --- |
| Status | built, in review |
| Date | 2026-11-21 |
| Builds on | [013](013-datasets-tab.md), [017](017-hf-compat.md), [019](019-bulk-select.md), [021](021-public-access-checks.md), [022](022-project-scopes.md) |
| Amends | D93 and D132 (a dataset other datasets read from can't be deleted), D95 and D131 (nor renamed or moved), D96 (`flock-ref-` tags are Flock's), D159 and D160 of 022 (forks and blends may cross scopes), D124 (blends run as jobs the way imports do) |
| Keeps | D85 to D92, D94, D97 to D123, D125 to D130, D133 to D158, D161 to D170 |

## 1. Goal

Pretraining work tries many mixes and filters of the same large datasets. Copying a dataset to
try something on it doesn't scale, and editing the original pollutes what everyone else reads.
This stage adds two ways to make a new dataset out of existing ones, both copy-on-write, both
from the Datasets tab:

1. **Forks** (D171). A new dataset at its own path that starts as one version of another, and
   is edited freely without touching the original.
2. **Blends** (D174). A new dataset whose rows are a seeded, deterministic sample of several
   datasets, given as row counts (100k of A, 200k of B) or as fractions of a total (300k rows,
   66% A and 34% B). Sources whose columns differ are lined up by a column map (D175).

Neither copies row data. Each is a Lance dataset whose manifest points at the sources' existing
data files, so creating one writes kilobytes to megabytes of metadata whatever the sources'
size. Edits write only under the new dataset's own folder. Both are ordinary Lance datasets
afterwards: the trainer, the Rows tab, tags and versions work on them unchanged.

To support that safely the stage also adds:

3. **Pins** (D173): every source version a fork or blend reads is kept from Lance's cleanup, and
   Flock knows which datasets read from which.
4. **Guards** (D177): renaming, moving or deleting a dataset that others read from is refused.
5. **Making a dataset independent** (D178): rewriting a fork or blend into its own files, which
   releases its sources.

**Out of scope:**
- Lance branches (`create_branch`). They write inside the source's folder (`tree/{name}/`,
  `_refs/branches/`), which is the pollution forks avoid, and the tab, trash, rename and
  privacy rules all treat that folder as one dataset. A later stage can add them.
- merging a fork back into its source;
- blending across orgs;
- editing rows from the UI. Filtering a fork (`ds.delete("int_score < 3")`), appends, updates
  and `merge_insert` are done from Python with Lance, through the proxy;
- mapping fields inside struct and list columns (D175 maps top-level columns).

---

## 2. What Lance provides

Checked against pylance 14.0.3 on local datasets. The scripts are in this thread's
`.amp/in/orb-backup/lance/`. The S3 behavior is checked in Part 0 of the plan (section 3).

| Capability | Lance | Observed |
| --- | --- | --- |
| Shallow clone | `ds.shallow_clone(target, version_or_tag)` | The clone holds only `_versions/` and `_transactions/` (662 B for a 1,000-row source). The source is base path 0, by **absolute** URI. |
| Copy-on-write edits | `delete`, `update`, append | Write `_deletions/` or `data/` under the clone only. The source's files and rows don't change. |
| A clone of a clone | `shallow_clone` on a fork | The new manifest lists **both** datasets as bases: the fork's own files and the original's. |
| Several sources in one manifest | `LanceOperation.Overwrite(schema, fragments, initial_bases=[DatasetBasePath…])`, `DataFile(…, base_id=n)` | 3 KB manifest for 2,000 rows from two datasets. |
| Subsampling without copying | `LanceFragment.delete_rows(offsets)` then `LanceOperation.Delete` | One deletion vector per fragment, written in the new dataset. Exact counts per source. |
| Upsampling without copying | the same source fragment listed twice under two fragment ids | 490 live rows listed twice read back as 980. |
| Renaming, reordering and dropping columns | `DataFile.fields` remapped to the new schema's field ids, `-2` for columns left out | Read correctly. B's `doc_id` and `content` read as the blend's `id` and `text`. No data written. |
| A column a source doesn't have | a field of the new schema that none of the fragment's files hold | Reads as null. No data written. |
| Filling or casting a column for one source | a small Lance file per fragment in the new dataset's `data/` holding only that column, added to the fragment's files in the same `Overwrite` | B's missing `src` filled with `"B"`, and its `int32` `n` read as `int64`. Upsampled copies of a fragment shared one fill file. Later deletes applied. A constant string column for 1M rows is 271 B. |
| Protecting a version | a tag on it | `cleanup_old_versions` refuses (or with `error_if_tagged_old_versions=False`, skips) tagged versions. The clone stays readable. `flock-ref-…` passes D96's tag rule. |
| Making a clone independent | overwrite with its own rows, then `cleanup_old_versions` on the clone | The source's files were untouched, and the clone read correctly after the source was removed. |

And the limits found on the way:

- **Cleaning up a source breaks what reads from it.** An untagged source version was cleaned up,
  and the blend reading it failed with `Not found: …/A/data/….lance`. Lance keeps no record of
  who reads a dataset's files.
- **`Overwrite` takes only fragments without deletion files** ("Overwrite fragments must be newly
  written"). A blend is two commits: version 1 lists the fragments, version 2 adds deletion
  vectors.
- **Base paths are absolute URIs, bucket included.** A relative path resolved against the
  process's working directory, not the dataset.
- **Compaction copies.** `optimize.compact_files()` on a clone rewrote the rows it compacted
  into the clone's `data/`. That's correct, but it's a copy.

---

## 3. Design decisions

The numbering continues from 022 (D158 to D170).

### D171. A fork is a shallow clone at its own path

- **What it is.** `source.shallow_clone(dest, version)`. Its version 1 has exactly the rows of
  `source@version`. Every later version is the fork's own.
- **Where it lives.** Any free path in `flock-datasets` (D95's rules for new paths). It's a
  dataset of its own: its own README, tags, privacy flag (D155: new paths are private), trash
  entry and Versions tab.
- **Which version.** A number or tag, resolved once and recorded. Defaults to the version shown
  on the dataset page, which is the latest unless one was picked.
- **Across scopes (022).** A source and its fork may be in different scopes: forking a global
  corpus into a project is the way to get a project's own copy without copying it, which 022
  left out. Project datasets live in the same bucket under `-projects/{pk}/` (D160 of 022), so
  base paths and pins work unchanged. `dataset_links` stores S3 paths, so scope changes are
  ordinary moves under D177.
- **Forks of forks and of blends** are allowed. Lance lists every dataset the files come from as
  a base (section 2), and D173 pins each of them.
- **It's synchronous.** A fork writes one manifest, one transaction file and its pins, so
  `POST datasets/fork` answers when it's done.
- **The first commit records where it came from** in `transaction_properties` as
  `flock.lineage` (as `flock.source` does for imports, D121), so the dataset says so outside
  Flock too.

### D172. Forks and blends name buckets by their upstream names, and the proxy accepts them

A fork's manifest holds its sources' absolute URIs. Clients reach a bucket as
`s3://flock-datasets/…` through the proxy. The API reads Lance straight upstream as
`s3://{prefix}{org pk:05}-flock-datasets/…` (D94). A manifest written with either name can't be
read by the other side today.

- **The API creates every fork and blend,** so base paths always hold the upstream name. The
  CLI and Python functions (D180) call the API's routes and don't write manifests themselves.
- **The proxy accepts an org's upstream bucket name as another name for that bucket,** for
  keys of that org only. `GET /{prefix}00042-flock-datasets/web/x/data/….lance` is the same
  request as `GET /flock-datasets/web/x/data/….lance`, with the same permission checks. Another
  org's upstream name is still `NoSuchBucket`. Errors keep naming the bucket by its short name.
- **So both sides read the same manifest.** The API opens it directly as it opens any dataset.
  Lance through the proxy (the trainer, `lance.dataset` in a notebook) requests the upstream
  name for files in other bases, and the proxy serves them.
- **Upstream names are stable.** They use the org's pk, not its name, so renaming an org breaks
  nothing.
- **Hand-made clones aren't supported.** A user who runs `shallow_clone` through the proxy gets a
  manifest naming `s3://flock-datasets/…`, which the API can't open. The tab shows it as any
  unreadable dataset (D94's 502), and the guide says to fork with Flock.
- **Rejected: the API reading through the proxy** with an internal key, so manifests hold
  `flock-datasets`. It adds a hop to every read the tab makes, and needs a per-org service key.
  The alias is a lookup in the proxy's bucket map.

### D173. Pins, and the table of who reads what

- **A pin is a Lance tag** on the source, `flock-ref-{link ulid}`, on the version read. Lance's
  cleanup then never removes that version's files, whoever runs it, from the API or a script.
- **Pins are inherited.** A fork of `F` pins `F` at its version, and copies every pin `F` holds,
  since its manifest reads those datasets' files too (section 2). A blend does the same for each
  source. So purging `F` later leaves its fork readable.
- **`dataset_links`** (migration `0022`) records each pin:
  `(pk, org_pk, ulid, dependent_path, source_path, source_version, kind, created_at)`, with
  `kind` `fork`, `blend` or `inherited`. It's what answers "what reads this dataset?" and "where
  did this come from?" without opening manifests. This is the first table about datasets
  (013 avoided one, D85), and it's kept in step the same way `private_datasets` is: every route
  that moves a path updates it in the same transaction. Datasets made outside Flock never
  appear in it, which is right, since they can't depend on anything (D172).
- **The pins and rows are written before the manifest,** under the sources' locks (`_locked`,
  D95). A crash after the tags but before the manifest leaves pins with no dataset. The trash
  sweep removes links whose `dependent_path` has no `_versions/` after a day, with their tags.
- **Released only on a permanent purge** of the dependent (D93's "Delete now" or the 14-day
  sweep), or by D178. Moving the dependent to the trash keeps them, so restoring it works.
- **Tags starting with `flock-ref-` are reserved.** The tag routes refuse to create, move or
  delete them ("Tags starting with flock-ref- keep a version for the datasets that read it.").

### D174. A blend

- **The request** names two or more sources, each a path and an optional version (resolved once
  and recorded, as D171), and either:
  - **counts:** an integer of rows for each source; or
  - **fractions:** a fraction for each, summing to 1 within 1e-9, and a `total`.

  Mixing the two is refused. Fractions become counts by largest remainder: each source gets
  `floor(f × total)`, and the rows left go one each to the largest remainders, ties by the order
  given. The counts always sum to `total` (300,000 at 66%/34% is 198,000 and 102,000).
- **`seed` is required** by the API, Python and the CLI, so every blend records the seed it
  used. The UI fills in 42, shown and editable, the same default as the trainer's
  `ResumableBatchDataSampler`.
- **Which rows.** Each live row of a source at its version is named by `(fragment id, offset)`.
  It gets the key
  `splitmix64(seed ^ fnv1a64(source path, version) ^ (fragment_id << 32 | offset))`, and the
  blend takes the `k` rows with the smallest keys. Three things follow:
  - **Reproducible across releases.** The function is Flock's, not NumPy's generator, whose
    streams NEP 19 doesn't promise to keep.
  - **Nested.** With the same seed, the 100k sample of a source is inside its 200k sample, so
    scaling one source in an ablation changes only the rows it has to.
  - **Independent of order,** so fragments can be keyed in parallel.
- **Without reading rows.** A fragment's live offsets come from its row count and deletion file
  (`to_table(columns=[], with_row_address=True)`), never its columns. The `k` smallest keys
  are found in two passes per source: a histogram of the top 16 bits finds the threshold
  bucket, then the rows below it are kept. Memory stays bounded at billions of rows.
- **Upsampling.** Asking for more rows than a source has live is refused, naming the source and
  its live rows, unless the request says `replace: true`. Then the source's fragments are listed
  `floor(k / n)` times in full, and once more with the `k mod n` smallest keys. Each listing is
  a new fragment id over the same files.
- **Columns** come from the column map (D175).
- **Fragment order** is a seeded shuffle of whole fragments, so a scan in order isn't all of A
  then all of B. The trainer's sampler shuffles rows anyway.
- **Fragments with no rows sampled** are left out.
- **Two commits.** Version 1: `Overwrite(schema, fragments, initial_bases=…)` with every
  source as a base. Version 2: `Delete(updated, [], "flock blend")`, whose deletion vector for
  each fragment is the source's deleted offsets plus those not sampled. Version 2 is tagged
  `blend`. Version 1 holds every row of the fragments listed, and the Versions tab labels it
  "Blend in progress". Part 0 checks whether `commit_batch` can make the two one version.
- **Pins** as D173, then lineage in version 1's `transaction_properties` and in
  `dataset_links`:

  ```json
  {
    "kind": "blend",
    "seed": 17,
    "spec": {"mode": "fraction", "total": 300000},
    "columns": [
      {"name": "text", "type": "string", "from": {"web/fineweb-edu/train": {"column": "text"}, "code/stack-v2/train": {"column": "content"}}},
      {"name": "source", "type": "string", "from": {"web/fineweb-edu/train": {"value": "fineweb-edu"}, "code/stack-v2/train": {"value": "stack-v2"}}}
    ],
    "replace": false,
    "sources": [
      {"path": "web/fineweb-edu/train", "version": 12, "fraction": 0.66, "rows": 198000, "live": 9672101024},
      {"path": "code/stack-v2/train", "version": 4, "fraction": 0.34, "rows": 102000, "live": 610000000}
    ]
  }
  ```

  The record and the fixed sampling function are enough to make the same blend again.
- **Blends are editable** from Python with Lance, as forks are. A blend with another seed is a
  new blend, not an edit.

### D175. Column maps

Sources of a mix rarely agree on columns: one calls it `text` and another `content`, one has
`url` and another doesn't, one stores `int_score` as `int32` and another as `int64`. A blend has
one output schema, and a **column map** says where each output column comes from in each
source.

- **An output column** has a name, an Arrow type and whether it's nullable. For each source it
  takes one of:

  | From | Means | Costs |
  | --- | --- | --- |
  | `{"column": "content"}` | that source column, under the output's name. A rename when the names differ. | nothing: the fragment's file is mapped to the output's field id |
  | `{"null": true}` | null for every row of this source. The output must be nullable. | nothing: no file holds the field |
  | `{"value": "stack-v2"}` | the same value for every row of this source, of the output's type | one file per fragment of the column alone, about 300 B per million rows |
  | `{"column": "n", "cast": true}` | a source column of another type, converted to the output's type | one file per fragment holding the converted column, so a copy of that column only |

- **Source columns no output uses** are dropped (`-2`) and never read.
- **Casts** are the ones Arrow does without losing values: wider ints and floats, int to float,
  `string` to `large_string`, and `T` to nullable `T`. Others are refused, naming the column,
  both types and the source. The cast runs when the blend is built and fails it if a value
  doesn't fit.
- **Fills and casts are written with the blend.** A source's sampled fragments get one extra
  file each in the blend's own `data/`, written before version 1 and listed with the
  fragment's other files in the same `Overwrite`. Upsampled copies of a fragment (D174) share
  it. So the blend still never copies a column it doesn't change. The preview (D179) shows the
  bytes a cast will write.
- **Nested columns** (structs, lists) map whole. A struct column can be taken as it is, filled
  with null, or left out. Mapping a field inside one isn't supported.
- **The automatic map** is where both the API and the UI start, so blends of matching sources
  need no map at all:
  1. The outputs are the first source's columns, in its order.
  2. For each other source, an output takes the column of the same name and type; else the
     column of the same name with a safe cast; else null, and the output becomes nullable.
  3. Columns only later sources have aren't outputs. The UI lists them so one click adds them,
     with null for the sources lacking them.
- **Problems block the blend:** a source column of the same name whose type can't be cast, an
  output that isn't nullable with a source giving null, two outputs with one name, or a value
  that isn't of the output's type. The preview names each one.
- **A source column** is offered as one checkbox in the UI, "Add a column naming each row's
  source": an output `source` (`string`) filled with each source's path, or a name typed per
  source. It's the most common fill, and costs a few hundred bytes per million rows.
- **The map is recorded** in the lineage (D174) as given, after the automatic map is applied, so
  the blend can be made again exactly.

### D176. Blends run as jobs

- **A table for long dataset work.** Migration `0022` adds `dataset_jobs`, with the same
  columns and states as `hf_imports` (D124: `running`, `finished`, `failed`, `cancelled`,
  `interrupted`), a `kind` of `blend` or `independent` (D178), the request as `options`, and
  `rows_total`/`rows_done` for progress. HF imports keep their table.
- **Why a job.** Keying billions of row addresses takes minutes. Creating a fork doesn't, so
  forks aren't jobs.
- **Where it runs.** In the API's process pool, as D124's conversions do, capped by
  `FLOCK_DATASET_JOB_WORKERS` (default 4). Each worker keys whole fragments.
- **Progress** shows as a row at the top of the destination folder, as imports do (D124), with
  Cancel. A cancelled or failed blend removes what it wrote at the destination and its pins.
  `interrupted` (the API restarted) does the same in the sweep.
- **Routes:** `POST datasets/blend` (202 with the job), `GET datasets/jobs`,
  `GET datasets/jobs/{pk}`, `DELETE datasets/jobs/{pk}`.

### D177. Datasets others read from can't move

Renaming, moving or deleting copies a dataset's objects and deletes the originals (D93, D95).
For a source, that breaks every fork and blend reading it, including the trash window, since
the trash is another bucket. Lance can add a base to a manifest but not repoint one.

- **Refused,** for a dataset with rows in `dataset_links` as a source, with 409 and the list of
  dependents: "web/fineweb-edu/train can't be deleted while 3 datasets read from it:
  exp/alice/edu-filtered, mixes/edu-code-1to2 and 1 more. Delete them or make them independent
  first." Folders are refused when any dataset in them is a source of a dataset outside them. A
  folder moved with its sources and their dependents together still moves, and its links are
  rewritten.
- **The bulk dialogs** (D131, D132) list these items as not moved or not deleted, with the
  reason, as they do for other failures.
- **Dependents themselves** rename, move and delete freely. The links follow them.
- **The dashboard says so before asking.** `GET info` returns `dependents` (count and the first
  10 paths), and the Rename, Move and Delete dialogs show the refusal and disable the button,
  rather than failing after a type-to-confirm.

### D178. Making a fork or blend independent

The way out of D177, and a way to read a heavily subsampled blend at full speed.

- **What it does.** Writes the dataset's live rows at its latest version as a new version in its
  own `data/`, removes its own `flock-ref-` and user tags on earlier versions, runs
  `cleanup_old_versions` on it for every earlier version, then releases its pins and links.
  Section 2's last row checked that this leaves the sources untouched and the result readable
  without them.
- **It costs a full copy and the history.** The dialog says both, with the size, and asks for
  the path to confirm. Earlier versions are gone afterwards.
- **A job** (`kind = independent`, D176), with D124's size limit
  (`FLOCK_HF_IMPORT_MAX_BYTES` reused, default 50 GB). Over it, the dialog shows the
  `flock datasets make-independent` command to run instead.
- **Route:** `POST datasets/independent {path}` (202).

### D179. The Datasets tab

- **Fork.** A "Fork" button on the dataset page, next to Rename, and "Fork…" in the row menu
  (D133). The dialog has the source (fixed), the version (D89's picker, defaulting to the one
  shown) and the destination as a folder (D131's folder picker, defaulting to the source's) and a name (default `{name}-fork`, then `-fork-2`…). It opens
  the new dataset when done.
- **Blend.** A "New blend" button in the folder header, next to "Import from Hugging Face" and
  "New folder", and a "Blend…" action in the selection bar (D130) when two or more datasets are
  selected, which starts the dialog with them. The dialog:
  - lists sources, each with a dataset picker (D131's folder picker, datasets only), a version,
    and its amount. Each row shows the source's live rows;
  - switches between **Counts** and **Percentages of a total**;
  - has the seed, filled in as 42, with a button that sets a random one;
  - has "Allow taking a source more than once" (`replace`);
  - has a **Columns** step, below;
  - shows a live summary from `POST datasets/blend/preview`: rows from each source, the share
    of the source that is, the total, the bytes fills and casts will write, and the problems (a
    source too small, D175's column problems), with Create disabled until there are none.
- **The Columns step** is a grid: one row per output column, one column per source.
  - **The first column** of each row is the output: its name (editable), its type (a menu of
    the Arrow types sources use) and a nullable switch. Up and down buttons reorder rows. A trash icon
    removes the output.
  - **Each source's cell** is a menu: that source's columns (with their types, those of a
    matching type first), "Null", and "Value…" (an input of the output's type). A column of
    another type that casts safely shows "→ int64"; one that doesn't is disabled with the
    reason.
  - **It opens filled with the automatic map** (D175). A cell the automatic map had to decide
    is marked: "renamed", "cast" or "null". Cells with a problem are red, with the reason on
    hover, and the step's header counts them ("2 columns need a decision").
  - **Below the grid,** "Not in the blend" lists the source columns no output uses, by source.
    "Add" makes one an output, filled from that column and with null for the other sources.
  - **"Add a column naming each row's source"** adds the `source` output with each source's
    path, editable per source.
  - **Small screens** show one source at a time, with a source switcher above the grid.
  - **Collapsed by default** when the automatic map has no problems and nothing was renamed,
    cast or filled: "Columns: 5, all sources match". Otherwise it opens on its own.
- **The dataset page of a fork or blend** shows where it came from at the top of the About tab:
  "Forked from **web/fineweb-edu/train** at version 12", or a table of the blend's sources with
  versions, rows and shares, the seed and the spec. Each source links to that version. A blend
  has "Blend again…", which opens the dialog filled in with its settings and its seed plus 1,
  so the new blend differs unless the seed is changed back.
- **"Read by"** on any source's About tab lists the datasets reading it, from `dataset_links`.
- **Folder listings** mark forks and blends with a small label after the name, from
  `dataset_links` (one query per page, no manifest reads).
- **The Versions tab** hides `flock-ref-` tags and shows "Kept for 3 datasets" on a pinned
  version, linking to them.
- **Members and admins** see these actions. Viewers of a public dataset (015) see the lineage
  but not the buttons.
- **UI copy** in plain sentences, as elsewhere.

### D180. Python and the CLI

All call the API, per D172.

```python
from flock.hub import datasets

datasets.fork("web/fineweb-edu/train", "exp/alice/edu", version="v3")
datasets.blend(
    {"web/fineweb-edu/train": 0.66, "code/stack-v2/train": 0.34},
    "mixes/edu-code",
    total=300_000,
    seed=17,
    columns={
        "text": {"code/stack-v2/train": "content"},  # a rename for one source
        "source": {
            "web/fineweb-edu/train": datasets.value("fineweb-edu"),
            "code/stack-v2/train": datasets.value("stack-v2"),
        },
    },
)
datasets.make_independent("mixes/edu-code")
```

- `columns` changes the automatic map (D175): each key is an output column, and each source
  either names a column, or gives `datasets.value(x)` or `datasets.null()`. A named column of
  another type is cast when the cast is safe, as in the UI. Outputs not named keep the
  automatic map. `columns=[...]` instead keeps
  only those outputs. A full map (types, nullability, order) is passed as D174's JSON.
  `datasets.blend_preview(...)` returns the map and the problems without creating anything.
- `fork` returns the new path and version. `blend` and `make_independent` wait for their job,
  printing progress to stderr as `convert` does, and return the same. `wait=False` returns the
  job instead.
- `flock datasets fork SRC DEST [--version]`,
  `flock datasets blend DEST --source PATH[@VERSION]=AMOUNT… [--total N] --seed S
  [--columns MAP.json] [--replace]` and `flock datasets make-independent PATH`. An amount
  with a `%` or a decimal point is a fraction. `--columns` takes the same map as `columns`.
  `flock datasets blend --preview` prints the automatic map and its problems.
- **Editing from Python is plain Lance** through the proxy, as the guide shows today. Filtering
  a fork is `lance.dataset(uri).delete("int_score < 3")`, which writes deletion vectors only.

---

## 4. Plan

One PR, after this doc is reviewed, in the parts below.

### Part 0. Check on the real stack, before building

On the local stack's Ceph and proxy:

- a fork created upstream reads through the proxy with the alias (D172), with Lance's native
  credential process, from the trainer's `get_dataset`;
- the same for a blend over two sources, and a fork of a fork;
- whether `commit_batch` of the `Overwrite` and the `Delete` makes one version (D174);
- fill files (D175) in a blend read through the proxy, and a struct column mapped whole;
- the time and memory to key a 1B-row source, to set a row limit if one is needed.

Anything that fails changes this doc before Part 1.

### Part 1. API

- Migration `0022`: `dataset_links`, `dataset_jobs`.
- The proxy's alias (D172), in `s3_proxy/auth.py`'s bucket map.
- `core/datasets.py`: fork, blend (keying in a pool module beside `_convert.py`'s), the
  automatic column map, fills and casts (D175), make independent, the link updates in rename,
  move, delete, restore and purge, the D177 refusals, and `dependents` and `lineage` in `info`
  and `at`.
- The tag routes refuse `flock-ref-`.

### Part 2. Web

- The Fork, Blend and Make independent dialogs, and the blend's Columns step, following
  `RenameDialog`, `MoveDatasetsDialog` and `HFDatasetDialog`.
- The About tab's lineage and "Read by", the listing label, the Versions tab's pins, and job
  rows in the folder listing, following imports.
- The refusals in the Rename, Move and Delete dialogs.

### Part 3. `flock.hub.datasets` and the CLI (D180)

### Tests

- `tests/app/test_datasets.py`: a fork's edits leave the source unchanged; a fork of a fork
  pins both; blend counts exact for both forms, and the largest-remainder rounding; the same
  seed gives the same rows across two runs, and a smaller count is a subset of a larger;
  upsampling refused without `replace` and exact with it; the automatic map on sources that
  match, differ by a name, by a castable type and by a missing column; a rename, a null, a
  value and a cast each reading back correctly, and leaving the source unchanged; an
  uncastable type and a non-nullable null refused; a column used by no output never read; a
  source's cleanup keeps its fork readable; renaming, moving and deleting a source refused, and
  its dependents moving freely with their links; purging a dependent releases its pins; making
  a dataset independent leaves its sources untouched and readable without them; a cancelled
  blend leaves nothing.
- `tests/s3`: the alias, and another org's upstream name refused.
- The sampling function against fixed values written in the test, so a change to it fails.
- Web specs for each dialog, the blend preview's problems, and the disabled refusals. For the
  Columns step: it opens with the automatic map and its marks, picking a column, Null and a
  value in a cell, a disabled uncastable column, adding a column from "Not in the blend", the
  source column checkbox, and collapsing when everything matches.
- **Manual,** on the local stack: fork an imported GLUE config and filter it from Python, blend
  two datasets whose columns differ by percentage, mapping them in the Columns step, train a
  few steps on the blend, then try to delete a source.

### Docs

- A guide page, "Fork and blend datasets", under Hub, and the API reference for the new routes.
- `use-datasets.md` and `storage-and-datasets.md`: compaction of a fork copies; hand-made clones
  aren't supported; sources can't move while read.
- Mark this file `applied`, with a "How it was actually built" section.

### How it was actually built

Everything above except the guide pages and Part 0 on Ceph, which are still to do.

- **Migration `0022`**, not `0024`: `main` ends at `0021`. 022's branch also needs a number, so
  whichever lands second renumbers its file and `tests/app/test_migrations.py`'s lists.
- **Code.** The Lance side is `flock/hub/datasets/_blend.py`, and the API side is
  `app/api/src/core/forks.py` (`store.forks`), called from `core/datasets.py`'s rename, delete,
  restore and purge paths. The Python functions and CLI are `flock/hub/datasets/_forks.py` and
  `cli.py`.
- **One blend is two commits.** The first is an `Overwrite` with `initial_bases`. It also holds
  the fill and cast files, written with `LanceFileWriter` into the blend's own `data/`, so no
  `DataReplacement` is needed. The second is a `Delete` that writes the deletion vectors. The
  blend is tagged `blend` at the second version.
  A blend can't start at version 1: Lance 14 refuses deletion files in an `Overwrite` ("Overwrite
  fragments must be newly written … Use Delete"), and a detached commit can't create a dataset.
- **Percentages** within 0.1 points of 100% are scaled to 100%, so 66.6% and 33.3% work. Further
  off is "The percentages add up to X%, not 100%."
- **The blend dialog has three steps:** Sources (each source's dataset, version and amount, the
  total, and the destination's folder, name and seed), then Columns, then a confirmation that
  shows what will be written, with Create. Each step shows its own problems, and Next stays
  disabled until there are none.
- **Column map checks found by the end-to-end tests** (`tests/app/test_dataset_blends.py`):
  a non-nullable output can't take a nullable source column (Lance wrote it, then failed every
  read); a source giving only nulls is refused; a source column given to a second output is
  written as a fill file (`copy`), since one field id can only be one column (it read as null
  before); and `columns: []` is an empty map, not the automatic one.
- **Guides and reference:** `docs/source/hub/fork-datasets.md`, `blend-datasets.md` and
  `forks-and-blends.md`.
- **The About tab** shows where a fork or blend came from at the top of the right column, above
  the version's numbers.
- **`columns` in Python** has no `datasets.cast()`. A named column of another type is cast
  when that's safe, as in the UI. A different output type is set by passing the whole map.
- **Checked on a local stack** of Postgres, the API, the proxy and moto. A blend made from the
  UI reads back through the proxy with `lance.dataset`. It has 1,980 web rows and 1,020 code
  rows, with its renames, cast and value fill. A fork filtered from Python has 2,456 rows, and
  its source keeps all 7,000.
- **Data size** on a fork or blend counts the source files it reads, not storage it uses.

---

## 5. Open questions

1. **More casts.** D175 allows only casts that keep every value. Should narrowing casts
   (`int64` to `int32`) be allowed when the build checks every value fits?
2. **Reverting from the UI.** Lance's `restore()` makes an old version the latest again. Worth a
   "Make this the latest" in the Versions tab of forks and blends?
3. **Deleting a source anyway.** D177 refuses. Should admins be able to delete a source and its
   dependents together in one confirmed step?
4. **Very large sources.** If Part 0 shows keying takes too long on the API, should `blend`
   accept a sample computed by the CLI on a compute node, with the API only committing it?
5. **Collapsing chains.** A blend of a fork of a blend reads from every dataset in the chain.
   That's correct (D173 pins them all) but the "Read by" lists grow. No limit is proposed.
