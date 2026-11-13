"""Downloading a Hugging Face dataset's data files to a folder that [`~hub.datasets.convert`]
reads (D119). Only the Hub is reached: nothing here needs Flock."""

import posixpath
import re
from collections.abc import Callable, Collection, Iterable
from functools import cache
from pathlib import Path
from typing import Any

from flock.hub.datasets._common import MANIFEST, DataFile, DatasetError, Manifest, file_format

PARQUET_REF = "refs/convert/parquet"
"""The branch where the Hub keeps its Parquet conversion of a dataset."""

CARD = "README.md"

type Splits = dict[str, list[str]]


@cache
def _pattern(pattern: str) -> re.Pattern[str]:
    from fsspec.utils import glob_translate

    return re.compile(glob_translate(pattern))


def match(files: Iterable[str], pattern: str) -> list[str]:
    """
    The repo files that `pattern` matches, as the `datasets` library resolves data files: `*`
    within a folder, `**` across folders, and hidden files, folders starting with `__` and the
    files `datasets` ignores (such as `README.md`) left out unless the pattern names them.

    Args:
        files (`Iterable[str]`):
            The repo's files, relative to its root.
        pattern (`str`):
            A glob relative to the repo's root.

    Returns:
        `list[str]`: the matching files, sorted.
    """
    from datasets.data_files import (
        FILES_TO_IGNORE,
        _is_inside_unrequested_special_dir,
        _is_unrequested_hidden_file_or_is_inside_unrequested_hidden_dir,
    )

    compiled = _pattern(pattern)
    ignored = set(FILES_TO_IGNORE) - {posixpath.basename(pattern)}
    return sorted(
        path
        for path in files
        if compiled.match(path)
        and posixpath.basename(path) not in ignored
        and not _is_inside_unrequested_special_dir(path, pattern)
        and not _is_unrequested_hidden_file_or_is_inside_unrequested_hidden_dir(path, pattern)
    )


def _resolve(files: Collection[str], patterns: Iterable[str], base: str = "") -> list[str]:
    """Every file `patterns` match, in pattern order then name order, each once."""
    found: dict[str, None] = {}
    for pattern in patterns:
        if "://" in pattern:
            raise DatasetError(f"The dataset card points at files outside the repo ({pattern})")
        for path in match(files, posixpath.join(base, pattern) if base else pattern):
            found.setdefault(path, None)
    return list(found)


def default_splits(files: Collection[str], base: str = "") -> Splits:
    """
    Each split's files when the dataset card doesn't say, by the Hub's default rules
    (`data/{split}-00000-of-00001.parquet`, then `train`, `validation` and `test` in folder or
    file names, then every file as `train`).

    Args:
        files (`Collection[str]`):
            The repo's files.
        base (`str`, *optional*):
            Only look inside this folder (a config's `data_dir`).

    Returns:
        `dict[str, list[str]]`: each split's files. Empty when nothing matches.
    """
    from datasets.data_files import _get_data_files_patterns

    def resolver(pattern: str) -> list[str]:
        found = _resolve(files, [pattern], base)
        if not found:
            raise FileNotFoundError(pattern)
        return found

    try:
        patterns = _get_data_files_patterns(resolver)
    except FileNotFoundError:
        return {}
    return {str(split): _resolve(files, found, base) for split, found in patterns.items()}


def card_configs(card_data: Any) -> dict[str, dict[str, Any]]:
    """The `configs:` block of a dataset card, by config name (empty when there's none)."""
    if not card_data:
        return {}
    from datasets.utils.metadata import MetadataConfigs

    try:
        return dict(MetadataConfigs.from_dataset_card_data(card_data))
    except ValueError as error:
        raise DatasetError(f"The dataset card's configs aren't valid: {error}") from None


def repo_splits(files: Collection[str], config: dict[str, Any]) -> Splits:
    """A card config's splits and their files (or the default rules' when it lists none)."""
    from datasets.data_files import sanitize_patterns

    base = (config.get("data_dir") or "").strip("/")
    if config.get("data_files") is None:
        return default_splits(files, base)
    return {
        split: _resolve(files, patterns, base)
        for split, patterns in sanitize_patterns(config["data_files"]).items()
    }


def default_config(names: list[str], marked: str | None, requested: str | None) -> str:
    """The config to import: `requested`, else the only one, the one marked default, or the one
    called `default`."""
    if requested is not None:
        if requested not in names:
            raise DatasetError(
                f"The dataset has no config {requested}. Its configs are {', '.join(names)}."
            )
        return requested
    if len(names) == 1:
        return names[0]
    if marked is not None:
        return marked
    if "default" in names:
        return "default"
    raise DatasetError(
        f"The dataset has several configs, so choose one with --config: {', '.join(names)}."
    )


