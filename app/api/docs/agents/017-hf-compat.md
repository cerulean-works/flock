# 017: importing from Hugging Face, and training Hugging Face architectures

| | |
| --- | --- |
| Status | applied |
| Date | 2026-11-14 |
| Builds on | [006](006-wandb-import.md), [013](013-datasets-tab.md), [014](014-model-registry.md) |
| Amends | D104 (`load_from_hub` builds `HFCompatibleModel` too), D105 (`flock models import`), D108 (an Import button on the Models tab) |
| Keeps | D85 to D103, D106, D107, D109 |
| Result | 1369 tests in `tests` pass (54 new test functions, more cases with parameters), and 572 in `app/web`. Imports of `HuggingFaceTB/SmolLM2-135M`, `stanfordnlp/imdb` and `cornell-movie-review-data/rotten_tomatoes` from the real Hub through a local stack match the originals; see [section 6](#6-how-it-was-actually-built) |

## 1. Goal

Flock only trains and stores models whose architecture is written in `flock.training`, and it
only shows datasets that someone wrote to `flock-datasets` with Lance. This stage opens both to
Hugging Face:

1. **Dataset imports.** A Hugging Face dataset becomes Lance datasets in `flock-datasets`, from
   the CLI, from Python or from the Datasets tab (D119 to D122).
2. **Model imports.** A Hugging Face model becomes a version in Flock's registry, in Flock's own
   format, from the CLI, from Python or from the Models tab (D123, D124).
3. **`HFCompatibleModel`.** A `BaseModel` in `flock.training` that wraps a `transformers`
   architecture, so the trainer, `save_to_hub` and `load_from_hub` handle it like `Olmo2`
   (D125 to D128).

Hugging Face is only the source of definitions and files. Everything is stored the Flock way:
Lance in `flock-datasets`, and `config.json`, `flock.json` and safetensors in `flock-models`.

```bash
# One step
flock datasets import HuggingFaceFW/fineweb-edu web/fineweb-edu --config sample-10BT

# Or two, when the download and the conversion run on different machines
flock datasets download HuggingFaceFW/fineweb-edu --config sample-10BT --to /scratch/fineweb-edu
flock datasets convert /scratch/fineweb-edu web/fineweb-edu --workers 64

flock models import Qwen/Qwen3-0.6B acme/qwen3-0.6b --primary
```

```python
from flock.hub import models
from flock.training.core.models import HFCompatibleModel

model = models.load_from_hub("acme/qwen3-0.6b")  # an HFCompatibleModel
fresh = HFCompatibleModel.from_hf("Qwen/Qwen3-0.6B")  # same architecture, fresh weights
```

**Out of scope:**
- exporting back to Hugging Face (writing a repo from a Flock version);
- models whose code lives in the repo (`trust_remote_code`), and pickled `.bin` weights;
- datasets that only exist as a loading script and have no Parquet conversion on the Hub;
- non-causal-LM heads (classification, seq2seq, vision);
- keeping an imported dataset in sync with later commits upstream (re-importing writes a new
  Lance version).

---

## 2. Design decisions

The numbering continues from 016.

### D119. A dataset import is two functions, download and convert

`flock.hub.datasets` is a new module. Its import is split so each half can run where it fits:

```python
from flock.hub import datasets

folder = datasets.download_from_hf(
    "HuggingFaceFW/fineweb-edu", "/scratch/fineweb-edu", config="sample-10BT"
)
written = datasets.convert(folder, "web/fineweb-edu", workers=64)
# or both at once
written = datasets.import_from_hf(
    "HuggingFaceFW/fineweb-edu", "web/fineweb-edu", config="sample-10BT"
)
```

- **`download_from_hf(repo, to, *, config=None, splits=None, revision=None)`** needs network
  access to the Hub and nothing else. It never touches Flock, so it runs on a login node with no
  Flock credentials. It uses `huggingface_hub.snapshot_download` with `allow_patterns` narrowed
  to the chosen config and splits, so it resumes and reuses the HF cache like any HF download.
- **`convert(folder, dest, *, org=None, workers=None, mode="create")`** needs Flock access and no
  Hub access, so it runs on a compute node with no internet. It writes to
  `s3://flock-datasets/{dest}/...` through the proxy.
- **`import_from_hf`** is the two calls in a row, downloading to a temporary folder that's
  deleted afterwards (or to `to=` if given, which is kept).
- **The folder is self-describing.** `download_from_hf` writes `flock-import.json` next to the
  files: the repo, the resolved commit sha, the config, and each split's files in order. `convert`
  reads only that, so it doesn't have to guess which file belongs to which split, and the
  dataset's history can say where it came from (D121).
- **What gets downloaded.** The repo's own data files if they are Parquet, JSON Lines, CSV or
  Arrow, matched to configs and splits by the `configs:` block in the dataset card, or by the
  Hub's default file-name rules when there isn't one. Otherwise the Hub's Parquet conversion at
  `refs/convert/parquet`. A dataset with neither is refused with a plain error.
- **Gated and private repos** use the usual `HF_TOKEN` or `hf auth login` token.

### D120. Conversion is multiprocess by default

Large datasets are the reason the steps split, so `convert` is built for them.

- **Workers default to `os.cpu_count()`** (or the CPUs the process is allowed, from
  `os.sched_getaffinity`, so it respects Slurm's allocation). `--workers 1` runs in-process,
  which tests and debugging use.
- **One Lance version per split, written by many processes.** Each worker takes source files
  from a queue, streams them as Arrow record batches, and writes Lance fragments straight to S3
  with `lance.fragment.write_fragments`. It returns the fragments' metadata to the parent, which
  commits them all at once with `LanceDataset.commit` (an `Overwrite` for a new dataset, an
  `Append` with `--mode append`). So a split appears in full or not at all, and a crash leaves
  only unreferenced files that Lance's cleanup removes.
- **Order is kept.** Fragments are committed in the order of the source files, and each file's
  rows in their order, so row `i` in Lance is row `i` upstream.
- **One schema per split.** Before any worker starts, the parent reads each file's schema
  (Parquet footers are cheap; JSON Lines and CSV read the first block) and unifies them with
  `pyarrow.unify_schemas`. Files that can't be unified stop the conversion before it writes
  anything, naming the first file that disagrees.
- **Memory stays flat.** Workers stream batches of at most 64 MB, and fragments roll over at
  Lance's default size, so a worker never holds a whole file.
- **Credentials.** The parent sets up the renewable S3 credential process with
  `flock.hub.s3.get_process` before the pool starts (as `get_dataset` does), and workers are
  spawned, not forked, so they inherit it without sharing sockets.
- **Progress** shows rows and bytes per split on a terminal, and one line per finished file
  otherwise (Slurm logs).

### D121. Where an imported dataset lands

- **One Lance dataset per split**, at `{dest}/{split}` when the import has one config, and at
  `{dest}/{config}/{split}` when it has several. `{dest}` itself is a folder in the Datasets tab
  (D87), so the splits sit together.
- **The dataset card** is copied to `{dest}/README.md`, and to each split's folder, so the README
  tab (D92) shows it.
- **Where it came from** goes in each version's Lance metadata (`flock.source`: repo, revision,
  config, split), and the dataset page shows it as "Imported from Hugging Face" with a link to
  the repo at that commit.
- **An existing dataset** at the destination refuses the import unless `--mode overwrite` (a new
  version replacing the rows) or `--mode append` is given. Old versions stay in Lance's history
  either way.

### D122. `flock datasets`

A new CLI group, next to `flock models`:

| Command | Does |
| --- | --- |
| `flock datasets import REPO DEST [--config]… [--split]… [--revision] [--workers] [--mode] [--org] [--keep DIR]` | download and convert in one step |
| `flock datasets download REPO --to DIR [--config]… [--split]… [--revision]` | download only |
| `flock datasets convert DIR DEST [--workers] [--mode] [--org]` | convert a downloaded folder |
| `flock datasets list [PATH] [--org]` | what the Datasets tab shows at a folder |

`--config` repeats (D129). It defaults to the repo's only config, or `default`, and asks for
one when there are several and none is called that. `--split` repeats and defaults to all.

### D123. A model import writes a normal Flock version

`flock.hub.models.import_from_hf(repo, name, *, revision=None, tags=(), primary=False)` and
`flock models import REPO NAME`:

- **The files** are the version's `config.json`, `flock.json`, safetensors shards and index, as
  `save_to_hub` writes them, plus the repo's tokenizer files, `generation_config.json` and
  `README.md` riding along (D104 allows other files).
- **`config.json`** is `HFCompatibleConfig` (D125) as a dict: the repo's own `config.json` under
  `hf_config`, and `hf_repo` set to the repo, so `get_hf_tokenizer()` finds the tokenizer.
- **`flock.json`** names `flock.training.core.models.hf:HFCompatibleModel` and its config class,
  with `run` and `step` null, and a new `source` field: `{"hub": "huggingface", "repo",
  "revision"}` with the resolved commit sha. The Models tab shows it on the Overview.
- **Weights are renamed, not loaded.** A Flock version's tensor names are the wrapper's
  state-dict names, which are the HF names with a `model.` prefix (D126). Safetensors keeps its
  names in a JSON header at the start of the file, followed by raw bytes, so the import rewrites
  the header and copies the bytes unchanged. Nothing is deserialized and neither torch nor
  transformers is needed, which is what lets the API run imports (D124). Sharding and dtype stay
  as upstream had them. The index's `weight_map` is renamed to match.
- **Refused:** repos with no safetensors weights (`.bin` only, since loading them unpickles),
  repos whose config needs `trust_remote_code`, and architectures that aren't a causal LM in the
  installed `transformers` (checked with `transformers`' model mapping on the client, and with a
  list kept in step with it on the API).
- **Checks after upload.** The CLI and Python then build the model on the meta device and check
  every tensor name and shape against the header, before committing. A mismatch aborts the
  upload, so a version that can't load never gets a number.
- **The two-step split** isn't needed for models, but `REPO` may be a local folder (for example
  one that `hf download` filled on a login node), and the import reads it in place.

