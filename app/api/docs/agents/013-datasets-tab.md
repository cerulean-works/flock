# 013: a Datasets tab that shows the Lance datasets in each org's `flock-datasets` bucket

| | |
| --- | --- |
| Status | applied |
| Date | 2026-10-28 |
| Builds on | [009](009-s3-proxy.md), [010](010-storage-tab.md) |
| Amends | D53 (one bucket per org is managed by Flock, and `flock-` names are reserved), D64 (the overview marks the managed bucket) |
| Keeps | D54–D62, D63, D65–D69 |
| Result | 414 tests in `tests/app`, `tests/cli` and `tests/s3` pass on moto (25 new in `tests/app/test_datasets.py`, 1 in `tests/app/test_migrations.py`), and the 125 S3-backed ones also pass against the real Ceph; 440 in `app/web` (41 new); see [section 6](#6-how-it-was-actually-built) |

## 1. Goal

Datasets are Lance datasets that people write straight to S3, through the proxy, with Lance
itself or any other S3 client. Flock adds no upload path, no dataset table and no registration
step. What S3 holds is what the tab shows. This stage adds:

1. **A managed bucket.** Every org gets a bucket named `flock-datasets`, created and owned by
   Flock (D85). It's an ordinary bucket to S3 clients.
2. **A "Datasets" item** in the sidebar, for the current org (D86).
3. **A folder browser** over `flock-datasets`. Any folder that holds a Lance `_versions/` folder
   is a dataset (D87).
4. **A dataset page** with the README, rows, schema and version history, at any version (D88 to
   D92).
5. **Deleting, renaming and tagging** from the page (D93, D95, D96). Deleted datasets wait
   14 days in a trash that admins restore from in org settings (D93).

Writing a dataset is one Lance call:

```python
import lance
from flock.cli import s3

creds = s3.credentials("acme")
lance.write_dataset(
    table, creds.uri("flock-datasets", "eval", "mmlu"), storage_options=creds.storage_options()
)
```

**Out of scope:**
- loading datasets for training (the consumer, built separately);
- writing data from the UI: uploads, edits, restores, compaction and cleanup;
- search across datasets, and filtering rows by a query;
- formats other than Lance (Parquet, JSONL and the rest show in Storage as files);
- dataset cards with metadata (license, tags) beyond the README's text.

---

## 2. Design decisions

The numbering continues from 012.

### D85. The managed `flock-datasets` bucket

- **One per org.** Each org has a bucket named `flock-datasets`, upstream
  `{prefix}{org pk:05}-flock-datasets` as D53 names every bucket. It's a row in `s3_buckets`
  like the others, so the proxy, keys, permissions (D54) and the Storage tab all work on it
  unchanged.
- **Marked as managed.** `s3_buckets` gets a `managed` column, `'datasets'` for this bucket and
  null for the rest. There is at most one live managed bucket of each kind per org.
- **Created by Flock,** idempotently (an "ensure"), in three places:
  - when an org is created, if S3 is on;
  - when the API starts, for every org that lacks one, in the background (this covers existing
    orgs and turning S3 on later);
  - when the Datasets tab loads and finds none, so a failure at either earlier point heals
    itself.

  The event log records it as `created` with no user and `detail.via = "flock"`. If the upstream
  name already exists outside Flock, the ensure fails as `create_bucket` does (D53), it's logged,
  and the Datasets tab says the bucket couldn't be created.
- **An org that already has a bucket called `flock-datasets`** keeps it, and the ensure marks it
  managed instead of creating another. Its objects stay where they are.
- **Can't be deleted** by anyone through the API, the CLI or the UI (409, "flock-datasets is
  managed by Flock and can't be deleted"). It goes when its org is deleted, like every other
  bucket.
- **Reserved names.** New bucket names can't start with `flock-` ("Names starting with flock-
  are reserved."), so Flock can add managed buckets later without meeting a user's bucket.
  Existing buckets with such names keep working.
- **Why a bucket and not a table.** A table of datasets would have to be kept in step with S3 by
  every writer, which means writing through Flock. Detecting datasets from the objects
  themselves (D87) lets any S3 client write, and nothing can drift.

### D86. Where the Datasets tab lives

- **The sidebar** gets "Datasets" (lucide `database`) between Runs and Storage, shown when S3 is
  on and an org is in view, as Storage is (D63). Also in the collapsed rail.
- **Routes**, under `(app)`:

  | URL | Page |
  | --- | --- |
  | `/datasets/{org}` | the bucket's root (D87) |
  | `/datasets/{org}/{path…}` | a folder, or a dataset if the folder is one (D88) |

  Whether a path is a folder or a dataset is decided by the API (D87), so URLs need no marker.
- **`datasets` is reserved** in `RESERVED` (`src/lib/utils.ts`), and migration 0013 refuses to
  run if an org is named `datasets`, as 0012 does for `storage`.
- **Breadcrumbs** appear once you're inside a folder or dataset, as on the Storage tab: Datasets ›
  org › folders › dataset. Each one is a link. The root page has none.
- **Switching orgs** on a datasets page goes to that org's `/datasets/{org}`.
- **The Storage tab** lists `flock-datasets` with a "Managed by Flock" badge. Its menu has "Open
  in Datasets" and no Delete. Inside, it's browsable as files like any bucket, so the raw objects
  are always one click away. A dataset page links back with "Browse files".

### D87. Folders and datasets

A **dataset is a folder that holds a `_versions/` folder**, which every Lance dataset has. The
folder's path is the dataset's name, such as `eval/mmlu`. Folders nest to any depth.

- **Listing a folder** shows one level, like D65:
  - **datasets** first, with name, latest version and last updated;
  - then **folders**;
  - then **files**, with name, size and last modified. Each file links to its page in the
    Storage tab.
- **Detection.** The API lists the folder with delimiter `/`. For each subfolder it lists
  `{subfolder}_versions/` with `max-keys=1`, 16 at a time. Lance's default manifest naming (V2
  paths, `u64::MAX - version`) sorts newest first, so that one key gives the latest version
  and its time with no extra call. A dataset written with the older naming (`{version}.manifest`)
  still counts as a dataset, but the listing shows its version as unknown.