def _readable(splits: Splits) -> dict[str, list[DataFile]] | None:
    """Each split's files that an import reads, or `None` when a split has none."""
    readable: dict[str, list[DataFile]] = {}
    for split, paths in splits.items():
        files = [DataFile(path, f) for path in paths if (f := file_format(path)) is not None]
        if not files:
            return None
        readable[split] = files
    return readable or None


def _choose_splits(
    found: dict[str, list[DataFile]], splits: Collection[str] | None
) -> dict[str, list[DataFile]]:
    if splits is None:
        return found
    missing = [split for split in splits if split not in found]
    if missing:
        raise DatasetError(
            f"The config has no split {', '.join(missing)}. Its splits are {', '.join(found)}."
        )
    return {split: found[split] for split in found if split in splits}


def parquet_splits(files: Collection[str]) -> dict[str, Splits]:
    """
    The Hub's Parquet conversion (`refs/convert/parquet`), as each config's splits and files.

    The conversion is laid out as `{config}/{split}/0000.parquet`. Splits only partly converted
    (`partial-{split}`, for large datasets) are named as such, and refused when chosen.
    """
    configs: dict[str, Splits] = {}
    for path in sorted(files):
        parts = path.split("/")
        if len(parts) != 3 or not parts[2].endswith(".parquet"):
            continue
        configs.setdefault(parts[0], {}).setdefault(parts[1], []).append(path)
    return configs


def _hub_errors() -> tuple[type[Exception], ...]:
    from huggingface_hub.errors import HfHubHTTPError, HFValidationError

    return (HfHubHTTPError, HFValidationError)


def _describe(error: Exception, repo: str) -> str:
    from huggingface_hub.errors import GatedRepoError, RepositoryNotFoundError

    if isinstance(error, GatedRepoError):
        return (
            f"{repo} is gated. Accept its terms on huggingface.co, then sign in with "
            "hf auth login or set HF_TOKEN."
        )
    if isinstance(error, RepositoryNotFoundError):
        return (
            f"There's no dataset {repo} on the Hub, or it's private and you aren't signed in "
            "(hf auth login, or HF_TOKEN)."
        )
    return f"Couldn't reach the Hub for {repo}: {error}"


def _hub[T](call: Callable[[], T], repo: str, revision: str | None, missing: bool = False) -> T:
    """`call()`, with the Hub's errors as [`DatasetError`]s (except a missing revision, which
    is raised as it is when `missing` is set)."""
    from huggingface_hub.errors import RevisionNotFoundError

    try:
        return call()
    except RevisionNotFoundError:
        if missing:
            raise
        raise DatasetError(f"{repo} has no revision {revision}.") from None
    except _hub_errors() as error:
        raise DatasetError(_describe(error, repo)) from None


def plan(
    repo: str,
    *,
    config: str | None = None,
    splits: Collection[str] | None = None,
    revision: str | None = None,
    token: str | bool | None = None,
    endpoint: str | None = None,
) -> Manifest:
    """
    What [`download_from_hf`] would download, without downloading it: the commit, config and
    each split's files. Reaches the Hub's API only.

    Args:
        repo (`str`):
            The dataset, as `owner/name`.
        config (`str`, *optional*):
            The config. Defaults to the only one, the card's default, or `default`.
        splits (`Collection[str]`, *optional*):
            The splits to download. Defaults to all.
        revision (`str`, *optional*):
            A branch, tag or commit. Defaults to `main`.
        token (`str` or `bool`, *optional*):
            The Hugging Face token, as `huggingface_hub` takes it. Defaults to your sign-in, and
            `False` sends none.
        endpoint (`str`, *optional*):
            The Hub's URL. Defaults to `huggingface_hub`'s (`HF_ENDPOINT`).

    Returns:
        [`~hub.datasets.Manifest`]: the manifest the download writes.

    Raises:
        [`~hub.datasets.DatasetError`]: when the repo can't be reached or has nothing to import.
    """
    from huggingface_hub import HfApi

    api = HfApi(endpoint=endpoint, token=token)
    info = _hub(lambda: api.dataset_info(repo, revision=revision), repo, revision)
    files = [s.rfilename for s in info.siblings or []]
    card = CARD if CARD in files else None
    assert info.sha is not None

    configs = card_configs(info.card_data)
    if configs:
        from datasets.utils.metadata import MetadataConfigs

        names = list(configs)
        try:
            marked = MetadataConfigs(configs).get_default_config_name()
        except ValueError as error:
            raise DatasetError(f"The dataset card's configs aren't valid: {error}") from None
        chosen = default_config(names, marked, config)
        found = _readable(repo_splits(files, configs[chosen]))
    else:
        names = ["default"]
        chosen = default_config(names, None, config)
        found = _readable(default_splits(files))
    if found is not None:
        return Manifest(
            repo=repo,
            revision=info.sha,
            config=chosen,
            configs=names,
            splits=_choose_splits(found, splits),
            card=card,
        )

    # The repo's own files aren't ones Flock reads (a loading script, images, archives): use
    # the Hub's Parquet conversion of it instead.
    from huggingface_hub.errors import RevisionNotFoundError

    try:
        converted = _hub(
            lambda: api.dataset_info(repo, revision=PARQUET_REF), repo, PARQUET_REF, missing=True
        )
    except RevisionNotFoundError:
        converted = None
    siblings = (converted.siblings or []) if converted is not None else []
    layout = parquet_splits([s.rfilename for s in siblings])
    if not layout:
        raise DatasetError(
            f"{repo} has no Parquet, JSON Lines, CSV or Arrow files, and the Hub hasn't "
            "converted it to Parquet, so it can't be imported."
        )
    names = list(layout)
    chosen = default_config(names, None, config)
    converted_splits = layout[chosen]
    wanted = {s.removeprefix("partial-"): s for s in converted_splits}
    chosen_splits = list(wanted) if splits is None else list(splits)
    missing = [s for s in chosen_splits if s not in wanted]
    if missing:
        raise DatasetError(
            f"The config has no split {', '.join(missing)}. Its splits are {', '.join(wanted)}."
        )
    partial = [s for s in chosen_splits if wanted[s] != s]
    if partial:
        raise DatasetError(
            f"The Hub only converted part of {repo}'s {', '.join(partial)} split to Parquet, "
            "so it can't be imported in full."
        )
    assert converted is not None and converted.sha is not None
    return Manifest(
        repo=repo,
        revision=converted.sha,
        config=chosen,
        configs=names,
        splits={
            s: [DataFile(path, "parquet") for path in converted_splits[s]] for s in chosen_splits
        },
        card=card,
        ref=PARQUET_REF,
    )