### D124. Imports from the web app run on the API

- **Buttons.** The Datasets tab (at the root and in any folder) and the Models tab get an
  "Import from Hugging Face" button for members and admins.
- **The dataset dialog** asks for the repo, then lists its configs and splits from the Hub's
  dataset viewer API (`/splits`), with the total size of each, and asks for a destination folder
  (default: the repo's name under the current folder). The model dialog asks for the repo,
  revision (default `main`), model name (default the repo's name), tags and whether to make it
  primary. Both have an optional access token field for gated and private repos.
- **The job.** `POST /orgs/{org}/imports/hf` starts it and returns 202. It runs as a background
  task as the W&B import does (D37): a row in a new `hf_imports` table holds who, what, state,
  bytes and rows done, and the last error. The token lives only in the running task. States are
  `running`, `finished`, `failed`, `cancelled` and `interrupted`. `GET` lists an org's imports
  and `DELETE /orgs/{org}/imports/hf/{id}` cancels one.
- **Models stream through.** The API downloads each file from the Hub and uploads it to an upload
  session (D100) with S3 multipart, rewriting the safetensors header on the way (D123). It
  doesn't touch local disk and needs no torch. The commit is the same commit any client makes,
  so a failed import leaves an aborted session and nothing else.
- **Datasets convert in the API's process pool,** with the API's own S3 access, using the same
  `convert` code as the CLI with workers capped by `FLOCK_HF_IMPORT_WORKERS` (default 4) and
  files downloaded to a temporary folder that's deleted at the end.
- **A size limit.** `FLOCK_HF_IMPORT_MAX_BYTES` (default 50 GB) caps what the API imports. A
  bigger dataset or model is refused before it starts, and the dialog shows the `flock datasets
  import` or `flock models import` command to run instead, filled in.
- **Where progress shows.** A running import shows as a row at the top of the destination folder
  or the models list, with a progress bar and Cancel, and the result links to the new dataset or
  version. Failures show the error in plain sentences.

### D125. `HFCompatibleModel` and `HFCompatibleConfig`

In `flock/training/core/models/hf.py`:

```python
@dataclass(frozen=True, kw_only=True)
class HFCompatibleConfig:
    hf_config: dict[str, Any]  # a transformers config.json, as a dict
    max_position_embeddings: int | None = None  # defaults to hf_config's
    hf_repo: str | None = None  # tokenizer repository or folder
    initializer_range: float | None = None  # defaults to hf_config's, then 0.02
    init_seed: int = 42
    attn_implementation: str = "sdpa"


class HFCompatibleModel(BaseModel):
    def __init__(self, config: HFCompatibleConfig) -> None: ...

    @classmethod
    def from_hf(
        cls, repo: str, *, revision: str | None = None, pretrained: bool = False, **overrides: Any
    ) -> "HFCompatibleModel": ...
```

- **Only the definition comes from transformers.** The constructor turns `hf_config` into a
  `PretrainedConfig` with `AutoConfig.for_model`, then builds
  `AutoModelForCausalLM.from_config(...)` as `self.model`, inside `_initialize_weights()`. It
  never loads weights and never looks at the Hub (unless `get_hf_tokenizer()` is called, as for
  every `BaseModel`).
- **The config is a frozen dataclass of plain JSON,** so `write_model`, `build` and the trainer's
  checkpoint check (`config.json` equal to `asdict(config)`) work unchanged. Changing an
  architecture setting means a new `hf_config`, which `from_hf(repo, num_hidden_layers=8)`
  makes by overriding keys.
- **`from_hf`** reads the repo's `config.json` and sets `hf_repo` to the repo, so the tokenizer
  comes along. With `pretrained=True` it also loads the repo's safetensors into the wrapper,
  renaming as D123 does, for training straight from the Hub without importing first. Either way
  the model saves in Flock's format.
- **`trust_remote_code` is never passed.** A config naming an architecture transformers doesn't
  ship is refused in `__post_init__`, so `load_from_hub` never runs code from a model repo.
- **It lives in `flock.training`,** so `build`'s rule that architectures come from there (D104)
  still holds with no new exception.

### D126. Weights are the wrapper's state dict

- The wrapper holds the transformers model as `self.model`, so its state-dict names are the HF
  names with `model.` in front (`model.model.layers.0.self_attn.q_proj.weight`,
  `model.lm_head.weight`). Every Flock tool already uses the module's own state dict, so nothing
  special-cases HF names: saving, loading, resuming, the tied-weight aliases in `build`.
- **Tied embeddings.** Most HF repos store one copy of a tied `lm_head`. `build` already fills
  aliases from `_tied_weight_names`, and the import doesn't add the missing copy.
- **Why not strip the prefix** with a custom `state_dict`? It would make the wrapper the one
  `BaseModel` whose names don't match its modules, which the optimizer state, resume checks and
  `load_model(strict=False)` would each have to know about. A rename at the edge (import, and
  later export) is simpler.

### D127. The forward pass

- `BaseModel.forward` validates the batch and builds a 4-D causal mask before calling
  `_forward`. Transformers models build their own mask from a 2-D one and pick a fast attention
  path from it, so `HFCompatibleModel` overrides `forward`: it runs the same checks (split out of
  `BaseModel.forward` into `_check_batch`), computes the same left-padding-aware `position_ids`,
  and calls `self.model(input_ids=..., attention_mask=..., position_ids=..., use_cache=False)`,
  returning `.logits`.
- **Loss stays with the trainer.** Labels aren't passed, so HF's own loss isn't used and the
  trainer's alignment and `-100` handling apply as for `Olmo2`.
- **`attn_implementation`** defaults to `sdpa`. `flash_attention_2` works if installed. It's in
  the config, so a checkpoint resumes with the same choice.

### D128. Initialization

- **Fresh models** get Flock's seeded initialization: the HF model is built inside
  `_initialize_weights()`, so `init_seed` decides the weights and two runs with the same seed
  start equal. Flock's normal init covers `Linear` and `Embedding`, and norms keep the ones
  transformers gives them (all ones), which is what HF's own `_init_weights` does for these
  architectures too.
- **`initializer_range`** defaults to the HF config's, so a fresh Qwen starts at Qwen's scale.
- **Pretrained models** are built on the meta device and given their weights, as `build` does,
  so no time goes into an init that's thrown away.

### D129. Several configs in one import

Added after the first review, at the user's request.

- **`config` takes several.** `download_from_hf`, `import_from_hf` and `plan` take one name, a
  list of names, or a mapping from each name to its own splits (`None` for those `splits=`
  picks). `flock datasets download` and `flock datasets import` take `--config` repeated. One
  name still works as before, and none keeps the default-config rule.
- **One folder, one conversion.** The download puts every chosen config's files in one folder.
  `flock-import.json` (now version 2) has `configs` (every config of the repo) and `splits`, each
  chosen config's splits and their files in order. `convert` writes them all with one pool of
  workers, unifies schemas per split of each config, checks every destination before any worker
  starts, and commits each split of each config on its own, in file order, so each one still
  appears in full or not at all. `ConvertedSplit` has the `config`, and progress and the CLI's
  report name each split as `config/split`.
- **Where they land.** `{dest}/{config}/{split}` whenever the repo has several configs, as D121
  says. The dataset card goes to `{dest}/README.md` and to each split's folder. `flock.source`
  has each split's own config.
- **Splits across configs.** `--split` keeps a split in every chosen config that has it. A split
  that no chosen config has is refused. A config left with no splits is refused too. A config
  given its own splits (the mapping) gets exactly those, and a missing one is refused.
- **Parquet fallback.** When any chosen config's own files aren't readable, every chosen config
  comes from the Hub's Parquet conversion, as one config did. Named configs of a repo without a
  card configs block (a loading script) are looked up in the conversion rather than refused.
- **The web app.** The dataset dialog shows each config with a checkbox (checked, unchecked or
  partly checked) and its splits as checkboxes with sizes under it. The default config is checked
  with all its splits. Import stays disabled until a split is chosen. With one config, only its
  splits show.
- **The API.** The body takes `configs: {name: [splits] | null}` in place of `config` and
  `splits`, since a flat split list can't say "test of en, all of fr". `null` means every split
  of that config, and leaving `configs` out means the default config. The dialog sends `null` for
  a config with every split checked. The size limit sums every chosen config's files. The job's
  `configs` holds each config's imported splits, and its result lists every dataset with its
  config. The filled-in command repeats `--config`. Configs whose splits differ get one command
  each, one per line, all into the same `dest` (their targets don't overlap).
- **The preview's fallback.** Without the dataset viewer, the preview lists every config's splits
  and sizes from `catalog`, a `plan` of every config (splits the Hub only partly converted left
  out). It reads only the Hub's API, as `plan` does.

---

## 3. Plan

One branch, `feat/hf-compat`, and one PR at the end, in four parts.

### Part 1. `HFCompatibleModel`

- `flock/training/core/models/hf.py` (D125 to D128), registered as `"hf"` in `registry`.
- `BaseModel._check_batch` split out of `forward`.
- `transformers` is already a dependency of `flock-training`, no new ones.

### Part 2. Model imports

- The safetensors header rewrite, in `flock.hub.models` with no torch import, shared with
  `from_hf(pretrained=True)` and the API.
- `models.import_from_hf` and `flock models import` (D123).

### Part 3. Dataset imports

- `flock.hub.datasets`: `download_from_hf`, `convert`, `import_from_hf` (D119 to D121).
- `flock datasets` (D122).
- No new dependencies. `flock-hub` already has `pylance`, `pyarrow` and `datasets`, which
  brings `huggingface_hub`. `huggingface-hub` is listed directly since the module imports it.

### Part 4. API and web

- Migration `0018_hf_imports.sql`, `core/hf_imports.py`, the routes (D124).
- The Import buttons, dialogs and progress rows in the Datasets and Models tabs. The dialogs'
  copy follows the plain-sentence rule.

### Tests

- **`tests/training/test_hf_model.py`,** on CPU with tiny configs (Llama, Qwen3, OLMo 2 from
  transformers): built twice with one seed gives equal weights; the forward pass matches the
  transformers model's logits with left padding; a train step runs; `write_model` then `build`
  round-trips; resume from a checkpoint; a remote-code config refused.
- **`tests/s3/test_hf_import.py`,** on moto with a tiny model saved to a local folder: the
  header rewrite keeps every byte of every tensor; `load_from_hub` after an import gives the
  same logits as transformers; `.bin` only refused; a name mismatch aborts before commit.
- **`tests/s3/test_datasets_import.py`:** a local folder of Parquet, JSON Lines and CSV files
  converts to Lance with the same rows in the same order, with 1 and 4 workers; schema mismatch
  refused before writing; a worker killed mid-way leaves no new version; `--mode append` and
  `overwrite`; several configs land in `{dest}/{config}/{split}`.
- **`tests/app/test_hf_imports.py`:** start, progress, cancel, the size limit, interrupted at
  restart, and the token never written to the database. The Hub is mocked with `respx`.
- **Manual,** once: import `HuggingFaceTB/SmolLM2-135M` and a small dataset from the web app,
  train a few steps on them with the trainer, push a checkpoint and load it back.

### Docs and status

- Guide pages: `docs/source/hub/import-from-hf.md` and a section in the training guide on
  `HFCompatibleModel`. Reference entries via `[[autodoc]]`.
- Mark this file `applied`, with a "How it was actually built" section.

---

## 4. Known gaps after this stage

- **No export.** A trained `HFCompatibleModel` can't be pushed back to the Hugging Face Hub yet,
  though the rename in D126 makes that a small step.
- **Big imports don't run from the web app** past the size limit. The CLI is the way.
- **No dedup with the HF cache.** A dataset downloaded for conversion is a second copy on disk
  until the temporary folder is removed.
- **FSDP and tensor parallelism** for `HFCompatibleModel` are as missing as for `Olmo2`.

## 5. Settled in review

The user approved the doc as written, so each open question took the proposal:

1. **Web imports run on the API,** with a size cap (D124).
2. **One Lance dataset per split** (D121).
3. **Tensor names keep the `model.` prefix** (D126).
4. **`refactor/simplify-hub-api` merged into main** first, and this branch was rebased onto it.

## 6. How it was actually built

One PR on `feat/hf-compat`, in the four parts of section 3. Where the code differs from
section 2:

### 6.1. `HFCompatibleModel`

- **No `from_config`.** It consults `auto_map` and remote code, so the constructor looks the
  class up in `MODEL_FOR_CAUSAL_LM_MAPPING` and calls `_from_config` with
  `dtype=torch.get_default_dtype()`. Without the dtype, a repo whose config names `bfloat16`
  would build in bf16.
- **transformers' own init is seeded** with `init_seed` too, so parameters Flock's init doesn't
  cover are deterministic. Flock's `Linear` and `Embedding` init then runs as D128 says.
- **`load_state_dict` ties the embeddings again** after loading, since `assign=True` would
  leave a tied `lm_head` as its own parameter.
- **Non-persistent buffers** (rotary frequencies) are filled on the CPU when the model is built
  on the meta device, as transformers' `from_pretrained` does.
- **`from_hf` follows `layer_types`** when `num_hidden_layers` is overridden and every layer
  has one type. Mixed types need `layer_types` given too.

### 6.2. Model imports

- `flock/hub/models/headers.py` holds the torch-free header rewrite, and
  `flock/hub/models/hf.py` the import, with the config and provenance helpers the API shares.
- **The name and shape check runs before the upload,** not between upload and commit. The
  guarantee is the same, and nothing is uploaded for nothing.
- **Re-imports dedupe** on `huggingface:{repo}@{sha}`, so the same commit into the same model
  returns its version, and an interrupted import resumes from its journal.
- **Local folders:** one in the HF cache records its repo and commit. Any other records its
  path, with no revision, so each import of it makes a new version.

### 6.3. Dataset imports

- The CLI lives in `flock/hub/datasets/cli.py`.
- **`flock.source` is a Lance transaction property,** which the API's versions route already
  returns as `properties`. It also has `"hub": "huggingface"`, as models' `source` does.
- **A split the Hub only partly converted** (`partial-{split}`) is refused rather than imported
  short.
- **Scripts calling `convert` with more than one worker** need an `if __name__ == "__main__":`
  guard, since workers are spawned. The guide says so.

### 6.4. API and web

- **Interrupted imports** follow D81 rather than D37. Other replicas may be running an import,
  so a clean shutdown marks only its own, and rows whose heartbeat is over 60 seconds old are
  marked at startup and by the periodic cleanup.
- **Datasets are downloaded by the API with httpx,** after the same `plan()` the CLI uses, so a
  cancel is instant. The conversion runs in a spawned child in its own process group, which a
  cancel or shutdown kills with its workers.
- **The server's own HF credentials are never used.** Planning passes `token or False`.
- **sha256 metadata.** Files up to 64 MiB are one PUT with it. Bigger ones go up as multipart,
  then a server-side copy onto themselves sets it. The commit is the unchanged one.
- **No meta-device check in the API,** which has no torch. It refuses `.bin`-only repos,
  configs with `auto_map`, and `model_type`s outside `core/causal_lms.py`. That file is
  generated from transformers, and a test checks it still matches.
- **Only whoever started an import, or an admin, cancels it,** as with upload aborts.
- **The UI polls** every 2 seconds while an import runs. Finished rows stay for a day and can be
  hidden.
- **The default limit is 50 GiB,** so it reads "50 GB" in the UI's 1024-based sizes.
- `FLOCK_HF_ENDPOINT` and `FLOCK_HF_DATASETS_SERVER` point the API at another Hub, for tests and
  mirrors.

### 6.5. Known gaps found while building

- **JSON Lines types come from each file's first 16 MB.** A column that's empty there and filled
  later stops the conversion with an error naming the file. Nothing wrong is written.
- **Weights transformers converts while loading** (old names, fused MoE experts) fail the name
  check and are refused.
- **`get_dataset` always reads with `cerulean-labs`' credentials,** so training on another org's
  imported dataset needs `s3.get_process(org=...)` and `SafeLanceDataset` directly. This
  predates this stage.
