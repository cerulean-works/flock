"""Import a Hugging Face model as a version in Flock's registry (D123).

The weights are renamed, not loaded: each safetensors file is copied with its header rewritten
(see [`~hub.models.headers`]). Checking the names against the architecture needs torch and
transformers, imported only then.
"""

import dataclasses
import json
import re
import secrets
import shutil
import time
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from flock.hub.models._common import ModelError, ModelVersion, Progress, Ref
from flock.hub.models._upload_journal import STAGING_PREFIX, journals, uploads_root
from flock.hub.models.files import FORMAT, INDEX, SINGLE, flock_commit
from flock.hub.models.headers import (
    DTYPES,
    TensorInfo,
    hf_to_flock,
    read_header,
    rename_file,
    rename_header,
    rename_index,
    tensors,
)

if TYPE_CHECKING:
    from flock.training.core.models.hf import HFCompatibleConfig

CONFIG = "config.json"
RIDE_ALONG = (
    "README.md",
    "generation_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "tokenizer.model",
    "special_tokens_map.json",
    "added_tokens.json",
    "vocab.json",
    "vocab.txt",
    "merges.txt",
    "chat_template.jinja",
    "chat_template.json",
)
"""Files of a Hugging Face repository that a version keeps as they are, when present."""

PICKLED = (".bin", ".pt", ".pth", ".ckpt")
# A folder of the Hugging Face cache: .../models--{org}--{name}/snapshots/{commit}
CACHED = re.compile(r"models--(?P<org>[^/]+?)--(?P<name>[^/]+)/snapshots/(?P<sha>[0-9a-f]{40})$")
ARCHITECTURE = "flock.training.core.models.hf:HFCompatibleModel"
CONFIG_CLASS = "flock.training.core.models.hf:HFCompatibleConfig"


@dataclass(frozen=True)
class Snapshot:
    """A Hugging Face model's files on local disk.

    Args:
        folder (`Path`): Where the files are.
        repo (`str`): The Hub repository, or the folder when it isn't known.
        revision (`str`, *optional*): The commit the files are from, when known.
    """

    folder: Path
    repo: str
    revision: str | None


def choose_files(names: Collection[str], repo: str, *, weights: bool = True) -> list[str]:
    """Pick the files of a Hugging Face model repository that Flock uses.

    Args:
        names (`Collection[str]`): The repository's file names, relative to its root.
        repo (`str`): The repository, for error messages.
        weights (`bool`, *optional*, defaults to `True`): Include the safetensors weights.
            Sharded weights are given as the index and the `model*.safetensors` pattern, since
            the shards are only known once the index is read.

    Returns:
        `list[str]`: File names and patterns for `snapshot_download`'s `allow_patterns`.

    Raises:
        [`~hub.models.ModelError`]: If there's no `config.json`, or the weights aren't
            safetensors.
    """
    if CONFIG not in names:
        raise ModelError(f"{repo} has no config.json, so it isn't a transformers model")
    chosen = [CONFIG, *(name for name in RIDE_ALONG if name in names)]
    if not weights:
        return chosen
    if INDEX in names:
        return [*chosen, INDEX, "model*.safetensors"]
    if SINGLE in names:
        return [*chosen, SINGLE]
    if any(name.endswith(PICKLED) for name in names):
        raise ModelError(
            f"{repo} only has pickled weights (.bin), which can run code when loaded. "
            "Only safetensors weights are imported."
        )
    raise ModelError(f"{repo} has no safetensors weights ({SINGLE} or {INDEX})")


def snapshot(repo: str, revision: str | None = None, *, weights: bool = True) -> Snapshot:
    """Find a Hugging Face model's files on local disk, downloading them from the Hub if needed.

    Args:
        repo (`str`): A Hub repository, such as `Qwen/Qwen3-0.6B`, or a local folder, which is
            read in place.
        revision (`str`, *optional*): A branch, tag or commit of a Hub repository.
        weights (`bool`, *optional*, defaults to `True`): Also find the safetensors weights.

    Returns:
        [`Snapshot`]: The folder, the repository and the resolved commit. A folder of the Hugging
        Face cache gives its repository and commit; another folder gives itself and no commit.

    Raises:
        [`~hub.models.ModelError`]: If the files Flock needs aren't there.
    """
    folder = Path(repo).expanduser()
    if folder.is_dir():
        folder = folder.resolve()
        if revision is not None:
            raise ValueError(f"{repo} is a local folder, which has no revisions")
        names = [p.relative_to(folder).as_posix() for p in folder.iterdir() if p.is_file()]
        choose_files(names, repo, weights=weights)
        cached = CACHED.search(folder.as_posix())
        if cached is not None:
            return Snapshot(folder, f"{cached['org']}/{cached['name']}", cached["sha"])
        return Snapshot(folder, str(folder), None)

    from huggingface_hub import HfApi, snapshot_download

    info = HfApi().model_info(repo, revision=revision)
    names = [sibling.rfilename for sibling in info.siblings or ()]
    allow = choose_files(names, repo, weights=weights)
    downloaded = snapshot_download(repo, revision=info.sha, allow_patterns=allow)
    return Snapshot(Path(downloaded), repo, info.sha)


