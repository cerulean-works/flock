# 001: loading, writing and importing hub datasets

| | |
| --- | --- |
| Status | in progress (step 2 import) |
| Date | 2026-10-25 |
| Builds on | the app's [007](../../../../app/api/docs/agents/007-hub-datasets.md) (REST API, S3 proxy, credentials) |
| Keeps | n/a |
| Result | step 1: 242 tests in `tests/hub/` pass in about 30 s (5 on S3); step 2: 264 (22 import tests, about 7 s); see [section 6](#6-how-it-was-actually-built) |

## 1. Goal

`flock.hub` is the Python side of the hub: a thin wrapper over HF `datasets` for datasets kept
as Lance datasets in our S3.

1. **`load_dataset`** with a `version`, no splits, streaming by default, always returning a
   `flock.hub.Dataset`.
2. **`Dataset`**, which behaves like HF's datasets downstream (iteration, `map`, DataLoader
   sharding, resumable streaming) and adds random access, versions, tags and the card.
3. **Writes** that resume after a crash and commit exactly once.
4. **Hugging Face imports**: an automatic fallback for small datasets, and a staged
   `flock-hub import` for Dolma-scale ones.

**Out of scope:** models; Lance branches; uploading from the web app; aws-chunked uploads.

---

## 2. Design decisions

### D1. Lance versions are dataset versions, pinned at load

`load_dataset(repo_id, version=...)` takes an int or a tag name and passes it to
`lance.dataset(uri, version=...)`. `version=None` resolves to the latest version **once**, and
the `Dataset` keeps that int, so a long training run never sees a commit that lands later. All
reads (iteration, `take`, `card`) go through that pinned version.

### D2. Mirror HF's Lance builder; don't call it

`datasets>=4.5` has a packaged `lance` builder, but it can't open anything but the latest
version, and it gives one `storage_options` dict to both s3fs and Lance, whose key names
differ. So we open Lance ourselves and build HF datasets the way that builder does
(`datasets/packaged_modules/lance/lance.py`):

- one HF shard per Lance fragment (`IterableDataset.from_generator(gen, gen_kwargs={"fragment_ids":
  [...]})`, where a list in `gen_kwargs` becomes the shards);
- rows from `fragment.to_batches(columns=..., batch_size=..., filter=..., blob_handling="all_binary")`;
- `features` from the Arrow schema (`datasets.Features.from_arrow_schema`).

A drift test (section 4) checks these assumptions against the installed `datasets`.

### D3. `Dataset` wraps; it doesn't subclass

`flock.hub.Dataset` holds the pinned Lance dataset and an HF `IterableDataset` (streaming) or
`Dataset` (not streaming). HF operations (`map`, `filter`, `shuffle`, `batch`, `select_columns`,
`rename_column`, `skip`, `take`) are delegated and their result re-wrapped, so the return type
is always `flock.hub.Dataset`, with `repo_id` and `version` carried over. `to_hf()` returns the
HF object.

- `num_rows` and `features` are known in both modes (from the manifest).
- `ds[i]`, `ds[a:b]` and `ds.take_rows(indices)` use Lance `take` in both modes, before any HF
  transform. Once a transform has been applied, `__getitem__` on a streaming dataset raises.
- Not streaming, the HF `Dataset` is built with `Dataset.from_generator` into HF's cache,
  keyed by `(dataset uuid, version, columns, filter)`. Versions never change, so the cache
  never goes stale.

### D4. No splits

`load_dataset(..., split=...)` raises a `TypeError` saying the hub has no splits. Datasets
imported from HF with several splits have a `split` column; pass
`filter="split = 'test'"`, which Lance pushes down.

### D5. Credentials

`config.py` reads `FLOCK_API_URL` and `FLOCK_API_KEY` (falling back to `~/.config/flock/hub.json`)
and calls `POST /api/v1/hub/credentials` for temporary S3 credentials, refreshing them a few
minutes before they expire. The Lance `storage_options` are `aws_endpoint` (the proxy's public
URL), `aws_access_key_id`, `aws_secret_access_key`, `aws_session_token`, `aws_region`, and
`allow_http` when the endpoint is `http://`. A dataset's URI is `s3://datasets/<org>/<name>`.

