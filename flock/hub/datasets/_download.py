"""Downloading a Hugging Face dataset's data files to a folder that [`~hub.datasets.convert`]
reads (D119). Only the Hub is reached: nothing here needs Flock."""

import posixpath
import re
from collections.abc import Callable, Collection, Iterable, Mapping
from functools import cache
from pathlib import Path
from typing import Any

from flock.hub.datasets._common import MANIFEST, DataFile, DatasetError, Manifest, file_format

PARQUET_REF = "refs/convert/parquet"
"""The branch where the Hub keeps its Parquet conversion of a dataset."""

CARD = "README.md"

type Splits = dict[str, list[str]]

type Configs = str | Collection[str] | Mapping[str, Collection[str] | None] | None
"""The configs to import: one name, several, or each name with its own splits."""


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


def requested_configs(config: Configs) -> dict[str, list[str] | None] | None:
    """
    `config` as [`plan`] takes it, as each config asked for with its own splits (`None` when
    the shared `splits` filter applies to it), or `None` for the default config.
    """
    if config is None:
        return None
    if isinstance(config, str):
        return {config: None}
    requested: dict[str, list[str] | None] = {}
    if isinstance(config, Mapping):
        for name, splits in config.items():
            if splits is None:
                requested[name] = None
                continue
            chosen = list(dict.fromkeys([splits] if isinstance(splits, str) else splits))
            if not chosen:
                raise DatasetError(f"No split of the config {name} is chosen.")
            requested[name] = chosen
    else:
        requested = dict.fromkeys(config)
    if not requested:
        raise DatasetError("Choose at least one config.")
    return requested


def choose_configs(
    names: list[str], marked: str | None, requested: dict[str, list[str] | None] | None
) -> dict[str, list[str] | None]:
    """The configs to import, in the repo's order, with the splits asked for in each:
    `requested`, else the only one, the one marked default, or the one called `default`."""
    if requested is not None:
        missing = [name for name in requested if name not in names]
        if missing:
            raise DatasetError(
                f"The dataset has no config {', '.join(missing)}. Its configs are "
                f"{', '.join(names)}."
            )
        return {name: requested[name] for name in names if name in requested}
    if len(names) == 1:
        return {names[0]: None}
    if marked is not None:
        return {marked: None}
    if "default" in names:
        return {"default": None}
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


def choose_splits(
    found: dict[str, dict[str, list[DataFile]]],
    chosen: dict[str, list[str] | None],
    splits: Collection[str] | None,
    partial: Collection[tuple[str, str]] = (),
) -> dict[str, dict[str, list[DataFile]]]:
    """
    Each chosen config's splits. A config asked for with its own splits gets exactly those.
    The others get each of `splits` they have (all their splits when it's `None`). A split no
    such config has is refused, and so is a config left with none, or a split the Hub only
    partly converted (in `partial`, as `(config, split)`).
    """
    shared = None if splits is None else list(dict.fromkeys(splits))
    kept: dict[str, list[str]] = {}
    for config, own in chosen.items():
        available = found[config]
        if own is not None:
            missing = [split for split in own if split not in available]
            if missing:
                raise DatasetError(
                    f"The config {config} has no split {', '.join(missing)}. Its splits are "
                    f"{', '.join(available)}."
                )
        wanted = own if own is not None else shared
        kept[config] = [split for split in available if wanted is None or split in wanted]
    sharing = [config for config, own in chosen.items() if own is None]
    if shared is not None and sharing:
        nowhere = [s for s in shared if not any(s in found[config] for config in sharing)]
        if nowhere:
            had = sorted({split for config in sharing for split in found[config]})
            if len(sharing) == 1:
                which = f"The config {sharing[0]}" if len(chosen) > 1 else "The config"
                raise DatasetError(
                    f"{which} has no split {', '.join(nowhere)}. Its splits are "
                    f"{', '.join(found[sharing[0]])}."
                )
            raise DatasetError(
                f"No config chosen has the split {', '.join(nowhere)}. Their splits are "
                f"{', '.join(had)}."
            )
        empty = [config for config in sharing if not kept[config]]
        if empty:
            raise DatasetError(
                f"The config {empty[0]} has none of the splits {', '.join(shared)}. Its splits "
                f"are {', '.join(found[empty[0]])}."
            )
    partly = [
        f"{c}/{s}" if len(chosen) > 1 else s for c in kept for s in kept[c] if (c, s) in partial
    ]
    if partly:
        raise DatasetError(
            f"The Hub only converted part of the {', '.join(partly)} split to Parquet, so it "
            "can't be imported in full."
        )
    return {config: {s: found[config][s] for s in kept[config]} for config in kept}


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
    config: Configs = None,
    splits: Collection[str] | None = None,
    revision: str | None = None,
    token: str | bool | None = None,
    endpoint: str | None = None,
) -> Manifest:
    """
    What [`download_from_hf`] would download, without downloading it: the commit, configs and
    each split's files. Reaches the Hub's API only.

    Args:
        repo (`str`):
            The dataset, as `owner/name`.
        config (`str`, `Collection[str]` or `Mapping[str, Collection[str] | None]`, *optional*):
            The configs, by name, or as a mapping from each name to its splits (`None` for those
            `splits` picks). Defaults to the only one, the card's default, or `default`.
        splits (`Collection[str]`, *optional*):
            The splits to download of each config that has them, for configs not given their
            own. Defaults to all.
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
        [`~hub.datasets.DatasetError`]: when the repo can't be reached or has nothing to import,
            or a config or split doesn't exist.
    """
    return _plan(repo, config, splits, revision, token, endpoint, every=False)