def weight_files(index: dict[str, Any] | None, where: str = INDEX) -> list[str]:
    """A version's safetensors files in order: the shards an index names, else `model.safetensors`.

    Args:
        index (`dict[str, Any]`, *optional*): The parsed `model.safetensors.index.json`, if the
            weights are sharded.
        where (`str`, *optional*): The index's name, for the error.

    Raises:
        [`~hub.models.ModelError`]: If the index has no `weight_map`.
    """
    if index is None:
        return [SINGLE]
    if not isinstance(index.get("weight_map"), dict):
        raise ModelError(f"{where} has no weight_map")
    return list(dict.fromkeys(index["weight_map"].values()))


def _weights(folder: Path) -> tuple[dict[str, Any] | None, list[str]]:
    """The folder's safetensors index, if sharded, and its weight files in order."""
    if (folder / INDEX).exists():
        index = json.loads((folder / INDEX).read_text())
        return index, weight_files(index, str(folder / INDEX))
    return None, [SINGLE]


def dedupe_key(repo: str, revision: str) -> str:
    """What makes importing one commit of a repository again return the version it made."""
    return f"huggingface:{repo}@{revision}"


def version_config(hf_config: dict[str, Any], repo: str) -> dict[str, Any]:
    """An imported version's `config.json`: [`training.HFCompatibleConfig`] with the repository's
    own config and its tokenizer repository, as `dataclasses.asdict` gives it.

    Args:
        hf_config (`dict[str, Any]`): The repository's `config.json`.
        repo (`str`): The repository, where the tokenizer comes from.
    """
    return {
        "hf_config": json.loads(json.dumps(hf_config)),
        "max_position_embeddings": None,
        "hf_repo": repo,
        "initializer_range": None,
        "init_seed": 42,
        "attn_implementation": "sdpa",
    }


def weight_dtype(found: Iterable[TensorInfo]) -> str | None:
    """The torch dtype of the first floating-point tensor, which `flock.json` records."""
    return next(
        (DTYPES.get(info.dtype) for info in found if info.dtype.startswith(("F", "BF"))), None
    )


def provenance(repo: str, revision: str | None, dtype: str | None) -> dict[str, Any]:
    """An imported version's `flock.json`, with where it came from under `source`.

    Args:
        repo (`str`): The repository.
        revision (`str`, *optional*): The commit the files are from, when known.
        dtype (`str`, *optional*): The weights' torch dtype, from [`weight_dtype`].
    """
    return {
        "format": FORMAT,
        "architecture": ARCHITECTURE,
        "config_class": CONFIG_CLASS,
        "dtype": dtype,
        "flock_commit": flock_commit(),
        "run": None,
        "step": None,
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": {"hub": "huggingface", "repo": repo, "revision": revision},
    }


def dump(data: dict[str, Any]) -> bytes:
    """JSON as an imported version's files hold it: indented, with a final newline."""
    return (json.dumps(data, indent=2) + "\n").encode()


def _renamed_tensors(folder: Path, files: Sequence[str]) -> dict[str, TensorInfo]:
    """Every tensor in `files`, under its Flock name, from the headers alone."""
    found: dict[str, TensorInfo] = {}
    for name in files:
        path = folder / name
        if not path.is_file():
            raise ModelError(f"{name} is missing from {folder}")
        with path.open("rb") as file:
            header = rename_header(read_header(file), hf_to_flock)
        found.update(tensors(header[8:]))
    return found


def check_weights(config: HFCompatibleConfig, found: dict[str, TensorInfo]) -> None:
    """Check renamed tensors against the architecture, built on the meta device.

    Args:
        config ([`training.HFCompatibleConfig`]): The architecture.
        found (`dict[str, TensorInfo]`): The tensors the version will hold, by Flock name.

    Raises:
        [`~hub.models.ModelError`]: If a tensor is missing, unexpected or the wrong shape. A
            tied weight stored once isn't missing.
    """
    import torch

    from flock.hub.models.files import _tied_weight_names
    from flock.training.core.models.hf import HFCompatibleModel

    with torch.device("meta"):
        model = HFCompatibleModel(config)
    expected = {name: tuple(tensor.shape) for name, tensor in model.state_dict().items()}
    aliases = _tied_weight_names(model)
    problems = []
    missing = [n for n in expected if n not in found and aliases.get(n) not in found]
    if missing:
        problems.append(f"missing {_names(missing)}")
    unexpected = [n for n in found if n not in expected]
    if unexpected:
        problems.append(f"unexpected {_names(unexpected)}")
    shapes = [
        f"{n} is {list(info.shape)}, not {list(expected[n])}"
        for n, info in found.items()
        if n in expected and info.shape != expected[n]
    ]
    if shapes:
        problems.append("wrong shapes: " + "; ".join(shapes[:5]))
    if problems:
        raise ModelError(
            "The weights don't match the architecture in config.json, so nothing was "
            f"uploaded. {'. '.join(problems)}."
        )


