import itertools
import os
import shutil
import subprocess
import textwrap
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass

import boto3
import pytest


@dataclass(frozen=True)
class S3Bucket:
    """A bucket on the test S3 server, with the global credentials the hub proxies for."""

    endpoint: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    region: str = "us-east-1"

    def storage_options(self) -> dict[str, str]:
        """Lance ``storage_options`` that reach this bucket directly (bypassing any proxy)."""
        return {
            "aws_endpoint": self.endpoint,
            "aws_access_key_id": self.access_key_id,
            "aws_secret_access_key": self.secret_access_key,
            "aws_region": self.region,
            "allow_http": "true",
        }

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
    A Ceph RGW for tests, handing out fresh buckets.

    Production runs Ceph RGW, so tests do too: a single radosgw on its SQLite ("dbstore")
    backend, which needs no monitors or OSDs. Note that this backend accepts
    ``If-None-Match: *`` on PUT without enforcing it, so only the hub proxy keeps concurrent
    Lance commits from overwriting each other here.
    """

    ACCESS_KEY_ID = "flock-test"
    SECRET_ACCESS_KEY = "flock-test-secret"

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint
        self._counter = itertools.count()
        self._client = S3Bucket(endpoint, "", self.ACCESS_KEY_ID, self.SECRET_ACCESS_KEY).client()

    @classmethod
    def start_container(cls, image: str = "quay.io/ceph/ceph:v20") -> tuple[str, str]:
        """Start a throwaway radosgw container; returns (container name, endpoint URL)."""
        image = os.environ.get("FLOCK_TEST_CEPH_IMAGE", image)
        name = f"flock-test-rgw-{os.getpid()}"
        script = textwrap.dedent(f"""\
            mkdir -p /var/lib/ceph/radosgw /var/run/ceph
            cat > /etc/ceph/ceph.conf <<EOF
            [client.rgw.test]
            rgw backend store = dbstore
            rgw config store = dbstore
            dbstore db dir = /var/lib/ceph/radosgw
            rgw frontends = beast port=7480
            EOF
            radosgw-admin --no-mon-config -n client.rgw.test user create --uid=flock \\
                --display-name=flock --access-key={cls.ACCESS_KEY_ID} \\
                --secret-key={cls.SECRET_ACCESS_KEY} > /dev/null
            exec radosgw -d --no-mon-config -n client.rgw.test
            """)
        started = subprocess.run(
            [
                "docker", "run", "-d", "--rm", "--name", name,
                "-p", "127.0.0.1::7480", "--tmpfs", "/var/lib/ceph/radosgw",
                "--entrypoint", "bash", image, "-c", script,
            ],
            check=False, capture_output=True, text=True,
        )  # fmt: skip
        if started.returncode != 0:
            pytest.skip(f"could not start a Ceph RGW container: {started.stderr.strip()}")
        port = (
            subprocess.run(
                ["docker", "port", name, "7480/tcp"], check=True, capture_output=True, text=True
            )
            .stdout.split()[0]
            .rpartition(":")[2]
        )
        endpoint = f"http://127.0.0.1:{port}"

        deadline = time.monotonic() + 60
        while True:
            try:
                urllib.request.urlopen(endpoint, timeout=2).close()
                break
            except urllib.error.HTTPError:
                break  # any S3 answer means it is serving
            except OSError:
                if time.monotonic() > deadline:
                    subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)
                    pytest.fail("Ceph RGW container did not become ready")
                time.sleep(0.25)
        return name, endpoint

    def bucket(self) -> S3Bucket:
        """A new empty bucket."""
        name = f"flock-test-{os.getpid()}-{next(self._counter)}"
        self._client.create_bucket(Bucket=name)
        return S3Bucket(self.endpoint, name, self.ACCESS_KEY_ID, self.SECRET_ACCESS_KEY)


@pytest.fixture(scope="session")
def s3_server() -> Iterator[S3Server]:
    """``FLOCK_TEST_S3_ENDPOINT`` (with the test credentials) if set, else a Ceph RGW container."""
    container = None
    endpoint = os.environ.get("FLOCK_TEST_S3_ENDPOINT")
    if not endpoint:
        if shutil.which("docker") is None:
            pytest.skip("set FLOCK_TEST_S3_ENDPOINT or install Docker to run S3 tests")
        container, endpoint = S3Server.start_container()
    try:
        yield S3Server(endpoint)
    finally:
        if container is not None:
            subprocess.run(["docker", "rm", "-f", container], check=False, capture_output=True)


@pytest.fixture
def s3_bucket(s3_server: S3Server) -> S3Bucket:
    """A fresh bucket on the test S3 server."""
    return s3_server.bucket()
