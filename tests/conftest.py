"""Fixtures shared by the app tests and the CLI tests."""

from collections.abc import Iterator
from typing import Any

import pytest


@pytest.fixture(scope="session")
def postgres() -> Iterator[Any]:
    """One PostgreSQL server for every flock the session starts (see ``serve_postgres``)."""
    from tests.app.conftest import serve_postgres  # here, so other tests needn't load the app

    yield from serve_postgres()