- **Paging.** 100 entries per page with "Load more", driven by S3's continuation token, so one
  page costs at most 101 LIST calls.
- **Inside a dataset** the browser doesn't descend. Its `data/`, `_versions/` and other internal
  folders are only visible in the Storage tab. A dataset inside another dataset's folder isn't
  looked for.
- **The bucket's root** is always a folder, never a dataset.
- **Empty states**, plain sentences:
  - root with nothing in it: "No datasets yet." and the write snippet from section 1, with the
    org filled in, plus a link to the account page for a long-lived key;
  - empty folder: "This folder is empty."

### D88. The dataset page

`/datasets/{org}/{path…}` for a dataset has a header and four tabs, kept in the URL as
`?tab=` like the run page.

- **Header:** the dataset's path, the version picker (D89), and these numbers for the version
  shown: rows, columns, fragments and data size (the sum of its data files, from the manifest).
  Actions: Copy S3 URI (`s3://flock-datasets/{path}`), Browse files, Rename (D95) and Delete
  (D93).
- **Tabs:**

  | Tab | Shows |
  | --- | --- |
  | README (default) | `README.md` from the dataset's folder (D92) |
  | Rows | the row viewer (D90) |
  | Schema | the columns and dataset details (D91) |
  | Versions | the version history (D89) |

### D89. Versions

Lance makes a new version for every write, so the history comes from Lance and nowhere else.

- **The version picker** lists versions newest first, with their tags. The URL keeps the choice
  as `?version=12` or `?version=v1.0` (a tag). With no `version`, the page shows the latest,
  resolved once when the page loads. Every request the page then makes carries that number, so
  a write that lands while you browse doesn't change what you're looking at. A "New version
  available" note appears on the next load.
- **The Versions tab** is a table, newest first, 50 per page:
  - version, time and tags;
  - the operation, from `read_transaction(v)` (Append, Overwrite, Delete, Update, Merge, Project,
    Rewrite, Restore and the rest), and rows added or removed where the operation says;
  - `transaction_properties`, if the writer set any, such as a message.

  Each row has "View", which opens the dataset at that version, and "Add tag" (D96).
- **Versions removed** by Lance's `cleanup_old_versions` are gone from the history, since only
  S3 is the record. That's the price of having no table.
- **Who wrote a version** isn't shown. Lance doesn't record it, and the proxy doesn't log
  writes. Writers who want it can put it in `transaction_properties`.

### D90. The row viewer