For tests and for working straight against a bucket, `HubConfig` can instead be given a URI
root (`s3://<bucket>/datasets` or a local path) and fixed `storage_options`; then REST calls
that only exist on the server (creating datasets, mirrors) are skipped or raise.

### D6. Format pins

New datasets are written with `data_storage_version="2.2"`, `enable_stable_row_ids=True` and
the default V2 manifest names. `auto_cleanup_options` is never set. Fragments aim for
`max_rows_per_file=1_000_000` and `max_bytes_per_file=2 GiB`.

### D7. The card is a sidecar, versioned through table metadata

A README is stored at `<dataset>/_flock/cards/<sha256>.md`, and `ds.update_metadata({"flock.card":
sha})` points to it: a small `UpdateConfig` version, so the card at version 3 is the one v3
points to. The card itself isn't put in the metadata, which every manifest would copy.

### D8. Resumable writes that commit once

`DatasetWriter(repo_id, mode, staging_dir, upload_id=None)`:

1. **Start.** Records `read_version` (the base; `None` for a new dataset), `mode` and
   `upload_id` (a uuid if not given) in `staging_dir/upload.json`, or reloads them if the file
   exists.
2. **`write_shard(shard_id, data)`** calls `lance.fragment.write_fragments(...)` and saves the
   `FragmentMetadata.to_json()` list to `staging_dir/shards/<shard_id>.json`. A shard with a file
   is skipped on a rerun. A crash mid-shard leaves orphan data files, harmless, which a prune
   removes after Lance's 7-day threshold.
3. **`commit(message=None, properties=None)`** first checks the transactions after the base for
   `flock.upload_id == upload_id` (`ds.read_transaction`); if one exists, the earlier run
   crashed after committing, and it is recorded. Otherwise it commits once:
   `LanceDataset.commit(uri, Transaction(read_version=base, operation=Append(frags) |
   Overwrite(schema, frags), transaction_properties={"flock.upload_id": ..., "__lance_commit_message":
   message, **properties}))`. It then writes `staging_dir/committed.json` with the version.
4. Other writers' appends in between are rebased by Lance. An `Overwrite` or `Restore` in
   between raises `CommitConflict`, telling the caller to rerun with `mode="overwrite"`.

`push(data, repo_id, mode="append", message=None)` is the one-call form for in-memory data
(HF `Dataset`/`IterableDataset`, Arrow table or reader, pandas). `create_dataset(repo_id,
description="", visibility="private")` creates the dataset over REST. Edits on a `Dataset`
(`delete(where)`, `update(values, where)`, `add_columns(...)`, `tag(name)`, `untag(name)`,
`set_card(text)`) act on the latest version and return the new `Dataset` at the version they
made. Edits whose Lance call can't carry transaction properties (`delete`) set their message
with `PATCH .../versions/{v}`.

Transaction property keys: `__lance_commit_message`, `flock.upload_id`, `flock.source`
(e.g. `hf:cais/mmlu@<sha>`).

### D9. Hugging Face imports: one pipeline, two modes

`flock/hub/importer.py` has `import_hf_dataset(repo, config=None, revision=None, target=None, *,
workdir=None, num_proc=1, stage="all", mode="overwrite")`:

- **Revision.** `revision` (default `main`) is pinned to a commit sha with
  `HfApi.dataset_info`. The import's version is tagged `hf-<sha[:8]>` and carries
  `flock.source=hf:<repo>@<sha>`.
- **Splits** become a `split` string column when there is more than one.
- **Card.** The HF README, with a provenance header (repo, config, sha, date) on top.
- **Small mode** (the default, used by mirrors): `datasets.load_dataset(repo, config,
  revision=sha)` for all splits, then one `DatasetWriter` commit.
