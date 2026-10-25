# Importing from Hugging Face

A Hugging Face dataset becomes a hub dataset in one of two ways: the server mirrors small ones when `load_dataset` asks for them, and you import large ones yourself with `flock-hub import`. Either way the import is pinned to one commit of the Hugging Face repo, and makes one version of the hub dataset.

## What an import makes

Whatever the route, an import of `cais/mmlu` at commit `c30699e…` gives:

| | |
|---|---|
| Rows | Every split of the config in one version. With several splits, a `split` column holds each row's split, so you load one with `filter="split = 'test'"`. |
| Transaction property | `flock.source=hf:cais/mmlu@c30699e…`, beside the version's message |
| Card | The repo's README, with a line naming the repo, config, commit and date after its YAML front matter |
| Tag | `hf-c30699e8`: `hf-` and the first 8 characters of the commit |

The data and the card are two versions: the card is set right after the data is committed. The tag points to the second.

## Mirrors

When `load_dataset` can't find a hub dataset, it takes the name as a Hugging Face dataset and loads its mirror instead. Mirrors live in the org `hf` (the server's `FLOCK_HUB_MIRROR_ORG`), named `<owner>--<name>`, plus `--<config>` when you give a config:

```python
from flock.hub import load_dataset

mmlu = load_dataset("cais/mmlu", config="all")  # hf/cais--mmlu--all, the latest import
pinned = load_dataset(
    "cais/mmlu", config="all", hf_revision="c30699e8356da336a370243923dbaf21066bb9fe"
)
test = load_dataset("cais/mmlu", config="all", filter="split = 'test'")
```

If there is no mirror yet, or none of that `hf_revision`, the server imports it first, and `load_dataset` logs the import's progress while it waits. The import runs on the server, so training nodes need no Hugging Face access or token. Mirrors are `internal`: anyone signed in can read them. [Mirroring Hugging Face datasets](https://docs.cerulean.works/flock/latest/guide/datasets/#hugging-face-mirrors) covers the server side.

The server only mirrors datasets up to `FLOCK_HUB_AUTO_IMPORT_MAX_BYTES` of data files (5 GB unless configured). For a larger one, `load_dataset` raises [`~hub.load.ImportTooLarge`], whose message holds the `flock-hub import` command to run instead.

Loading by commit sha (all 40 characters, or a prefix of 7 or more) finds an existing mirror without the server asking Hugging Face anything. A branch name such as `main` always does, to learn which commit it points to.

In direct mode (a [`~hub.config.HubConfig`] with `root`), there is no server: `load_dataset` imports the dataset itself, into `<root>/hf/...`.

## Small imports from Python

[`~hub.importer.import_hf_dataset`] runs an import where you call it. Without a work directory, it loads every split into memory with `datasets.load_dataset` and writes them in one commit, as mirrors do:

```python
from flock.hub import import_hf_dataset

ds = import_hf_dataset("allenai/ai2_arc", config="ARC-Easy", target="flock/arc-easy")
print(ds.version, ds.tags)
```

`target` defaults to the mirror name, such as `hf/allenai--ai2_arc--ARC-Easy`. On the server, the target is created if it doesn't exist, and you need write access to it: to write a mirror yourself, you must be a member of the mirror org.

## Staged imports

For corpora too large for memory, such as Dolma, give a work directory. The import then runs in three stages, each of which resumes after a crash:

1. **download**: the data files of the config (from the README's `configs`), or the files matching `--data-files` globs, are downloaded into `<workdir>/raw/`, eight at a time.
2. **process**: each file is converted on its own, by `--num-proc` processes. JSON Lines files (`.json`, `.jsonl`, compressed with gzip or zstd and others) are read with HF's `json` builder and written in the Dolma schema below. Parquet files are kept as they are.
3. **upload**: each converted file is written as one shard of a [`~hub.write.DatasetWriter`], four at a time, and the shards are committed as one version.

```bash
uv run flock-hub import \
  --repo allenai/dolma-sample \
  --target flock/dolma-sample \
  --workdir /scratch/dolma-sample \
  --num-proc 16
```

The Dolma schema:

| Column | Type | Holds |
|---|---|---|
| `id` | `string` | The document id (a numeric id is written as its JSON text) |
| `text` | `large_string` | The document |
| `source` | `string` | Where it came from |
| `added`, `created` | `string` | The dates as written in the file, unparsed |
| `metadata`, `attributes` | `string` | JSON text, since their shape varies from file to file |

Other top-level fields are refused, as HF's `json` builder refuses fields its features don't name.

### Resuming

`<workdir>/state.json` records what is imported: the repo, the config, the commit, the target and the list of files. Each stage marks every file it finishes (`raw/<id>.done`, `processed/<id>.done`), and the upload keeps the writer's own state in `upload/`. Rerun the same command after a crash and every stage skips what is done:

- A file downloaded or converted before the crash isn't downloaded or converted again.
- A commit that landed before the crash is found by its upload id and not made again.
- A rerun after the import finished changes nothing.

The commit is pinned at the first run: a rerun keeps importing the same commit even if `main` moved. A work directory holds one import, so asking it for another repo, config, target or commit raises an error.

`--stage` runs one stage alone, for example to download on a machine with a fast link and convert on one with many cores. A stage refuses to start until the one before it is done for every file.

### URL lists

Some repos, such as `allenai/dolma`, list their files as URLs instead of holding them, for a loading script that `datasets` 4 and later no longer runs. Give the list with `--urls-file`, a local file or a path in the repo, with one URL per line (lines starting with `#` are skipped):

```bash
uv run flock-hub import \
  --repo allenai/dolma \
  --urls-file urls/v1_7.txt \
  --target flock/dolma-v1_7 \
  --workdir /scratch/dolma-v1_7 \
  --num-proc 64
```

Downloads go to `<file>.part` first. A download cut short resumes from where it stopped with an HTTP Range request.

## Appending

`mode="append"` (`--mode append`) adds the import's rows to the target's latest version instead of replacing them. Each import keeps its own tag, so several imports into one dataset stay distinguishable.

The [command line reference](cli) lists every flag.
