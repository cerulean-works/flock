"""`load_dataset`: a hub dataset at one version, as a `flock.hub.Dataset`."""

from typing import TYPE_CHECKING, Any

import lance

from flock.hub.client import DatasetNotFound
from flock.hub.config import HubConfig, get_default, parse_repo_id
from flock.hub.dataset import DEFAULT_BATCH_SIZE, Dataset, Pin, _ReadSpec

if TYPE_CHECKING:
    from lance.dataset import LanceDataset


class VersionNotFound(LookupError):
    """The dataset has no such version or tag."""


def load_dataset(
    repo_id: str,
    version: int | str | None = None,
    *,
    columns: list[str] | None = None,
    filter: str | None = None,
    streaming: bool = True,
    batch_size: int = DEFAULT_BATCH_SIZE,
    cache_dir: str | None = None,
    hub_config: HubConfig | None = None,
    config: str | None = None,
    hf_revision: str | None = None,
    auto_import: bool = True,
    **kwargs: Any,
) -> Dataset:
    """
    Loads one version of a hub dataset.

    The version is resolved once and pinned: a `Dataset` loaded with `version=None` keeps the version that was latest at load, so a long training run never sees a later commit.

    The hub has no splits. A dataset imported from Hugging Face with several splits has a `split` column instead: pass `filter="split = 'test'"`.

    Args:
        repo_id (`str`):
            The dataset, as `org/name`.
        version (`int` or `str`, *optional*):
            A version number or a tag. Defaults to the latest version.
        columns (`list[str]`, *optional*):
            Read only these columns, in this order.
        filter (`str`, *optional*):
            A SQL predicate that Lance pushes down, such as `"split = 'test'"`. Row counts and positions for random access count matching rows only.
        streaming (`bool`, *optional*, defaults to `True`):
            Wrap an HF `IterableDataset` that reads Lance as it goes. With `False`, the version is first copied into HF's cache (once per version, columns and filter) and an HF `Dataset` is wrapped.
        batch_size (`int`, *optional*, defaults to 256):
            Rows per batch read from Lance.
        cache_dir (`str`, *optional*):
            HF's cache directory for non-streaming datasets. Defaults to HF's.
        hub_config (`HubConfig`, *optional*):
            Where the hub is. Defaults to [`~config.get_default`].
        config (`str`, *optional*):
            The Hugging Face config to fall back to. Reserved: the fallback isn't available yet.
        hf_revision (`str`, *optional*):
            The Hugging Face revision to fall back to. Reserved, as `config`.
        auto_import (`bool`, *optional*, defaults to `True`):
            Fall back to Hugging Face for a missing dataset. Reserved, as `config`.

    Returns:
        [`Dataset`]: the dataset at the pinned version.

    Raises:
        `TypeError`: for `split=` or any other unknown keyword argument.
        [`~client.DatasetNotFound`]: if the dataset doesn't exist or has no versions yet.
        [`VersionNotFound`]: if the version or tag doesn't exist.
        `ValueError`: if a name in `columns` isn't a column, or `repo_id` isn't `org/name`.
    """
    if "split" in kwargs:
        raise TypeError(
            "the hub has no splits; datasets imported with several splits have a 'split' column, "
            "so pass filter=\"split = 'test'\" instead"
        )
    if kwargs:
        raise TypeError(f"load_dataset() got unexpected keyword arguments {sorted(kwargs)}")
    if batch_size < 1:
        raise ValueError(f"batch_size must be at least 1, got {batch_size}")
    # The Hugging Face fallback (config, hf_revision, auto_import) comes with the importer.
    del config, hf_revision, auto_import

    hub = hub_config or get_default()
    pin = resolve(repo_id, version, hub)
    spec = _ReadSpec(
        repo_id=repo_id,
        pin=pin,
        columns=list(columns) if columns is not None else None,
        filter=filter,
        batch_size=batch_size,
        cache_dir=cache_dir,
    )
    return Dataset._load(spec, streaming)


def resolve(repo_id: str, version: int | str | None, hub: HubConfig) -> Pin:
    """
    Finds `repo_id` and pins `version` (an int, a tag, or `None` for the latest) to a version number.

    Raises:
        [`~client.DatasetNotFound`]: if the dataset doesn't exist or has no versions yet.
        [`VersionNotFound`]: if the version or tag doesn't exist.
    """
    if isinstance(version, bool) or not isinstance(version, int | str | None):
        raise TypeError(f"version must be an int, a tag name or None, got {version!r}")
    org, name = parse_repo_id(repo_id)
    dataset_id = None
    if hub.direct:
        uri = hub.dataset_uri(org, name)
    else:
        info = hub.client.get_dataset(org, name)
        uri, dataset_id = info["uri"], info["id"]

    latest = open_latest(repo_id, uri, hub)
    if version is None:
        pin = Pin(uri, latest.version, hub, dataset_id)
        pin.remember(latest)
        return pin
    try:
        pinned = latest.checkout_version(version)
    except (OSError, ValueError) as e:
        tags = sorted(latest.tags.list())
        raise VersionNotFound(
            f"{repo_id} has no version {version!r}; the latest is {latest.version}"
            + (f" and its tags are {tags}" if tags else " and it has no tags")
        ) from e
    pin = Pin(uri, pinned.version, hub, dataset_id)
    pin.remember(pinned)
    return pin


def open_latest(repo_id: str, uri: str, hub: HubConfig) -> LanceDataset:
    """
    Opens the latest version of the Lance dataset at `uri`.

    Raises:
        [`~client.DatasetNotFound`]: if there is no Lance dataset there.
    """
    try:
        return lance.dataset(uri, storage_options=hub.lance_storage_options())
    except (OSError, ValueError) as e:
        if "not found" in str(e).lower():
            raise DatasetNotFound(
                f"{repo_id} not found (or it has no versions yet); "
                "loading from Hugging Face isn't available yet"
            ) from e
        raise