def catalog(
    repo: str,
    *,
    revision: str | None = None,
    token: str | bool | None = None,
    endpoint: str | None = None,
) -> Manifest:
    """Every config and split of `repo` that an import can take, as a [`plan`] choosing them
    all would list them (leaving out splits the Hub only partly converted)."""
    return _plan(repo, None, None, revision, token, endpoint, every=True)


def _plan(
    repo: str,
    config: Configs,
    splits: Collection[str] | None,
    revision: str | None,
    token: str | bool | None,
    endpoint: str | None,
    every: bool,
) -> Manifest:
    from huggingface_hub import HfApi

    requested = requested_configs(config)
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
        chosen = dict.fromkeys(names) if every else choose_configs(names, marked, requested)
        found = {name: _readable(repo_splits(files, configs[name])) for name in chosen}
    else:
        names = ["default"]
        default = _readable(default_splits(files))
        if requested is not None and set(requested) != {"default"} and default is not None:
            # Configs other than default only exist in the Hub's Parquet conversion (of a
            # dataset with a loading script), unless the repo's own files are readable.
            choose_configs(names, None, requested)
        chosen = dict.fromkeys(names) if every else (requested or {"default": None})
        found = {"default": default if set(chosen) == {"default"} else None}
    readable = {name: splits for name, splits in found.items() if splits is not None}
    if len(readable) == len(found):
        return Manifest(
            repo=repo,
            revision=info.sha,
            configs=names,
            splits=choose_splits(readable, chosen, splits),
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
    assert converted is not None and converted.sha is not None
    names = list(layout)
    partial = {
        (name, split.removeprefix("partial-"))
        for name, found_splits in layout.items()
        for split in found_splits
        if split.startswith("partial-")
    }
    converted_splits = {
        name: {
            split.removeprefix("partial-"): [DataFile(path, "parquet") for path in paths]
            for split, paths in found_splits.items()
            if not (every and split.startswith("partial-"))
        }
        for name, found_splits in layout.items()
    }
    if every:
        chosen_splits = {name: found for name, found in converted_splits.items() if found}
    else:
        chosen = choose_configs(names, None, requested)
        chosen_splits = choose_splits(converted_splits, chosen, splits, partial)
    return Manifest(
        repo=repo,
        revision=converted.sha,
        configs=names,
        splits=chosen_splits,
        card=card,
        ref=PARQUET_REF,
    )


def download_from_hf(
    repo: str,
    to: str | Path,
    *,
    config: Configs = None,
    splits: Collection[str] | None = None,
    revision: str | None = None,
) -> Path:
    """
    Downloads a Hugging Face dataset's files for one or more configs to a folder that
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
        config (`str`, `Collection[str]` or `Mapping[str, Collection[str] | None]`, *optional*):
            The configs, by name, or as a mapping from each name to its splits (`None` for those
            `splits` picks). Defaults to the only one, the card's default, or `default`.
        splits (`Collection[str]`, *optional*):
            The splits to download of each config that has them, for configs not given their
            own. Defaults to all.
        revision (`str`, *optional*):
            A branch, tag or commit. Defaults to `main`.

    Returns:
        `Path`: the folder.

    Raises:
        [`~hub.datasets.DatasetError`]: when the repo can't be reached, the config or a split
            doesn't exist, a config would have no splits left, or there's nothing to import.

    Example:

    ```python
    from flock.hub import datasets

    folder = datasets.download_from_hf(
        "HuggingFaceFW/fineweb-edu", "/scratch/fineweb-edu", config=["sample-10BT", "sample-100BT"]
    )
    ```
    """
    from huggingface_hub import hf_hub_download, snapshot_download

    manifest = plan(repo, config=config, splits=splits, revision=revision)
    folder = Path(to).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    wanted = [f.path for f in manifest.files]
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
