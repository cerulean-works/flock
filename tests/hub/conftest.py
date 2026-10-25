from pathlib import Path

import datasets
import pytest

from flock.hub import HubConfig

datasets.disable_progress_bars()


@pytest.fixture
def hub(tmp_path: Path) -> HubConfig:
    """A hub in direct mode over a local folder."""
    return HubConfig(root=str(tmp_path / "hub"))


@pytest.fixture
def cache_dir(tmp_path: Path) -> str:
    """An HF cache directory of the test's own."""
    return str(tmp_path / "hf-cache")
