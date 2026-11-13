"""Find a Hugging Face model's files, in a local folder or downloaded from the Hub (D123)."""

import re
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from flock.hub.models._common import ModelError
from flock.hub.models.files import INDEX, SINGLE

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