- **Staged mode** (`flock-hub import`, `flock/hub/cli.py`, for Dolma scale). `workdir/state.json`
  and a marker per unit make every stage resumable:
  1. **download**: resolve data files from the README `configs` globs or `--data-files`,
     download them with `hf_hub_download` in a thread pool. `--urls-file` (URL-list repos like
     `allenai/dolma`, whose loading script `datasets>=4` won't run) downloads with httpx and
     resumes with Range.
  2. **process**: a `ProcessPoolExecutor(num_proc)`, one unit per raw file (`.json.gz`,
     `.jsonl.zst`, …): `datasets.load_dataset("json", data_files=[f], features=..., split="train",
     cache_dir=workdir/hf_cache)` (HF's own `json` builder), then a batched `map` to the
     Dolma schema: `id`, `text` (large string), `source`, `added`, `created`, and `metadata`
     and `attributes` as JSON strings, since their shape varies across shards.
  3. **upload**: each processed unit is a `write_shard`, a few at a time, then one commit.

### D10. The fallback to Hugging Face

`load_dataset(repo_id, ..., config=None, hf_revision=None, auto_import=True)`:

1. the hub dataset `repo_id`;
2. if it isn't there and `auto_import`, the mirror `<mirror org>/<owner>--<name>[--<config>]`,
   at the version tagged `hf-<sha8>` when `hf_revision` is given, else its latest;
3. if there is no mirror (or no version for that sha), `POST /api/v1/hub/mirrors`, polling
   `GET /api/v1/hub/mirrors/{id}` with progress in the log until it finishes, then step 2.

A 422 (too large) is raised as an error that includes the server's `flock-hub import ...`
command. The import itself runs on the server (app D50), so training nodes need no HF access.

---

## 3. Layout

`flock/hub/`: `__init__.py` (exports `load_dataset`, `Dataset`, `push`, `create_dataset`,
`DatasetWriter`, `CommitConflict`), `config.py`, `client.py` (httpx REST), `dataset.py`,
`load.py`, `write.py`, `importer.py`, `cli.py` (console script `flock-hub`), `docs/`.

## 4. Tests (`tests/hub/`)

We copy HF's builder rather than call it, so the suite has to prove the copy matches and keeps
matching. Fixtures are local `file://` Lance datasets unless a test needs S3, which uses the
`s3_bucket` fixture (`tests/conftest.py`: moto in-process, or a real server).

- **Parity with HF's builder.** For each fixture, `datasets.load_dataset("lance",
  data_files=..., streaming=s)` (latest version) against ours, `s` in {True, False}: the same
  rows in the same order, equal `features`, shard count = fragment count, `columns`, batch size.
- **Types:** int and float widths, bool, string and large string, binary and large binary,
  blob v2 columns, struct, list, fixed-size list (embeddings), timestamps with and without a
  timezone, date, decimal, dictionary-encoded strings, JSON-string metadata, nulls in every
  type, empty strings, non-ASCII text, a cell over 16 MB.
- **Shapes:** schema only (no rows), one row, many tiny fragments, one large fragment,
  deletions (deleted rows never appear), after compaction, after adding and dropping columns
  (the schema at each version).
- **Versions:** int, tag and `None` resolve and pin; an append after loading is invisible to
  the loaded `Dataset` through iteration and `take`; restore; unknown versions and tags fail
  clearly; `card` per version.
- **Streaming:** `state_dict()` / `load_state_dict()` resumed after every shard boundary and
  mid-shard gives every row exactly once; `torch.utils.data.DataLoader` with 0–4 workers gives no
  duplicates or gaps; `split_dataset_by_node` with world sizes that do and don't divide the
  fragment count; `shuffle(seed, buffer_size)` is deterministic and a permutation; `skip`/`take`.
- **Return types:** every delegated operation returns `flock.hub.Dataset` in both modes, with
  `repo_id` and `version`; `to_hf()` gives the right HF class.
- **Random access:** `ds[i]`, negative indices, slices, `take_rows` out of order and with
  repeats, out-of-range errors; all agree with iteration order, in both modes.
- **Cache:** a second non-streaming load reads nothing from Lance; another version, column set
  or filter misses; a corrupted cache is rebuilt.
- **API surface:** `split=` is a `TypeError`; unknown keyword arguments are refused; `filter`
  matches filtering in Python.
- **Writer:** a crash injected after each shard, inside commit, and between commit and
  `committed.json` gives exactly one version with the right rows; two writers both land (on a
  local URI, where Lance's rename commit is atomic); an overwrite racing an append raises
  `CommitConflict`; transaction properties are recorded.
- **S3:** the above for one small dataset on `s3_bucket`; credentials refreshed during a long
  stream (with a mocked credentials endpoint and short expiry).
- **Drift guard:** import `datasets.packaged_modules.lance.lance` and check that it still makes
  one shard per fragment, reads with `blob_handling="all_binary"`, and infers the same features
  as we do.
- **Import** (phase 2): an mmlu-like local repo (several configs and splits) and a tiny Dolma
  repo (gz and zst shards, varying metadata), with `HfApi` mocked: the fallback order, a known
  sha loading with no import, staged resume after a kill in each stage, the `split` filter.

## 5. Plan

| Step | Branch | What |
| --- | --- | --- |
| 1 | `feat/hub-client` | D1–D8, `config`/`client`/`dataset`/`load`/`write`, the tests above except import. |
| 2 | `feat/hub-import` | D9, D10, `importer`, `cli`, import tests (with the app's D50). |
| 3 | `feat/hub-e2e` | Default to the proxy; end-to-end tests; user docs (`docs/source`). |

## 6. How it was actually built

### Step 1 (`feat/hub-client`)

- **An Arrow builder, not `from_generator`.** Reading goes through `LanceVersionBuilder`, a
  small `ArrowBasedBuilder` (as HF's Lance builder is) over one pinned version, in both modes:
  `as_streaming_dataset` when streaming, `download_and_prepare` into HF's cache otherwise.
  `Dataset.from_generator` turned every row into Python and back, and HF's writer then
  overflowed on `uint64` values over 2^63 (the types test caught it). The builder keeps
  everything D2 asks for: `gen_kwargs={"fragment_ids": [...]}` makes one shard per fragment,
  batches come from `fragment.to_batches(..., blob_handling="all_binary")` and are cast with
  `table_cast` as HF's `_cast_table` does, and `state_dict()` resumes as in HF. The cache key
  (D3) is the builder's config id: a hash of the dataset (server uuid, or URI in direct mode),
  version, columns, filter and the version's data file names, so a dataset recreated at the
  same local path misses.
- **Blob v2 columns.** HF's builder fails on them: `Features.from_arrow_schema` has no type for
  `lance.blob.v2`. We infer them as `large_binary`, which is what `all_binary` returns. The
  drift test checks that HF still fails, so we notice when it stops.
- **Magic bytes.** HF's builder turns a binary column whose first row starts with PNG, JPEG,
  WAV and similar magic bytes into `Image`, `Audio` or `Video`. We copy its table, and the drift
  test compares it with HF's.
- **Unknown `columns`** raise `ValueError`; HF's builder drops them silently.
- **Empty results.** HF can't prepare a split with no rows (a version without fragments, or a
  filter that matches nothing), so those give an empty in-memory `Dataset`, uncached.
- **Random access with a filter** scans the filter once per `Dataset` for the matching row ids
  and reads them with `_take_rows`/`take_blobs(ids=...)`. Unfiltered, it uses `take` and
  `take_blobs(indices=...)`. With a filter, a streaming `num_rows` is counted at load.
- **Credentials** are re-read at each fragment: the builder opens the pinned version per
  fragment through a small cache keyed by (URI, dataset id, version, storage options), so new
  credentials open it again. Lance's own refresh needs a `LanceNamespace`, which we don't use.
  `session_token` is optional in the answer, since the test RGW knows no tokens.
- **DataLoader.** Streaming `Dataset`s are also torch `IterableDataset`s (added at runtime, as
  HF does for its own), so a `DataLoader` lets HF split shards between workers. HF's
  `split_dataset_by_node` only takes HF objects, so `flock.hub.split_dataset_by_node` wraps it.
- **Names.** `load_dataset` takes the config as `hub_config=` (`config=` is the HF config of
  D10), and `flock.hub.config.set_default` sets one for the process. The pushed-down filter is
  `Dataset.lance_filter`, since `Dataset.filter` is HF's.
- **Conflicts.** Only append uploads raise `CommitConflict` for an `Overwrite` or `Restore`
  after their base (checked before committing, and Lance's own `CommitConflictError` is mapped
  too). An overwrite upload replaces whatever is there, as Lance allows. An upload that started
  before the dataset existed raises if another writer created it first.
- **Appends are cast** to the base version's column types (pandas makes `large_string`).
- **Edit messages.** `update`, `add_columns`, `restore` and `set_card` can't carry transaction
  properties either, so all edits set their message with `PATCH .../versions/{v}`. Direct mode
  drops it. Lance's `update` moves updated rows to the end of the iteration order.
- **Docs.** `doc-builder` couldn't be installed in the agent's sandbox, so the build wasn't
  run; a script checked the toctree, relative links and every autodoc target instead.

### Step 2: import (`feat/hub-import`)

- **One seam for repos.** `importer.source()` returns `HfSource` (`HfApi.dataset_info(...,
  files_metadata=True)`, `hf_hub_download`) or, when `FLOCK_HUB_IMPORT_FAKE_ROOT` is set (test
  only), `LocalSource`: folders `<root>/<owner>/<name>/` whose sha is a hash of their files. Small
  mode hands `datasets.load_dataset` the repo id and `revision=sha`, or the folder. The server
  uses the same `plan()` for its size check, so the subprocess and the API agree on files.
- **Which files a config has** (`plan`): the README YAML `configs` (`data_files` as a glob, a
  list, or `{split, path}` items, fsspec-style globs; a glob naming a folder takes its files),
  else every data-like file (`.parquet`, `.arrow`, `.json[l]`, `.ndjson`, `.csv`, `.tsv`, `.txt`,
  optionally `.gz`/`.zst`/`.bz2`/`.xz`), restricted to `<config>/` when a config is given.
  Several configs and none given or default raises, as HF does. Staged mode without YAML has one
  split; a `split` column is added only with several.
- **The card's header goes after the README's YAML front matter**, not above it, so tools that
  read the front matter still find it. The data commit and the card are two versions; the tag
  points to the card's. `_finish` is idempotent: a rerun finds the tag at or after the data
  version and stops, so a crash between commit and card leaves one data version.
- **Dolma processing.** `id`, `source`, `added` and `created` are read as `Json` too, not only
  `metadata`/`attributes`: Arrow's JSON reader otherwise parses ISO dates as timestamps and they
  come back reformatted. `_to_dolma` decodes them back to strings (a numeric `id` becomes its
  JSON text). Files with other top-level fields fail with a `ValueError` naming the file.
  Parquet files pass through unchanged in staged mode; other formats are refused.
- **Staged layout.** `workdir/state.json` (repo, config, sha, target, mode, units), `raw/<id>-<name>`
  + `raw/<id>.done`, `processed/<id>.parquet` + `processed/<id>.done`, `upload/` (the
  `DatasetWriter`'s staging dir) and `upload/imported.json` after card and tag. Each unit's HF
  cache (`hf_cache/<id>`) is deleted once its Parquet file is written. `num_proc=1` processes
  inline (no pool), which is what lets the crash tests monkeypatch `process_file`.
- **`--urls-file`** may be a local path or a path in the repo; the repo is still resolved to a
  sha for provenance.
- **Fallback (D10).** A `GET /api/v1/hub/mirrors?repo=&config=` was added to the server: it gives
  the mirror dataset's name (so the client never hardcodes `hf`) and the imports so far, so
  `hf_revision=None` or a known sha (full, or a prefix of at least 7) loads with no POST and no
  HF call. A branch name always POSTs (the server resolves it, and answers 200 at once when
  that commit is mirrored). A `version` passed with a fallback applies to the mirror. A repo
  missing on HF too gives `DatasetNotFound` ("nor on Hugging Face"), so callers that only
  expected a missing dataset still get that type; `test_versions.test_unknown_dataset` now
  passes `auto_import=False` so it doesn't ask the Hub. Direct mode imports locally into
  `<root>/hf/...`, resolving branch names client-side.
- **Entry point.** `python -m flock.hub.importer` reads the job from argv or stdin (the server
  uses stdin, since the job holds credentials). `card_uri` from the plan wasn't needed: the card
  sidecar goes beside the URI it is given, with the same storage options. The final line is
  `{"version": N, "versions": [data, card]}` so the server can record authorship of both.
  `flock.hub` exports `import_hf_dataset` lazily (module `__getattr__`), because importing it
  eagerly from the package makes `python -m flock.hub.importer` warn that the module was
  already imported.
- **Telemetry.** `datasets` HEADs an S3 URL to count downloads of packaged builders. With the
  fake root set, the importer turns that off (`HF_UPDATE_DOWNLOAD_COUNTS`), in worker processes
  too, so the tests make no network calls.
- **Dependencies.** `flock-hub` gained `click`, `pyyaml` and `zstandard` (HF's `.zst`
  extraction needs it), and the `flock-hub` script in both `pyproject.toml` files.