- **Paging** by offset, 50 rows a page, or 10 when the schema has binary columns (below). Lance
  skips whole fragments by their row counts, so a page deep in a large dataset is still fast.
  The URL keeps `offset`.
- **Columns:** all of them by default, with a picker that hides some. Hidden columns aren't read.
- **Cells** are rendered by the API from Arrow types, so the browser gets plain JSON:

  | Type | Shown as |
  | --- | --- |
  | null | a muted `null` |
  | numbers, booleans | as is, right-aligned |
  | strings | the first 500 characters. Longer cells show their length and open in full (up to 1 MiB) in a side sheet. |
  | timestamps, dates | ISO 8601, in the column's time zone if it has one |
  | binary | a thumbnail if the bytes are a PNG, JPEG, GIF or WebP image under 1 MiB, otherwise its size |
  | struct `{bytes, path}` | as an image, the way HF `datasets` stores its `Image` feature |
  | fixed-size list of floats | `float32[768]` with the first few values, for embeddings |
  | other lists and structs | JSON, cut at 500 characters, opening in the side sheet |
  | Lance blob columns | their size only. Lance reads blob columns as descriptions, so the bytes aren't fetched (to confirm in Step 3). |

- **HF features.** When the schema's metadata holds HF `datasets` features (the `huggingface`
  key, which HF writes when it saves Arrow), `ClassLabel` columns show their label names next to
  the integer.
- **Limits.** A page reads at most 64 MiB of cell data. Beyond that, the remaining binary cells
  show only their size. This keeps a page of large images from stalling the API.

### D91. The Schema tab

- **Columns** as a tree (structs and lists expand), with each field's Arrow type, whether it's
  nullable, and its field metadata.
- **Details:** Lance's data storage version, whether stable row IDs are on, fragment count, and
  indices (name, type and columns, from `describe_indices`).
- **Schema metadata,** as key and value. Long values are cut and expand.

### D92. The README

- **The card** is `README.md` in the dataset's folder, rendered with `marked` and DOMPurify, as
  the Storage tab's Markdown preview does (D67). The first 1 MiB is read.
- **It isn't versioned** with the data. Lance ignores files it didn't write, and
  `cleanup_old_versions` leaves it alone (checked with pylance 14). Every version shows the
  current README.
- **No README:** "This dataset has no README. Add a README.md to its folder to describe it." The
  README tab stays the default, so the empty state teaches the convention.

### D93. Deleting a dataset, and restoring it

Deleting a dataset moves it to a trash bucket. Admins see deleted datasets in the org settings
page, where they restore them or delete them for good. Anything still in the trash 14 days after
its delete is removed.

- **Who deletes:** members and admins, as D54 lets them delete objects. Delete in the header
  opens the type-the-name dialog (from 009), asking for the dataset's path. The dialog says "Org
  admins can restore it for 14 days."
- **The trash** is one bucket upstream, `{FLOCK_S3_BUCKET_PREFIX}flock-trash`, created at startup
  if missing. It has no row in `s3_buckets`, so the proxy never serves it and no S3 key can
  read or change it. Org buckets start with five digits, so the name can't clash with one. A
  deleted dataset goes to `{org pk:05}/{trash pk}/` in it.
- **A table records the deletes,** since only Flock deletes this way and nothing else needs to
  stay in step with it (D85's reason against a table doesn't apply):

  ```sql
  dataset_trash(pk, org_pk → orgs SET NULL, path, deleted_by → users SET NULL, deleted_at,
                moved_at NULL, latest_version, objects, bytes)
  ```

- **Moving it,** in the same order as a rename (D95), so there's never a broken dataset:
  1. Take the path's advisory lock, list the objects, and insert the row with `moved_at` null.
  2. Copy everything to the trash, `_versions/` last (cross-bucket `CopyObject`, server side).
  3. Delete the original, `_versions/` first, then set `moved_at`.

  A row left with `moved_at` null for over an hour (the request died) is settled by the sweep
  below. If the dataset is still at its path, the copy is deleted along with the row.
  Otherwise the move is finished and `moved_at` set.
- **Limits** are the rename's: 10,000 objects and 100 GiB. Above either, the API refuses before
  touching anything, and gives `aws s3 rm --recursive s3://flock-datasets/{path}/`, a permanent
  delete outside Flock.