def _names(names: Sequence[str]) -> str:
    shown = ", ".join(names[:5])
    return f"{shown} and {len(names) - 5} more" if len(names) > 5 else shown


def stage(found: Snapshot, staging: Path) -> None:
    """Write a version's files for a Hugging Face model, checked against its architecture.

    Args:
        found ([`Snapshot`]): The model's files on local disk.
        staging (`Path`): A new folder to write the version's files to.

    Raises:
        [`~hub.models.ModelError`]: If the weights are missing or don't match the architecture.
        `ValueError`: If the architecture isn't a causal LM `transformers` ships.
    """
    from flock.training.core.models.hf import HFCompatibleConfig

    folder = found.folder
    hf_config = json.loads((folder / CONFIG).read_text())
    fields = version_config(hf_config, found.repo)
    config = HFCompatibleConfig(**fields)
    index, files = _weights(folder)
    renamed = _renamed_tensors(folder, files)
    check_weights(config, renamed)

    staging.mkdir(parents=True)
    for name in files:
        (staging / name).parent.mkdir(parents=True, exist_ok=True)
        rename_file(folder / name, staging / name, hf_to_flock)
    if index is not None:
        (staging / INDEX).write_bytes(dump(rename_index(index, hf_to_flock)))
    for name in RIDE_ALONG:
        if (folder / name).is_file():
            shutil.copyfile(folder / name, staging / name)

    (staging / CONFIG).write_bytes(dump(dataclasses.asdict(config)))
    dtype = weight_dtype(renamed.values())
    (staging / "flock.json").write_bytes(dump(provenance(found.repo, found.revision, dtype)))


def import_from_hf(
    repo: str,
    name: str,
    *,
    revision: str | None = None,
    tags: Sequence[str] = (),
    primary: bool = False,
) -> ModelVersion:
    """
    Imports a Hugging Face model as a new version of `name`, creating the model if it's new.

    The version is in Flock's own format, as [`~hub.models.save_to_hub`] writes it, so
    [`~hub.models.load_from_hub`] returns an `HFCompatibleModel`. Its `config.json` holds the
    repository's under `hf_config`, its `flock.json` says where it came from under `source`, and
    the tokenizer files, `generation_config.json` and `README.md` come along. The weights keep
    their shards and dtype. Each tensor is renamed with `model.` in front, by rewriting the
    safetensors headers, and nothing is deserialized.

    Before anything is uploaded, the model is built on the meta device and every tensor name
    and shape is checked, so a version that can't load is never made. Importing the same
    commit again into the same model returns the version already made, and finishes an import
    that stopped partway.

    Args:
        repo (`str`):
            A Hub repository, such as `Qwen/Qwen3-0.6B`, or a local folder holding one (for
            example one `hf download` filled), which is read in place.
        name (`str`):
            The model, as `org/name`.
        revision (`str`, *optional*):
            A branch, tag or commit of a Hub repository. Defaults to `main`.
        tags (`Sequence[str]`, *optional*):
            Tags for the new version.
        primary (`bool`, *optional*, defaults to `False`):
            Make the new version primary once it's committed. A model's first version is
            primary anyway.

    Returns:
        [`~hub.models.ModelVersion`]: the new version.

    Raises:
        [`~hub.models.ModelError`]: when the repository has no safetensors weights (only
            pickled `.bin` files, say), the weights don't match the architecture, or Flock
            refuses the upload.
        `ValueError`: when the architecture isn't a causal LM that the installed transformers
            ships, such as one whose code lives in its repository.

    Example:

    ```python
    from flock.hub import models

    version = models.import_from_hf("Qwen/Qwen3-0.6B", "acme/qwen3-0.6b", primary=True)
    model = models.load_from_hub("acme/qwen3-0.6b")
    ```
    """
    return import_model(repo, name, revision=revision, tags=tags, primary=primary)


def import_model(
    repo: str,
    name: str,
    *,
    revision: str | None = None,
    tags: Sequence[str] = (),
    primary: bool = False,
    progress: Progress | None = None,
) -> ModelVersion:
    """[`import_from_hf`], reporting bytes sent to `progress`, as the CLI does."""
    from flock.hub.models.upload import push, resume_journal

    ref = Ref.parse(name, version=False)
    found = snapshot(repo, revision)
    key = dedupe_key(found.repo, found.revision) if found.revision else None
    if key is not None:
        for journal in journals(ref.model):
            if journal.staged and journal.options.get("dedupe_key") == key:
                return resume_journal(journal, progress)
    options = {
        "metadata": {},
        "tags": list(tags),
        "primary": primary,
        "run": None,
        "step": None,
        "dedupe_key": key,
    }
    staging = uploads_root() / f"{STAGING_PREFIX}{secrets.token_hex(8)}"
    try:
        stage(found, staging)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return push(staging, ref, options, staged=True, progress=progress)