def download_from_hf(
    repo: str,
    to: str | Path,
    *,
    config: str | None = None,
    splits: Collection[str] | None = None,
    revision: str | None = None,
) -> Path:
    """
    Downloads a Hugging Face dataset's files for one config to a folder that
    [`~hub.datasets.convert`] reads, on a machine that can reach the Hub.

    The repo's own Parquet, JSON Lines, CSV or Arrow files are downloaded, matched to splits by
    the dataset card's `configs:` block or the Hub's default file names. A dataset with none
    (a loading script, say) is downloaded from the Hub's Parquet conversion instead. The folder
    gets the dataset card and a `flock-import.json` saying which files belong to which split.

    Downloads resume, and gated or private repos use your Hugging Face sign-in (`HF_TOKEN` or
    `hf auth login`). Nothing here reaches Flock.

    Args:
        repo (`str`):
            The dataset, as `owner/name`.
        to (`str` or `Path`):
            The folder to download to. It's created if missing.
        config (`str`, *optional*):
            The config. Defaults to the only one, the card's default, or `default`.
        splits (`Collection[str]`, *optional*):
            The splits to download. Defaults to all.
        revision (`str`, *optional*):
            A branch, tag or commit. Defaults to `main`.

    Returns:
        `Path`: the folder.

    Raises:
        [`~hub.datasets.DatasetError`]: when the repo can't be reached, the config or a split
            doesn't exist, or there's nothing to import.

    Example:

    ```python
    from flock.hub import datasets

    folder = datasets.download_from_hf(
        "HuggingFaceFW/fineweb-edu", "/scratch/fineweb-edu", config="sample-10BT"
    )
    ```
    """
    from huggingface_hub import hf_hub_download, snapshot_download

    manifest = plan(repo, config=config, splits=splits, revision=revision)
    folder = Path(to).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    wanted = [f.path for files in manifest.splits.values() for f in files]
    _hub(
        lambda: snapshot_download(
            repo,
            repo_type="dataset",
            revision=manifest.revision,
            local_dir=folder,
            allow_patterns=[re.sub(r"([\[\]*?])", r"[\1]", path) for path in wanted],
        ),
        repo,
        manifest.revision,
    )
    if manifest.card is not None:
        # From the repo itself, since the Parquet conversion's branch has no card.
        card_revision = revision if manifest.ref is not None else manifest.revision
        _hub(
            lambda: hf_hub_download(
                repo, CARD, repo_type="dataset", revision=card_revision, local_dir=folder
            ),
            repo,
            card_revision,
        )
    missing = [path for path in wanted if not (folder / path).is_file()]
    if missing:
        raise DatasetError(f"The download didn't complete: {missing[0]} is missing.")
    (folder / MANIFEST).write_text(manifest.to_json())
    return folder
