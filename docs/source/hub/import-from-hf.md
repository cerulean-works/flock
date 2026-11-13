# Import from Hugging Face

You can bring a model from the Hugging Face Hub into your organization's model registry. The import stores it in Flock's own format, so `load_from_hub` and the trainer handle it like any other Flock model. Hugging Face is only the source of the files.

## Import a model

[Sign in](../cli/sign-in) first. Then import a repository as a new version of a model in your organization:

```bash
flock models import Qwen/Qwen3-0.6B acme/qwen3-0.6b --primary
```

Or from Python:

```python
from flock.hub import models

version = models.import_from_hf("Qwen/Qwen3-0.6B", "acme/qwen3-0.6b", primary=True)
print(version)  # acme/qwen3-0.6b@1
```

The command prints the new version once it's committed. Load it as you would any version:

```python
model = models.load_from_hub("acme/qwen3-0.6b")  # an HFCompatibleModel
```

The model is an [`HFCompatibleModel`](../training/trainer#train-a-hugging-face-architecture), which wraps the `transformers` architecture so the trainer, `save_to_hub` and `load_from_hub` work with it.

| Option | Python | Does |
|---|---|---|
| `--revision REV` | `revision=` | Imports a branch, tag or commit instead of `main`. |
| `--tag TAG` | `tags=` | Tags the new version. Repeat the flag for more tags. |
| `--primary` | `primary=True` | Makes the new version primary once it's committed. A model's first version is primary anyway. |

Gated and private repositories use your usual Hugging Face token, from `HF_TOKEN` or `hf auth login`.

## Import from a local folder

`REPO` can also be a folder that already holds the repository's files. This helps when the compute node that talks to Flock has no internet access: download on a login node, then import on the compute node.

```bash
hf download Qwen/Qwen3-0.6B --local-dir /scratch/qwen3-0.6b
flock models import /scratch/qwen3-0.6b acme/qwen3-0.6b
```

The import reads the folder in place. A folder inside the Hugging Face cache (`models--Qwen--Qwen3-0.6B/snapshots/<commit>`) records its repository and commit, as a Hub import does. Any other folder records its own path as the repository, with no commit.

## What a version holds

An imported version has the same files that [`save_to_hub`](store-models#save-a-model) writes, plus some of the repository's files kept as they are:

| File | Holds |
|---|---|
| `config.json` | `HFCompatibleConfig`: the repository's `config.json` under `hf_config`, and `hf_repo` set to the repository so `get_hf_tokenizer()` finds the tokenizer. |
| `flock.json` | `HFCompatibleModel` and its config class, the weights' dtype, no run or step, and `source`: `{"hub": "huggingface", "repo": ..., "revision": ...}` with the commit it came from. |
| `model.safetensors`, or shards and `model.safetensors.index.json` | The weights, with the repository's sharding and dtype. |
| `README.md`, `generation_config.json`, tokenizer files | Copied unchanged when the repository has them. |

Each tensor name gets `model.` in front, because `HFCompatibleModel` holds the `transformers` model as `self.model`. For example `lm_head.weight` becomes `model.lm_head.weight`. The import rewrites only each safetensors file's header, where the names are, and copies the tensor bytes unchanged. Nothing is deserialized. A tied output projection that the repository stores once stays stored once.

## Checks and refusals

Before anything is uploaded, the import builds the model on the meta device and checks every tensor's name and shape against the weights. A mismatch stops the import, so a version that can't load never gets a number.

The import refuses:

- repositories with only pickled weights (`.bin`), since loading them can run code;
- repositories whose architecture isn't a causal language model that the installed `transformers` ships, such as models whose code lives in the repository (`trust_remote_code`);
- weights that `transformers` itself converts while loading, such as older checkpoints whose tensor names differ from the current architecture. These fail the name check.

## Run it again

Importing the same commit into the same model again returns the version already made. If an import stopped partway, for example because the job ended, running it again finishes the upload. Imports from a folder outside the Hugging Face cache have no commit, so each run makes a new version.

## Datasets

<!-- Placeholder: the dataset import half of this page is still to be written. -->

[Models API and CLI reference](models)