- **In org settings,** admins get a "Deleted datasets" section: path, who deleted it and when,
  size, and the days left before it's removed. Each row has:
  - **Restore**, which copies it back to its path the same way, `_versions/` last, then deletes the
    trash copy and the row. If something now exists at the path, the dialog asks for another
    path, with D95's rules.
  - **Delete now**, a type-the-name dialog, which removes the trash copy and the row.

  Members don't see the section.
- **The sweep** runs in the API every 10 minutes. It removes entries deleted more than 14 days
  ago and settles stuck moves, claiming rows with `FOR UPDATE SKIP LOCKED` so any number of
  replicas can run it (012). It also removes trash prefixes with no row, older than a day.
- **Deleting an org** removes its trash entries at once, like its buckets.
- **The Storage tab still deletes for good.** Deleting a dataset's folder from Storage (D65) or
  with any S3 client skips the trash, since those are plain S3 deletes. The dataset page is the
  way to delete with a safety net.
- **The README goes too**, since it's in the folder.

### D94. How the API reads Lance

- **Dependency.** `pylance` joins the API's dependencies. It's already in the lock through the
  tests.
- **Reads go straight upstream** with the global key, as the Storage tab's listing does
  (D69). The proxy isn't involved, since the API checks membership itself. Opening
  `s3://{upstream bucket}/{path}` with storage options built from `FLOCK_S3_*`
  (`aws_virtual_hosted_style_request=false`, and `allow_http` for http endpoints).
- **Tags are written the same way,** with pylance straight to the upstream (D96). Renames and
  deletes are plain S3 calls on the upstream bucket.
- **In a thread.** Every Lance call runs in `asyncio.to_thread`, so the event loop never blocks.
- **A cache of opened datasets,** keyed by `(upstream bucket, path, version)`, 32 entries.
  A version never changes, so the cache can't go stale, and it holds on any number of replicas
  (012). Resolving "latest" always asks S3.
- **Routes,** under `orgs/{org}/datasets`, all checking membership like D69's:

  | Route | Does |
  | --- | --- |
  | `GET at?path&cursor` | a folder's listing (D87), or `{kind: "dataset", …}` when the path is a dataset |
  | `GET info?path&version` | the header numbers, schema and details (D88, D91), with the resolved version |
  | `GET rows?path&version&offset&limit&columns` | one page of rendered cells (D90) |
  | `GET cell?path&version&row&column` | one full cell for the side sheet, up to 1 MiB |
  | `GET versions?path&cursor` | the history (D89) |
  | `GET readme?path` | the README's text, or 404 |
  | `POST delete {path}` | moves it to the trash (D93) |
  | `GET trash` | deleted datasets, admins only (D93) |
  | `POST trash/{pk}/restore {to?}` | restores, to `to` if given, admins only |
  | `DELETE trash/{pk}` | deletes for good, admins only |
  | `POST rename {path, to}` | D95 |
  | `PUT tags/{tag} {path, version}` | creates the tag, or moves it to `version` (D96) |
  | `DELETE tags/{tag}?path` | D96 |

  A path that isn't a dataset gets 404 on all but `at`. Lance errors (a corrupt manifest, a
  missing data file) come back as 502 with Lance's message, and the page shows it in place of
  the tab's content.

### D95. Renaming a dataset

S3 has no rename, so renaming copies the dataset's objects to the new path and deletes the old
ones. Lance stores every path in its manifests relative to the dataset's folder, so a copied
dataset opens at its new path with all its versions, tags, deletions and indices. This was
checked with pylance 14.

- **Who:** members and admins, since a rename writes and deletes objects (D54).
- **How:** Rename in the header opens a dialog with the path to edit, such as moving
  `scratch/mmlu` to `eval/mmlu`. It's a move as well as a rename. The dialog says "Anything still
  writing to the old path will create a new dataset there." After the rename the page goes to the
  new URL.
- **The new path:**
  - segments of letters, digits, `.`, `-` and `_`, joined by `/`, at most 512 characters;
  - nothing may exist under it yet (409, "Something already exists at eval/mmlu.");
  - it can't be inside the dataset itself, or the bucket's root.

  Missing parent folders need nothing, since S3 folders are only prefixes.
