"""Fixtures shared by the app tests, the CLI tests and the S3 tests."""

import importlib
import itertools
import os
import secrets
import socket
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest


def pytest_xdist_auto_num_workers() -> int:
    """Bound local resource use; explicit ``-n N`` still overrides auto detection."""
    available = int(os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS", os.process_cpu_count() or 1))
    return min(4, max(1, available))


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Keep module-scoped S3 environments together, without serializing SDK tests."""
    for item in items:
        if "make_module_flock" in getattr(item, "fixturenames", ()):
            item.add_marker(
                pytest.mark.xdist_group(str(item.path.relative_to(item.config.rootpath)))
            )


class Clock:
    """A module-local wall clock; event loops and database clocks keep running normally."""

    def __init__(self) -> None:
        self.now = time.time()

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def __getattr__(self, name: str) -> Any:
        return getattr(time, name)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], Clock]:
    def bind(module: str) -> Clock:
        clock = Clock()
        monkeypatch.setattr(importlib.import_module(module), "time", clock)
        return clock

    return bind


@pytest.fixture(scope="session")
def postgres() -> Iterator[Any]:
    """One PostgreSQL server for every flock the session starts (see ``serve_postgres``)."""
    from tests.app.conftest import serve_postgres  # here, so other tests needn't load the app

    yield from serve_postgres()


class S3Server:
    """
    The S3 server that test flocks put their proxy in front of.

    By default this is moto's S3 server, in this process. Setting ``FLOCK_TEST_S3_ENDPOINT``,
    ``FLOCK_TEST_S3_ACCESS_KEY`` and ``FLOCK_TEST_S3_SECRET_KEY`` runs the same tests against a
    real server (Cerulean's Ceph) instead. Every bucket the tests create is named
    ``flock-test-<run id>-…`` (see ``bucket_prefix``), and the session removes them all at the
    end, so other buckets on a shared server are never touched.
    """

    def __init__(self, endpoint: str, access_key_id: str, secret_access_key: str, real: bool):
        self.endpoint = endpoint
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.region = "us-east-1"
        self.real = real
        self.run_prefix = f"flock-test-{secrets.token_hex(3)}-"
        self._counter = itertools.count()

    def bucket_prefix(self) -> str:
        """A new ``FLOCK_S3_BUCKET_PREFIX`` for one flock: each test database numbers its orgs
        from 1, so flocks sharing the server need prefixes of their own."""
        return f"{self.run_prefix}{next(self._counter)}-"

    def client(self) -> Any:
        """A boto3 S3 client with the global key, reaching the server directly."""
        import boto3
        from botocore.config import Config

        return boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            region_name=self.region,
            config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
        )

    def buckets(self, prefix: str | None = None) -> list[str]:
        """The server's buckets whose names start with ``prefix`` (default: this run's)."""
        prefix = prefix or self.run_prefix
        names = [b["Name"] for b in self.client().list_buckets().get("Buckets", [])]
        return sorted(name for name in names if name.startswith(prefix))

    def remove_buckets(self, prefix: str | None = None) -> list[str]:
        """Empty and delete this run's buckets (those starting with ``prefix``, itself inside
        the run's prefix); returns their names."""
        prefix = prefix or self.run_prefix
        if not prefix.startswith(self.run_prefix):
            raise ValueError(f"{prefix} is not one of this test run's prefixes")
        client = self.client()
        removed = []
        for name in self.buckets(prefix):
            for upload in client.list_multipart_uploads(Bucket=name).get("Uploads", []):
                client.abort_multipart_upload(
                    Bucket=name, Key=upload["Key"], UploadId=upload["UploadId"]
                )
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=name):
                objects = [{"Key": o["Key"]} for o in page.get("Contents", [])]
                if objects:
                    client.delete_objects(Bucket=name, Delete={"Objects": objects, "Quiet": True})
            client.delete_bucket(Bucket=name)
            removed.append(name)
        return removed


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def s3_server() -> Iterator[S3Server]:
    """A real S3 server if ``FLOCK_TEST_S3_ENDPOINT`` is set, else moto's, in this process.
    Every bucket the session created is removed at the end, whatever happened."""
    endpoint = os.environ.get("FLOCK_TEST_S3_ENDPOINT")
    if endpoint:
        if "://" not in endpoint:
            endpoint = f"https://{endpoint}"
        server = S3Server(
            endpoint,
            os.environ["FLOCK_TEST_S3_ACCESS_KEY"],
            os.environ["FLOCK_TEST_S3_SECRET_KEY"],
            real=True,
        )
        try:
            yield server
        finally:
            server.remove_buckets()
        return
    pytest.importorskip("moto")
    from moto.server import ThreadedMotoServer

    port = _free_port()
    moto = ThreadedMotoServer(ip_address="127.0.0.1", port=port, verbose=False)
    moto.start()
    try:
        yield S3Server(f"http://127.0.0.1:{port}", "flock-test", "flock-test-secret", real=False)
    finally:
        moto.stop()
