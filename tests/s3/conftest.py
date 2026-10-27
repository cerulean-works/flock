"""A flock with S3 on, its proxy, and helpers to reach buckets through it."""

import asyncio
import itertools
import secrets
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import asyncpg
import boto3
import httpx
import pytest
from botocore.auth import S3SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.config import Config
from botocore.credentials import Credentials
from botocore.exceptions import ClientError

from tests.app.conftest import Client, Flocks, Postgres, S3Flock, User
from tests.conftest import S3Server


@dataclass(frozen=True)
class Creds:
    access_key_id: str
    secret_access_key: str


class PayloadAuth(S3SigV4Auth):
    """botocore's signer, declaring ``payload_hash`` (if given) as x-amz-content-sha256."""

    def __init__(self, credentials: Credentials, payload_hash: str | None) -> None:
        super().__init__(credentials, "s3", "us-east-1")
        self.payload_hash = payload_hash

    def payload(self, request):
        return self.payload_hash or super().payload(request)


def code(error: ClientError) -> str:
    return error.response["Error"]["Code"]


def status(error: ClientError) -> int:
    return error.response["ResponseMetadata"]["HTTPStatusCode"]


@dataclass
class S3Env:
    """A flock with S3 on, with users in two orgs: ``admin`` (the super admin), ``carol`` (an
    admin of ``flock``), ``alice`` and ``bob`` (members of ``flock``) and ``olga`` (a member
    of ``lab``)."""

    flock: S3Flock
    server: S3Server
    users: dict[str, User] = field(default_factory=dict)
    _names: Any = field(default_factory=itertools.count)

    @property
    def proxy_url(self) -> str:
        return self.flock.proxy.url

    def api(self, user: str) -> Client:
        return self.flock.client() if user == "admin" else self.users[user].api

    def sql(self, query: str, *args: Any) -> list[asyncpg.Record]:
        async def run() -> list[asyncpg.Record]:
            conn = await asyncpg.connect(self.flock.database_url)
            try:
                return await conn.fetch(query, *args)
            finally:
                await conn.close()

        return asyncio.run(run())

    def key(self, user: str, **body: Any) -> Creds:
        """An ephemeral key of ``user`` (for their default org unless ``org`` is given)."""
        creds = self.flock.credentials(self.api(user), **body)
        return Creds(creds["access_key_id"], creds["secret_access_key"])

    def name(self, stem: str = "b") -> str:
        """A bucket name no other test uses."""
        return f"{stem}-{next(self._names)}-{secrets.token_hex(2)}"

    def bucket(self, org: str = "flock", user: str = "admin", name: str | None = None) -> str:
        """A new bucket of ``org``, created through the API; returns its name."""
        name = name or self.name()
        created = self.api(user).post(f"/api/v1/orgs/{org}/buckets", {"name": name})
        assert created.status == 201, created.body
        return name

    def upstream_name(self, name: str, org: str = "flock") -> str:
        pk = self.flock.store.orgs[org].pk
        return f"{self.flock.bucket_prefix}{pk:05d}-{name}"

    def s3(self, creds: Creds) -> Any:
        """A boto3 client signing with ``creds`` against the proxy."""
        return boto3.client(
            "s3",
            endpoint_url=self.proxy_url,
            aws_access_key_id=creds.access_key_id,
            aws_secret_access_key=creds.secret_access_key,
            region_name="us-east-1",
            config=Config(
                s3={"addressing_style": "path"},
                signature_version="s3v4",  # presigned URLs default to SigV2 otherwise
                retries={"max_attempts": 1},
            ),
        )

    def upstream(self) -> Any:
        """A boto3 client with the global key, reaching the S3 server directly."""
        return self.server.client()

    def raw(
        self,
        creds: Creds,
        method: str,
        path: str,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        payload_hash: str | None = None,
    ) -> httpx.Response:
        """A request signed by botocore with exactly the given headers."""
        request = AWSRequest(method=method, url=self.proxy_url + path, data=body)
        for name, value in (headers or {}).items():
            request.headers[name] = value
        credentials = Credentials(creds.access_key_id, creds.secret_access_key)
        PayloadAuth(credentials, payload_hash).add_auth(request)
        prepared = request.prepare()
        return httpx.request(
            method, prepared.url, headers=dict(prepared.headers.items()), content=body
        )

    def storage_options(self, creds: Creds) -> dict[str, str]:
        """Lance ``storage_options`` reaching buckets through the proxy."""
        return {
            "aws_endpoint": self.proxy_url,
            "aws_access_key_id": creds.access_key_id,
            "aws_secret_access_key": creds.secret_access_key,
            "aws_region": "us-east-1",
            "allow_http": "true",
        }


def make_env(flocks: Flocks, server: S3Server) -> S3Env:
    flock = flocks.s3(server)
    assert flock.client().post("/api/v1/orgs", {"name": "lab"}).status == 201
    env = S3Env(flock, server)
    for name, role, org in [
        ("alice", "member", "flock"),
        ("bob", "member", "flock"),
        ("carol", "admin", "flock"),
        ("olga", "member", "lab"),
    ]:
        env.users[name] = flock.add_user(f"{name}@flock.test", role, org)
    return env


@pytest.fixture
def make_flock(postgres: Postgres, tmp_path: Path) -> Iterator[Flocks]:
    """Starts flocks on fresh databases, as in the app tests."""
    flocks = Flocks(postgres, tmp_path)
    yield flocks
    flocks.stop()


@pytest.fixture(scope="module")
def make_module_flock(
    postgres: Postgres, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Flocks]:
    flocks = Flocks(postgres, tmp_path_factory.mktemp("flock"))
    yield flocks
    flocks.stop()


@pytest.fixture(scope="module")
def env(make_module_flock: Flocks, s3_server: S3Server) -> S3Env:
    """One S3 flock per module; tests make buckets of their own."""
    return make_env(make_module_flock, s3_server)


@pytest.fixture
def wait_for() -> Callable[..., Any]:
    def wait(predicate: Callable[[], Any], timeout: float = 30.0, interval: float = 0.05) -> Any:
        deadline = time.monotonic() + timeout
        while not (value := predicate()):
            if time.monotonic() > deadline:
                raise AssertionError("timed out waiting for condition")
            time.sleep(interval)
        return value

    return wait