- **Order,** chosen so that there is never a broken dataset, only at times two good ones or one
  dataset and some loose files:
  1. Take a PostgreSQL advisory lock on the old and the new path, so two renames or a rename and
     a delete of the same dataset wait for each other.
  2. Note the latest version, and list every object under the old path.
  3. Copy everything except `_versions/`, 16 at a time, then copy `_versions/` last. Until that
     last step the new path holds only loose files.
  4. Check the latest version again. If a write landed during the copy, delete the copy and
     refuse (409, "The dataset changed while it was being renamed. Try again.").
  5. Delete the old path, `_versions/` first.

  If the request dies during step 3, the new path has loose files that can be deleted, and the
  dataset is untouched at the old path. If it dies during step 5, the dataset is complete at the
  new path, and the old path has loose files.
- **Copies.** `CopyObject` within the bucket, server side, for objects up to 5 GiB. Larger ones
  (Lance's data files can be) use a multipart copy with `UploadPartCopy`. The upstream client
  in `s3_proxy/upstream.py` gains both.
- **Limits.** Ceph copies the bytes, so a rename takes time in proportion to the dataset's size.
  It runs in the request, up to 10,000 objects and 100 GiB. Above either, the API refuses before
  copying, with the size and an `aws s3 mv --recursive` command to run instead. The dialog shows
  a spinner and can't be closed while the request runs.
- **The README moves too,** since it's in the folder.

### D96. Tags

Lance tags name a version, and are stored in the dataset's `_refs/tags/`. They don't create a
version, and Lance's cleanup never removes a tagged version.

- **Who:** members and admins.
- **Where:** in the Versions tab. Each version's tags show as chips. "Add tag" on a row asks for a
  name. Each chip has a delete button with a confirmation.
- **Names** are ASCII letters, digits, `.`, `-` and `_`, starting with a letter or digit, at most
  100 characters. Lance itself accepts non-ASCII names, but pylance 14 then fails to list any of
  the dataset's tags, so Flock refuses them.
- **Moving a tag.** Adding a name that's already on another version asks "v1.0 is on version 3.
  Move it to version 7?" and moves it with `tags.update`.
- **The version picker** lists tags with their versions (D89), and `?version=` takes a tag.
  Moving or deleting a tag that a page has open doesn't change that page, since the page keeps
  the version number it resolved.
- **Two people tagging at once.** If the same name is created twice, the second gets 409 and the
  dialog offers to move it.

---

## 3. Plan

One PR, `feat/datasets-tab`, from main.

### Step 1. Migration

`0013_datasets.sql`:
- `s3_buckets.managed text CHECK (managed IN ('datasets'))`, with a unique index on
  `(org_pk, managed)` for live managed buckets;
- the `dataset_trash` table (D93);
- refuses to run if an org is named `datasets` (D86).

### Step 2. The managed bucket

In `core/s3.py`: `ensure_managed(org)`, called on org creation, at startup and from the
Datasets tab (D85). The reserved `flock-` prefix in `check_name` for new buckets. Refusing to
delete a managed bucket. `managed` in bucket responses.

### Step 3. Reading datasets

`core/datasets.py` with the detection, the Lance reads, the cache and the cell rendering of
D87 to D94, and `api/datasets.py` with the routes.

### Step 4. Writing datasets

Moving to the trash, restoring, deleting for good and the sweep (D93), renaming with
`CopyObject` and `UploadPartCopy` in the upstream client (D95), and tags (D96), in
`core/datasets.py` and the same router. The trash bucket's name is
`FLOCK_S3_TRASH_BUCKET` (default `{FLOCK_S3_BUCKET_PREFIX}flock-trash`), added to both compose
files and `docs/source/configuration.md`.

### Step 5. Web

- The sidebar item and `RESERVED`.
- The folder page and the dataset page with its four tabs and version picker, and the delete,
  rename and tag dialogs.
- The Storage overview's badge and menu for the managed bucket.
- The "Deleted datasets" section on the org settings page, for admins, with its restore and
  delete dialogs.
- Types, and specs for each page.

### Step 6. Tests

- `tests/app/test_datasets.py`, on moto, with datasets written by Lance through the proxy:
  - the managed bucket: created with the org, at startup for an org without one, adopted when
    the org already has a `flock-datasets` bucket, refused deletion, `flock-` names refused;
  - detection: datasets nested at several depths, folders, files, a dataset's internals not
    listed, the latest version from one LIST, paging;
  - info, rows and versions at the latest version, at an older one and at a tag, and a write
    after the page resolved its version not showing up;
  - cell rendering for each type in D90's table, HF `Image` and `ClassLabel` features, and the
    64 MiB page limit;
  - the README, present and missing;
  - deleting: the move to the trash with `_versions/` last in and first out, the limits, the
    trash invisible through the proxy, restoring to the old path and to a new one when the old
    is taken, deleting for good, members refused on the trash routes, the 14-day sweep (with a
    moved clock), a stuck move settled both ways, an org's deletion emptying its trash, and a
    non-member getting 404 everywhere;
  - renaming: versions, tags, deletions, indices and the README surviving the move, a nested
    target, a taken target, a target inside the dataset, a write during the copy giving 409 and
    leaving no copy, an object over 5 GiB copied in parts (a lowered threshold in tests), and the
    limits;
  - tags: creating, moving, deleting, the name rules, a duplicate giving 409, and loading at a
    moved tag.
- The same tests against Ceph with `FLOCK_TEST_S3_*`, as 009 and 010 do.
- Web specs: the folder listing and paging, the dataset tabs, the version picker and its URL,
  each cell kind, the delete, rename and tag dialogs, the Storage badge, and the Deleted
  datasets section with its days left.
- **Manual:** write a small HF dataset (with images and class labels) to Ceph with Lance through
  the proxy, append to it, tag and move a tag in the tab, look at it at each version, rename it
  into another folder, delete it, restore it from org settings, and delete it for good.

### Step 7. Docs and status

- An API docs page for the dataset routes.
- A "Datasets" section in the `flock.cli` S3 docs, showing how to write a Lance dataset into
  `flock-datasets`.
- Mark this file `applied`, with a "How it was actually built" section.

---

## 4. Known gaps after this stage

- **No authorship** in the history (D89).
- **Pruned versions disappear** from the history (D89).
- **No row filtering or search.** Lance can push a SQL filter down, so this is a small later
  addition.
- **The README isn't versioned** (D92).
- **Datasets above 10,000 objects or 100 GiB** can't be deleted or renamed from the UI. The CLI
  deletes them for good, with no trash (D93, D95).
- **Deletes outside the dataset page** (Storage, S3 clients) skip the trash (D93).
- **Writers aren't told about renames.** A job writing to the old path makes a new dataset
  there (D95).
- **No dataset sizes in the listing,** only in the dataset's header, for the same reason as D66.

## 5. Settled before review

1. **Folders of any depth.** Any folder holding `_versions/` is a dataset (D87).
2. **The managed bucket shows in Storage**, marked and undeletable (D86).
3. **A README.md** in the dataset's folder is its card (D92).
4. **Members can delete datasets** from the UI, into a trash that admins restore from or empty in
   org settings, removed after 14 days (D93).
5. **Members can rename and move datasets** (D95), and **create, move and delete tags** (D96).
   Everything else in the UI is read-only.

## 6. How it was actually built

The plan was followed, in six commits: the API, the migration and the settings (steps 1 to 4),
the tests (step 6), the web app (step 5), the docs (step 7), and two fixes found by trying the
tab in a browser. These are the places where the code differs from the decisions above, or adds
to them.

- **Blob columns (D90's open question).** Confirmed with pylance 14: a scan reads a blob column
  (`lance.blob.v2`, data storage 2.2, or `lance-encoding:blob` metadata) as a description,
  `{kind, position, size, blob_id, blob_uri}`, without fetching the bytes. The row viewer shows
  its `size`. `blob_handling="all_descriptions"` doesn't do the same for plain binary columns, so
  those are read.
- **The 64 MiB page limit** bounds what a page sends, not what it reads. A binary cell's size can
  only be known by reading it, so with binary columns the API reads the page one row at a time,
  sends images until 64 MiB of them are sent, and shows only the size of the rest. A page holds
  at most one row's bytes beyond what it sends.
- **The managed bucket.** Creating it on org creation is best effort: a failure is logged and the
  org is still created (startup and the Datasets tab try again). The startup pass runs in the
  background with the trash bucket's creation. `ensure_managed` takes a live bucket already named
  `flock-datasets` (adopting it, with its objects) before creating one. Its creator is null, and
  the Storage overview and `flock s3 buckets list` say Flock made it. The `flock-` check applies
  only to creating buckets. Every test org now has `flock-datasets`, so three existing tests that
  counted an org's buckets were updated.
- **The cache** is keyed by the manifest's ETag too, `(bucket, path, version, etag)`. A dataset
  deleted outside Flock and written again at the same path reuses version numbers, and without
  the ETag would be served from the old one's cache. Explicit versions are resolved with a HEAD
  of their manifest (either naming), tags by opening the latest version.
- **Times.** A version's time is the one in Lance's manifest (`get_version()`), since S3's time of
  the manifest object changes when a rename or restore copies it. The folder listing's "Updated"
  is still S3's time of the newest manifest (one LIST, as D87 asks), so it shows the time of a
  rename.
- **Data size** is the sum of the data files' `file_size_bytes` in the version's fragments.
  Deletion files and indices aren't counted.
- **Routes beyond D94's table.** `rows` takes `limit` (at most 100) and repeated `columns`.
  `versions` returns `latest`, and per version `rows` and `rows_delta` (only when the version just
  before still exists). `PUT tags/{tag}` takes `move`: without it, a tag on another version
  answers 409 with that `version`, which is how the dialog asks to move it. The same version again
  answers 200. Refusals of a rename or restore because something exists at the path carry
  `taken: true`, and refusals over the limits carry `command`.
- **Paths.** `at` answers 404 for a path inside a dataset (it checks the ancestors) and for a
  folder with nothing in it and no folder marker. A new path (D95) also can't hold `.`, `..` or a
  `_versions` segment, and can't be inside another dataset, which the browser would never show.
  Restoring to a new path follows the same rules.
- **Tag names** can't be only digits, since `?version=12` must mean version 12.
- **Cells.** Integers beyond 2^53 and non-finite floats come as strings. An HF `Image` struct with
  no bytes shows its path. The `cell` route returns a whole vector as JSON. `ClassLabel` labels
  come from the `huggingface` schema metadata.
- **The trash.** The row is inserted and committed first, then locked `FOR UPDATE` by the request
  for the rest of the move, so the sweep's `SKIP LOCKED` leaves a move that is still running
  alone. Restore and delete-now answer 409 while a move is unfinished. Settling a stuck move whose
  dataset is gone from its path deletes only the objects there that are older than the delete, so
  a new dataset written there since is kept. Deleting an org removes its rows in the org's
  transaction and empties `{org pk}/` of the trash in the background. The sweep's orphan pass
  covers an interrupted one.
- **Renames** compare the latest version's number and manifest ETag before and after the copy.
  Multipart copies use 512 MiB parts and are aborted if a part fails. `Upstream` gained
  `copy_object`, `copy_large`, `complete_upload`, `get_range` and `list_all`.
- **Web.** One route, `datasets/[org]/[...path]`, whose load reads only `?version=`. Tabs
  (`?tab=`) and row pages (`?offset=`) change the URL without running it again, and every read
  of the tabs carries the version it resolved. The browser reaches Flock through one relay,
  `/api/datasets/{org}/{op}`, limited to the routes above. It keeps Flock's extra fields
  (`taken`, `version`, `command`), so it uses a new `flockAnswer` in `server/flock.ts` instead of
  the Storage tab's `relay`. Deleting, renaming, tagging, restoring and deleting for good are
  `fetch` calls rather than form actions for the same reason. The org page reloads after the
  last two. Choosing in the version picker always puts the number or tag in the URL. "New version
  available" shows whenever the version shown is older than the latest, with a link to the
  latest by number. After a tag changes, the header reads the tags again but keeps its version.
  After a delete, the page goes to the nearest folder that still exists, since a folder that held
  only the dataset is gone with it. Tag chips sit under the version number rather than in a
  column of their own, so they show on narrow screens. The Storage tab's Markdown rendering moved
  to `Markdown.svelte`, shared with the README tab. Folder pages also have "Browse files".
- **Formatting.** Prettier reformatted `routes/device/+page.svelte`, which `pnpm lint` failed on
  before.
- **Docs.** A Datasets section in the S3 guide, an API page (`api/datasets`), and a Datasets
  section in `flock.cli.s3`. The doc-builder build and link check weren't run.

Checked by hand in a browser against moto with a local Postgres, API, proxy and web dev server:
a dataset with HF images, class labels, embeddings and long captions written with Lance through
the proxy and appended to, its README, rows, side sheet, schema and versions, the version picker
and a tag, moving a tag to another version, renaming it into another folder, deleting it, the
Deleted datasets section, restoring it, and the Storage overview's badge. Deleting for good and
the restore-to-another-path dialog were checked by the specs only, and none of this was repeated
against Ceph by hand.
