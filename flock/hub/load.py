"""`load_dataset`: a hub dataset at one version, as a `flock.hub.Dataset`, falling back to a mirror of the Hugging Face dataset of the same name."""

import logging
import time
from typing import TYPE_CHECKING, Any

import lance

from flock.hub.client import DatasetNotFound, HubError
from flock.hub.config import HubConfig, get_default, parse_repo_id
from flock.hub.dataset import DEFAULT_BATCH_SIZE, Dataset, Pin, _ReadSpec

if TYPE_CHECKING:
    from lance.dataset import LanceDataset

log = logging.getLogger(__name__)

POLL_SECONDS = 2.0
"""How often a server-side import is polled while `load_dataset` waits for it."""


class VersionNotFound(LookupError):
    """The dataset has no such version or tag."""


class ImportTooLarge(HubError):
    """The Hugging Face dataset is too large for the server to import by itself. `command` is the `flock-hub import` command line to run instead."""

    def __init__(self, message: str, command: str) -> None:
        super().__init__(message, 422, {"command": command})
        self.command = command


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

    If `repo_id` isn't a hub dataset and `auto_import` is on, it is taken as a Hugging Face dataset, and its mirror is loaded instead (`hf/<owner>--<name>[--<config>]`): at the version imported from `hf_revision` if one is given, else the latest. If there is no mirror (or none of that revision), the server imports it first, logging its progress while `load_dataset` waits. In direct mode the import runs here, into the same root.

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
            The Hugging Face config to fall back to.
        hf_revision (`str`, *optional*):
            The Hugging Face branch, tag or commit to fall back to. Defaults to the mirror's latest import, or `main` when there is none.
        auto_import (`bool`, *optional*, defaults to `True`):
            Fall back to a mirror of the Hugging Face dataset `repo_id`, importing it if needed.

    Returns:
        [`Dataset`]: the dataset at the pinned version.

    Raises:
        `TypeError`: for `split=` or any other unknown keyword argument.
        [`~client.DatasetNotFound`]: if the dataset doesn't exist or has no versions yet, and isn't on Hugging Face either (or `auto_import` is off).
        [`VersionNotFound`]: if the version or tag doesn't exist.
        [`ImportTooLarge`]: if the Hugging Face dataset is too large for the server to import; its message holds the `flock-hub import` command to run instead.
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
    hub = hub_config or get_default()
    try:
        pin = resolve(repo_id, version, hub)
    except DatasetNotFound:
        if not auto_import:
            raise
        try:
            repo_id, version = fall_back(repo_id, version, config, hf_revision, hub)
        except LookupError as e:  # not on Hugging Face either
            raise DatasetNotFound(
                f"{repo_id} not found on the hub, nor on Hugging Face ({e})", 404
            ) from e
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
            raise DatasetNotFound(f"{repo_id} not found (or it has no versions yet)") from e
        raise


def fall_back(
    repo: str,
    version: int | str | None,
    config: str | None,
    hf_revision: str | None,
    hub: HubConfig,
) -> tuple[str, int | str | None]:
    """
    The mirror of the Hugging Face dataset `repo` and the version to load (D10), importing it first if there is none.

    `version`, if given, is a version of the mirror and wins over `hf_revision`.

    Returns:
        `tuple`: the mirror's repo id and the version to load.
    """
    if hub.direct:
        return _fall_back_direct(repo, version, config, hf_revision, hub)
    try:
        return _fall_back_server(repo, version, config, hf_revision, hub)
    except HubError as e:
        if e.status == 404:  # the server found no such repo on Hugging Face
            raise LookupError(str(e)) from e
        raise


def _fall_back_server(
    repo: str,
    version: int | str | None,
    config: str | None,
    hf_revision: str | None,
    hub: HubConfig,
) -> tuple[str, int | str | None]:
    client = hub.client
    known = client.mirrors(repo, config)
    dataset = known["dataset"]
    finished = [m for m in known["mirrors"] if m["state"] == "finished"]
    if finished and (hf_revision is None or version is not None):
        return dataset, version
    if hf_revision is not None and len(hf_revision) >= 7:
        for mirror in finished:
            if mirror["sha"].startswith(hf_revision.lower()):
                return dataset, mirror["version"]
    try:
        mirror = client.request_mirror(repo, config, hf_revision)
    except HubError as e:
        if e.status == 422 and "command" in e.body:
            raise ImportTooLarge(
                f"{repo} is too large for the server to import by itself; import it with:\n"
                f"  {e.body['command']}",
                e.body["command"],
            ) from e
        raise
    while mirror["state"] == "running":
        progress = mirror.get("progress") or {}
        log.info(
            "importing %s from Hugging Face into %s: %s %s/%s",
            repo, mirror["dataset"], progress.get("stage", "starting"),
            progress.get("done", 0), progress.get("total", "?"),
        )  # fmt: skip
        time.sleep(POLL_SECONDS)
        mirror = client.get_mirror(mirror["id"])
    if mirror["state"] != "finished":
        raise HubError(f"importing {repo} from Hugging Face failed: {mirror.get('error')}")
    return mirror["dataset"], version if version is not None else mirror["version"]


def _fall_back_direct(
    repo: str,
    version: int | str | None,
    config: str | None,
    hf_revision: str | None,
    hub: HubConfig,
) -> tuple[str, int | str | None]:
    """Direct mode has no server, so the mirror lives under the same root and is imported here."""
    # Imported here: the importer pulls in this module through `write`.
    from flock.hub import importer

    mirror = importer.mirror_repo_id(repo, config)
    org, name = parse_repo_id(mirror)
    try:
        latest = open_latest(mirror, hub.dataset_uri(org, name), hub)
    except DatasetNotFound:
        latest = None
    sha = None
    if hf_revision is not None:
        is_sha = importer.is_sha(hf_revision)
        sha = hf_revision if is_sha else importer.source().sha(repo, hf_revision)
    if latest is not None:
        if version is not None or sha is None:
            return mirror, version
        tag = importer.hf_tag(sha)
        if tag in latest.tags.list():
            return mirror, tag
    log.info("importing %s from Hugging Face into %s", repo, mirror)
    imported = importer.import_hf_dataset(repo, config, sha or hf_revision, mirror, hub_config=hub)
    assert imported is not None
    return mirror, version if version is not None else imported.version
