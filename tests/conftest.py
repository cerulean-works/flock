import itertools
import os
import socket
from collections.abc import Iterator
from dataclasses import dataclass

import boto3
import pytest
from moto.server import ThreadedMotoServer


@dataclass(frozen=True)
class S3Bucket:
    """A bucket on the test S3 server, with the global credentials the hub proxies for."""

    endpoint: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    region: str = "us-east-1"
    # Prefix every key this test writes, so runs against a shared bucket don't collide.
    prefix: str = ""

    def storage_options(self) -> dict[str, str]:
        """Lance ``storage_options`` that reach this bucket directly (bypassing any proxy)."""
        options = {
            "aws_endpoint": self.endpoint,
            "aws_access_key_id": self.access_key_id,
            "aws_secret_access_key": self.secret_access_key,
            "aws_region": self.region,
        }
        if self.endpoint.startswith("http://"):
            options["allow_http"] = "true"
        return options

    def client(self):
        """A boto3 S3 client with the global credentials."""
        return boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            region_name=self.region,
        )


class S3Server:
    """
    An S3 server for tests, handing out fresh buckets.

    By default this is moto's S3 server, in this process: it agrees with production Ceph RGW on
    everything the hub relies on (conditional PUTs, ranged reads, multipart uploads, copies,
    listing order). Ceph's standalone "dbstore" backend, the other option that runs without a
    cluster, gets all of those wrong.

    Setting ``FLOCK_TEST_S3_ENDPOINT``, ``FLOCK_TEST_S3_ACCESS_KEY`` and ``FLOCK_TEST_S3_SECRET_KEY``
    runs the same tests against a real server instead. There, every test shares the bucket
    ``FLOCK_TEST_S3_BUCKET`` (default ``flock-hub-test``) under its own key prefix, and nothing is
    created outside it.
    """

    def __init__(self, endpoint: str, access_key_id: str, secret_access_key: str) -> None:
        self.endpoint = endpoint
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.shared_bucket = os.environ.get("FLOCK_TEST_S3_BUCKET", "flock-hub-test")
        self.real = "FLOCK_TEST_S3_ENDPOINT" in os.environ
        self._counter = itertools.count()
        self._run = os.urandom(4).hex()

    def bucket(self) -> S3Bucket:
        """A new empty bucket (or, on a real server, a new empty prefix of the shared one)."""
        n = next(self._counter)
        if self.real:
            name, prefix = self.shared_bucket, f"tests/{self._run}-{n}/"
        else:
            name, prefix = f"flock-test-{n}", ""
        bucket = S3Bucket(self.endpoint, name, self.access_key_id, self.secret_access_key)
        bucket = S3Bucket(**{**bucket.__dict__, "prefix": prefix})
        if not self.real:
            bucket.client().create_bucket(Bucket=name)
        return bucket


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def s3_server() -> Iterator[S3Server]:
    """A real S3 server if ``FLOCK_TEST_S3_ENDPOINT`` is set, else moto's, in this process."""
    endpoint = os.environ.get("FLOCK_TEST_S3_ENDPOINT")
    if endpoint:
        if "://" not in endpoint:
            endpoint = f"https://{endpoint}"
        yield S3Server(
            endpoint,
            os.environ["FLOCK_TEST_S3_ACCESS_KEY"],
            os.environ["FLOCK_TEST_S3_SECRET_KEY"],
        )
        return
    port = _free_port()
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=port, verbose=False)
    server.start()
    try:
        yield S3Server(f"http://127.0.0.1:{port}", "flock-test", "flock-test-secret")
    finally:
        server.stop()


@pytest.fixture
def s3_bucket(s3_server: S3Server) -> S3Bucket:
    """A fresh bucket on the test S3 server."""
    return s3_server.bucket()
