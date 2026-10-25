"""Flock's dataset hub: versioned Lance datasets in our own S3, loaded through HF `datasets`."""

from typing import TYPE_CHECKING, Any

from flock.hub.client import DatasetNotFound, HubError
from flock.hub.config import HubConfig
from flock.hub.dataset import Dataset, split_dataset_by_node
from flock.hub.load import ImportTooLarge, VersionNotFound, load_dataset
from flock.hub.write import CommitConflict, DatasetWriter, create_dataset, push

if TYPE_CHECKING:
    from flock.hub.importer import import_hf_dataset

__all__ = [
    "CommitConflict",
    "Dataset",
    "DatasetNotFound",
    "DatasetWriter",
    "HubConfig",
    "HubError",
    "ImportTooLarge",
    "VersionNotFound",
    "create_dataset",
    "import_hf_dataset",
    "load_dataset",
    "push",
    "split_dataset_by_node",
]


def __getattr__(name: str) -> Any:
    # Imported on first use, so that `python -m flock.hub.importer` (the server's mirror
    # imports) doesn't find the module already imported by its package and warn.
    if name == "import_hf_dataset":
        from flock.hub.importer import import_hf_dataset

        return import_hf_dataset
    raise AttributeError(f"module 'flock.hub' has no attribute {name!r}")
