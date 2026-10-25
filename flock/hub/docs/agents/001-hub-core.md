# 001: loading, writing and importing hub datasets

| | |
| --- | --- |
| Status | planned |
| Date | 2026-10-25 |
| Builds on | the app's [007](../../../../app/api/docs/agents/007-hub-datasets.md) (REST API, S3 proxy, credentials) |
| Keeps | n/a |
| Result | n/a |

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
`s3_bucket` fixture (`tests/conftest.py`, a Ceph RGW in Docker).

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

Filled in as each step lands.
