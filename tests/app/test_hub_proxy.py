import asyncio
import hashlib
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
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

from app.api.src import hub_credentials
from app.api.src.hub_proxy.app import manifest_version
from tests.app.conftest import Flocks, HubFlock, ProxyServer
from tests.app.test_hub_chunked import crc32, frame
from tests.conftest import S3Bucket

lance = pytest.importorskip("lance")
pa = pytest.importorskip("pyarrow")


class PayloadAuth(S3SigV4Auth):
    """botocore's signer, declaring ``payload_hash`` (if given) as x-amz-content-sha256."""

    def __init__(self, credentials: Credentials, payload_hash: str | None) -> None:
        super().__init__(credentials, "s3", "us-east-1")
        self.payload_hash = payload_hash

    def payload(self, request):
        return self.payload_hash or super().payload(request)


@dataclass(frozen=True)
class Creds:
    access_key_id: str
    secret_access_key: str
    session_token: str | None = None


@dataclass
class Hub:
    """A flock, its database, the proxy in front of a test bucket, and helpers to set up
    datasets and credentials directly in Postgres (the datasets API is built separately)."""

    flock: HubFlock
    proxy: ProxyServer
    bucket: S3Bucket
    database_url: str
    server_secret: str
    users: dict[str, int]  # name -> user pk
    orgs: dict[str, int]

    def sql(self, query: str, *args: Any) -> list[asyncpg.Record]:
        async def run() -> list[asyncpg.Record]:
            conn = await asyncpg.connect(self.database_url)
            try:
                return await conn.fetch(query, *args)
            finally:
                await conn.close()

        return asyncio.run(run())

    def invalidate(self) -> None:
        """As the API does after changing keys, memberships or datasets."""
        self.sql("NOTIFY hub_invalidate")

    def dataset(self, name: str | None = None, org: str = "flock", visibility="private"):
        """A new dataset; returns its name and uuid."""
        name = name or f"ds-{uuid.uuid4().hex[:8]}"
        rows = self.sql(
            """
            INSERT INTO datasets (uuid, org_pk, name, visibility, created_at, updated_at)
            VALUES (gen_random_uuid(), $1, $2, $3, now(), now()) RETURNING uuid
            """,
            self.orgs[org],
            name,
            visibility,
        )
        return name, str(rows[0]["uuid"])

    def key(self, user: str, read_only: bool = False, org: str | None = None) -> Creds:
        akid = hub_credentials.new_access_key_id(hub_credentials.KEY_PREFIX)
        self.sql(
            """
            INSERT INTO hub_keys (user_pk, name, access_key_id, read_only, org_pk, created_at)
            VALUES ($1, 'tests', $2, $3, $4, now())
            """,
            self.users[user],
            akid,
            read_only,
            self.orgs[org] if org else None,
        )
        return Creds(akid, hub_credentials.secret_for(self.server_secret, akid))

    def temp(
        self, user: str, read_only: bool = False, ttl: float = 3600, mirror: bool = False
    ) -> Creds:
        akid = hub_credentials.new_access_key_id(hub_credentials.TEMP_PREFIX)
        session = hub_credentials.Session(
            self.users[user], time.time() + ttl, read_only, akid, mirror
        )
        return Creds(
            akid,
            hub_credentials.secret_for(self.server_secret, akid),
            hub_credentials.make_session_token(self.server_secret, session),
        )

    def s3(self, creds: Creds):
        """A boto3 client signing with ``creds`` against the proxy."""
        return boto3.client(
            "s3",
            endpoint_url=self.proxy.url,
            aws_access_key_id=creds.access_key_id,
            aws_secret_access_key=creds.secret_access_key,
            aws_session_token=creds.session_token,
            region_name="us-east-1",
            config=Config(
                s3={"addressing_style": "path"},
                signature_version="s3v4",  # presigned URLs default to SigV2 otherwise
                retries={"max_attempts": 1},
            ),
        )

    def storage_options(self, creds: Creds) -> dict[str, str]:
        options = {
            "aws_endpoint": self.proxy.url,
            "aws_access_key_id": creds.access_key_id,
            "aws_secret_access_key": creds.secret_access_key,
            "aws_region": "us-east-1",
            "allow_http": "true",
        }
        if creds.session_token:
            options["aws_session_token"] = creds.session_token
        return options

    def raw(
        self,
        creds: Creds,
        method: str,
        path: str,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        payload_hash: str | None = None,
    ) -> httpx.Response:
        """A request signed by botocore with exactly the given headers (boto3's operations
        don't let tests send, say, a manifest PUT without ``If-None-Match``)."""
        request = AWSRequest(method=method, url=self.proxy.url + path, data=body)
        for name, value in (headers or {}).items():
            request.headers[name] = value
        credentials = Credentials(creds.access_key_id, creds.secret_access_key, creds.session_token)
        PayloadAuth(credentials, payload_hash).add_auth(request)
        prepared = request.prepare()
        return httpx.request(
            method, prepared.url, headers=dict(prepared.headers.items()), content=body
        )

    def upstream_keys(self, dataset_uuid: str) -> list[str]:
        paginator = self.bucket.client().get_paginator("list_objects_v2")
        prefix = f"datasets/{dataset_uuid}/"
        return [
            obj["Key"][len(prefix) :]
            for page in paginator.paginate(Bucket=self.bucket.bucket, Prefix=prefix)
            for obj in page.get("Contents", [])
        ]


@pytest.fixture(scope="module")
def hub(make_module_flock: Flocks, s3_server) -> Hub:
    flock = make_module_flock.hub(s3_server.bucket())
    admin = flock.client()
    assert admin.post("/api/v1/orgs", {"name": "lab"}).status == 201
    for email, role, org in [
        ("alice@flock.test", "member", "flock"),
        ("bob@flock.test", "member", "flock"),
        ("carol@flock.test", "admin", "flock"),
        ("olga@flock.test", "member", "lab"),
    ]:
        flock.add_user(email, role, org)
    hub = Hub(flock, flock.proxy, flock.bucket, flock.database_url, "", {}, {})
    hub.server_secret = hub.sql("SELECT value FROM meta WHERE key = 'hub_key_secret'")[0]["value"]
    for row in hub.sql("SELECT pk, email FROM users"):
        hub.users[row["email"].partition("@")[0]] = row["pk"]
    for row in hub.sql("SELECT pk, name FROM orgs"):
        hub.orgs[row["name"]] = row["pk"]
    return hub


def rows_table(start: int, n: int, writer: str = "") -> Any:
    return pa.table(
        {
            "id": pa.array(range(start, start + n), pa.int64()),
            "text": [f"row {i}" for i in range(start, start + n)],
            "writer": [writer] * n,
        }
    )


def commits(hub: Hub, dataset_uuid: str) -> list[tuple[int, str]]:
    rows = hub.sql(
        """
        SELECT c.version, split_part(u.email, '@', 1) AS who
        FROM dataset_commits c JOIN datasets d ON d.pk = c.dataset_pk
        LEFT JOIN users u ON u.pk = c.user_pk
        WHERE d.uuid = $1 ORDER BY c.version
        """,
        uuid.UUID(dataset_uuid),
    )
    return [(row["version"], row["who"]) for row in rows]


# ---------------------------------------------------------------------- Lance end to end


def test_lance_round_trip_through_the_proxy(hub):
    name, ds_uuid = hub.dataset()
    uri = f"s3://datasets/flock/{name}"
    alice = hub.storage_options(hub.temp("alice"))
    lance.write_dataset(rows_table(0, 100, "alice"), uri, storage_options=alice)
    bob = hub.storage_options(hub.key("bob"))
    lance.write_dataset(rows_table(100, 50, "bob"), uri, mode="append", storage_options=bob)

    ds = lance.dataset(uri, storage_options=bob)
    assert ds.version == 2
    assert ds.count_rows() == 150
    assert ds.take([0, 149]).to_pydict()["id"] == [0, 149]
    assert sorted(ds.to_table(columns=["id"])["id"].to_pylist()) == list(range(150))
    ds.tags.create("v1.0", 1)
    assert lance.dataset(uri, storage_options=alice).tags.list()["v1.0"]["version"] == 1
    assert lance.dataset(uri, version="v1.0", storage_options=alice).count_rows() == 100
    assert [v["version"] for v in ds.versions()] == [1, 2]
    # Members may move and delete tags; they are labels, not history.
    lance.dataset(uri, storage_options=alice).tags.delete("v1.0")
    assert lance.dataset(uri, storage_options=bob).tags.list() == {}
    ds.tags.create("v1.0", 1)

    assert commits(hub, ds_uuid) == [(1, "alice"), (2, "bob")]
    # Upstream, everything lives under the dataset's uuid.
    keys = hub.upstream_keys(ds_uuid)
    assert any(k.startswith("_versions/") for k in keys)
    assert "_refs/tags/v1.0.json" in keys


def test_manifest_names():
    assert manifest_version("1.manifest") == 1
    assert manifest_version("18446744073709551614.manifest") == 1
    assert manifest_version(f"{2**64 - 1 - 42:020}.manifest") == 42
    assert manifest_version("x.manifest") is None
    assert manifest_version("0.manifest") is None
    assert manifest_version("1.txn") is None


def code(error: ClientError) -> str:
    return error.response["Error"]["Code"]


def status(error: ClientError) -> int:
    return error.response["ResponseMetadata"]["HTTPStatusCode"]


# ---------------------------------------------------------------------- signatures and keys


@pytest.mark.parametrize("kind", ["key", "temp"])
def test_boto3_header_and_presigned_requests(hub, kind):
    name, _ = hub.dataset()
    creds = hub.key("alice") if kind == "key" else hub.temp("alice")
    s3 = hub.s3(creds)
    key = f"flock/{name}/data/a b+c=d.txt"
    s3.put_object(Bucket="datasets", Key=key, Body=b"hello world", ContentType="text/plain")
    got = s3.get_object(Bucket="datasets", Key=key)
    assert got["Body"].read() == b"hello world"
    assert got["ContentType"] == "text/plain"
    assert s3.head_object(Bucket="datasets", Key=key)["ContentLength"] == 11

    url = s3.generate_presigned_url("get_object", Params={"Bucket": "datasets", "Key": key})
    assert httpx.get(url).content == b"hello world"
    # The presigned URL covers exactly that object.
    other = url.replace("a%20b", "a%20c")
    assert httpx.get(other).status_code == 403

    put = s3.generate_presigned_url(
        "put_object", Params={"Bucket": "datasets", "Key": key + "2"}, ExpiresIn=60
    )
    assert httpx.put(put, content=b"presigned").status_code == 200
    assert s3.get_object(Bucket="datasets", Key=key + "2")["Body"].read() == b"presigned"


def test_bad_credentials_are_refused(hub):
    name, _ = hub.dataset()
    key = f"flock/{name}/x"
    good = hub.key("alice")
    hub.s3(good).put_object(Bucket="datasets", Key=key, Body=b"x")

    def refused(creds: Creds) -> ClientError:
        with pytest.raises(ClientError) as error:
            hub.s3(creds).get_object(Bucket="datasets", Key=key)
        return error.value

    assert code(refused(Creds(good.access_key_id, "wrong"))) == "SignatureDoesNotMatch"
    assert code(refused(Creds("FLKHNOSUCHKEYNOSUCHK", "x"))) == "InvalidAccessKeyId"
    assert code(refused(Creds("AKIAIOSFODNN7EXAMPLE", "x"))) == "InvalidAccessKeyId"
    expired = hub.temp("alice", ttl=-1)
    assert code(refused(expired)) == "InvalidToken"
    # A valid token belongs to one access key id only.
    first, second = hub.temp("alice"), hub.temp("alice")
    swapped = Creds(first.access_key_id, first.secret_access_key, second.session_token)
    assert code(refused(swapped)) == "InvalidToken"
    no_token = Creds(first.access_key_id, first.secret_access_key)
    assert code(refused(no_token)) == "InvalidToken"
    # A token signed with another server secret is forged.
    forged = hub_credentials.make_session_token(
        "not-the-secret",
        hub_credentials.Session(hub.users["alice"], time.time() + 60, False, first.access_key_id),
    )
    forged_creds = Creds(first.access_key_id, first.secret_access_key, forged)
    assert code(refused(forged_creds)) == "InvalidToken"
    # Deleting a key revokes it (the API notifies the proxy).
    revoked = hub.key("alice")
    hub.s3(revoked).get_object(Bucket="datasets", Key=key)
    hub.sql("DELETE FROM hub_keys WHERE access_key_id = $1", revoked.access_key_id)
    hub.invalidate()
    assert code(refused(revoked)) == "InvalidAccessKeyId"


def test_read_only_credentials_cannot_write(hub):
    name, _ = hub.dataset()
    key = f"flock/{name}/x"
    hub.s3(hub.key("alice")).put_object(Bucket="datasets", Key=key, Body=b"x")
    for creds in (hub.key("alice", read_only=True), hub.temp("alice", read_only=True)):
        s3 = hub.s3(creds)
        assert s3.get_object(Bucket="datasets", Key=key)["Body"].read() == b"x"
        with pytest.raises(ClientError) as error:
            s3.put_object(Bucket="datasets", Key=key, Body=b"y")
        assert code(error.value) == "AccessDenied"
        with pytest.raises(ClientError):
            s3.create_multipart_upload(Bucket="datasets", Key=key + "-mp")


def test_last_used_is_recorded_once_a_minute(hub, wait_for):
    name, _ = hub.dataset()
    creds = hub.key("alice")
    s3 = hub.s3(creds)

    def last_used():
        rows = hub.sql(
            "SELECT last_used_at FROM hub_keys WHERE access_key_id = $1", creds.access_key_id
        )
        return rows[0]["last_used_at"]

    assert last_used() is None
    s3.put_object(Bucket="datasets", Key=f"flock/{name}/x", Body=b"x")
    first = wait_for(last_used)
    s3.get_object(Bucket="datasets", Key=f"flock/{name}/x")
    time.sleep(0.2)
    assert last_used() == first


def test_unsupported_requests(hub):
    name, _ = hub.dataset()
    creds = hub.key("alice")
    chunked = hub.raw(
        creds,
        "PUT",
        f"/datasets/flock/{name}/x",
        b"x",
        payload_hash="STREAMING-AWS4-HMAC-SHA256-PAYLOAD",
    )
    assert chunked.status_code == 501
    assert b"<Code>NotImplemented</Code>" in chunked.content
    s3 = hub.s3(creds)
    for call in (
        lambda: s3.list_objects(Bucket="datasets", Prefix=f"flock/{name}/"),
        lambda: s3.get_object_acl(Bucket="datasets", Key=f"flock/{name}/x"),
        lambda: s3.put_object_acl(Bucket="datasets", Key=f"flock/{name}/x", ACL="public-read"),
    ):
        with pytest.raises(ClientError) as error:
            call()
        assert status(error.value) == 501, error.value.response
    with pytest.raises(ClientError) as error:
        s3.head_bucket(Bucket="other")
    assert status(error.value) == 404
    assert [b["Name"] for b in s3.list_buckets()["Buckets"]] == ["datasets"]


# ---------------------------------------------------------------------- permissions


def test_permission_matrix(hub):
    private, _ = hub.dataset()
    internal, _ = hub.dataset(visibility="internal")
    lab, _ = hub.dataset(org="lab")
    seed = hub.s3(hub.key("admin"))
    for org, name in (("flock", private), ("flock", internal), ("lab", lab)):
        seed.put_object(Bucket="datasets", Key=f"{org}/{name}/x", Body=b"x")

    def can(creds: Creds, org: str, name: str) -> tuple[bool, bool]:
        s3 = hub.s3(creds)
        try:
            s3.get_object(Bucket="datasets", Key=f"{org}/{name}/x")
            read = True
        except ClientError as e:
            assert code(e) == "AccessDenied"
            read = False
        try:
            s3.put_object(Bucket="datasets", Key=f"{org}/{name}/y", Body=b"y")
            write = True
        except ClientError as e:
            assert code(e) == "AccessDenied"
            write = False
        return read, write

    olga = hub.key("olga")
    assert can(olga, "flock", private) == (False, False)
    assert can(olga, "flock", internal) == (True, False)
    assert can(olga, "lab", lab) == (True, True)
    alice = hub.temp("alice")
    assert can(alice, "flock", private) == (True, True)
    assert can(alice, "lab", lab) == (False, False)
    # A key scoped to an org reaches nothing else, internal datasets included.
    scoped = hub.key("admin", org="lab")
    assert can(scoped, "lab", lab) == (True, True)
    assert can(scoped, "flock", private) == (False, False)
    assert can(scoped, "flock", internal) == (False, False)
    # Super admins act as admins everywhere.
    assert can(hub.key("admin"), "lab", lab) == (True, True)
    # Missing and forbidden datasets look the same.
    assert can(olga, "flock", "no-such-dataset") == (False, False)
    assert can(alice, "nope", "nothing") == (False, False)

    # A deleted dataset is gone, even though its objects are still upstream.
    hub.sql("UPDATE datasets SET deleted_at = now() WHERE name = $1", private)
    hub.invalidate()
    assert can(alice, "flock", private) == (False, False)

    # Roles are checked live: removing a member takes effect once the API notifies.
    removable, _ = hub.dataset()
    seed.put_object(Bucket="datasets", Key=f"flock/{removable}/x", Body=b"x")
    bob = hub.key("bob")
    assert can(bob, "flock", removable) == (True, True)
    role = hub.sql("SELECT role FROM memberships WHERE user_pk = $1", hub.users["bob"])[0]
    hub.sql("DELETE FROM memberships WHERE user_pk = $1", hub.users["bob"])
    hub.invalidate()
    try:
        assert can(bob, "flock", removable) == (False, False)
    finally:
        hub.sql(
            "INSERT INTO memberships (user_pk, org_pk, role, created_at) "
            "VALUES ($1, $2, $3, now())",
            hub.users["bob"],
            hub.orgs["flock"],
            role["role"],
        )
        hub.invalidate()


def test_mirror_writer_writes_to_the_mirror_org(hub):
    mirror, _ = hub.dataset(org="hf", visibility="internal")
    key = f"hf/{mirror}/x"
    with pytest.raises(ClientError):
        hub.s3(hub.temp("alice")).put_object(Bucket="datasets", Key=key, Body=b"x")
    hub.s3(hub.temp("alice", mirror=True)).put_object(Bucket="datasets", Key=key, Body=b"x")
    assert hub.s3(hub.key("olga")).get_object(Bucket="datasets", Key=key)["Body"].read() == b"x"
    # It grants nothing elsewhere.
    lab, _ = hub.dataset(org="lab")
    with pytest.raises(ClientError):
        hub.s3(hub.temp("alice", mirror=True)).put_object(
            Bucket="datasets", Key=f"lab/{lab}/x", Body=b"x"
        )


def test_history_cannot_be_rewritten(hub):
    name, ds_uuid = hub.dataset()
    uri = f"s3://datasets/flock/{name}"
    alice = hub.key("alice")
    lance.write_dataset(rows_table(0, 10), uri, storage_options=hub.storage_options(alice))
    keys = hub.upstream_keys(ds_uuid)
    manifest = next(k for k in keys if k.startswith("_versions/"))
    transaction = next(k for k in keys if k.startswith("_transactions/"))
    base = f"/datasets/flock/{name}/"

    # Manifests need If-None-Match: * (D45), and existing ones can't be replaced.
    next_manifest = f"_versions/{2**64 - 1 - 2:020}.manifest"
    assert hub.raw(alice, "PUT", base + next_manifest, b"m").status_code == 403
    taken = hub.raw(alice, "PUT", base + manifest, b"m", {"If-None-Match": "*"})
    assert taken.status_code == 412
    assert b"PreconditionFailed" in taken.content
    for creds in (alice, hub.key("admin")):
        response = hub.raw(creds, "PUT", base + transaction, b"t")
        assert response.status_code == 403, response.content
    # Nor by copying or a multipart upload onto them.
    s3 = hub.s3(alice)
    with pytest.raises(ClientError) as error:
        s3.copy_object(
            Bucket="datasets",
            Key=f"flock/{name}/{transaction}",
            CopySource={"Bucket": "datasets", "Key": f"flock/{name}/{manifest}"},
        )
    assert code(error.value) == "AccessDenied"
    with pytest.raises(ClientError):
        s3.create_multipart_upload(Bucket="datasets", Key=f"flock/{name}/{next_manifest}")
    # New transaction files are fine.
    assert hub.raw(alice, "PUT", base + "_transactions/new.txn", b"t").status_code == 200
    # Until a version refers to it, a transaction file is its writer's to replace or delete:
    # Lance deletes and rewrites its own after losing a commit race.
    pending = base + f"_transactions/1-{uuid.uuid4()}.txn"
    assert hub.raw(alice, "PUT", pending, b"t").status_code == 200
    assert hub.raw(alice, "PUT", pending, b"t2").status_code == 200
    assert hub.raw(alice, "DELETE", pending).status_code == 204
    assert hub.raw(alice, "DELETE", base + transaction).status_code == 403
    assert lance.dataset(uri, storage_options=hub.storage_options(alice)).count_rows() == 10
    assert commits(hub, ds_uuid) == [(1, "alice")]


def test_only_admins_delete(hub):
    name, _ = hub.dataset()
    key = f"flock/{name}/x"
    member = hub.s3(hub.key("alice"))
    member.put_object(Bucket="datasets", Key=key, Body=b"x")
    member.put_object(Bucket="datasets", Key=key + "2", Body=b"x")
    with pytest.raises(ClientError) as error:
        member.delete_object(Bucket="datasets", Key=key)
    assert code(error.value) == "AccessDenied"
    with pytest.raises(ClientError):
        member.delete_objects(Bucket="datasets", Delete={"Objects": [{"Key": key}]})
    # Not even with an admin's read-only key.
    with pytest.raises(ClientError):
        hub.s3(hub.key("carol", read_only=True)).delete_object(Bucket="datasets", Key=key)

    admin = hub.s3(hub.key("carol"))
    admin.delete_object(Bucket="datasets", Key=key)
    with pytest.raises(ClientError) as error:
        member.head_object(Bucket="datasets", Key=key)
    assert status(error.value) == 404
    result = admin.delete_objects(
        Bucket="datasets", Delete={"Objects": [{"Key": key + "2"}, {"Key": key + "3"}]}
    )
    assert sorted(d["Key"] for d in result["Deleted"]) == [key + "2", key + "3"]
    assert "Contents" not in member.list_objects_v2(Bucket="datasets", Prefix=f"flock/{name}/")
    # Keys outside datasets the caller administers spoil the whole request.
    other, _ = hub.dataset(org="lab")
    with pytest.raises(ClientError):
        admin.delete_objects(Bucket="datasets", Delete={"Objects": [{"Key": f"lab/{other}/x"}]})


def test_listing_above_a_dataset_is_refused(hub):
    name, _ = hub.dataset()
    s3 = hub.s3(hub.key("admin"))
    s3.put_object(Bucket="datasets", Key=f"flock/{name}/x", Body=b"x")
    for prefix in ("", "flock/", f"flock/{name}", "flock"):
        with pytest.raises(ClientError) as error:
            s3.list_objects_v2(Bucket="datasets", Prefix=prefix)
        assert code(error.value) == "AccessDenied", prefix
    listed = s3.list_objects_v2(Bucket="datasets", Prefix=f"flock/{name}/")
    assert [o["Key"] for o in listed["Contents"]] == [f"flock/{name}/x"]


def test_renamed_dataset_moves_with_its_objects(hub):
    name, ds_uuid = hub.dataset()
    uri = f"s3://datasets/flock/{name}"
    opts = hub.storage_options(hub.key("alice"))
    lance.write_dataset(rows_table(0, 10), uri, storage_options=opts)
    renamed = name + "-renamed"
    hub.sql("UPDATE datasets SET name = $1 WHERE uuid = $2", renamed, uuid.UUID(ds_uuid))
    hub.invalidate()
    with pytest.raises((OSError, ValueError)):
        lance.dataset(uri, storage_options=opts)
    moved = lance.dataset(f"s3://datasets/flock/{renamed}", storage_options=opts)
    assert moved.count_rows() == 10
    lance.write_dataset(
        rows_table(10, 5), f"s3://datasets/flock/{renamed}", mode="append", storage_options=opts
    )
    assert commits(hub, ds_uuid) == [(1, "alice"), (2, "alice")]
    # A new dataset under the old name starts empty.
    hub.dataset(name)
    with pytest.raises((OSError, ValueError)):
        lance.dataset(uri, storage_options=opts).count_rows()


# ---------------------------------------------------------------------- S3 operations


def test_aws_chunked_uploads_are_decoded(hub):
    """Recent AWS SDKs send uploads aws-chunked with a checksum trailer by default."""
    name, ds_uuid = hub.dataset()
    alice = hub.key("alice")
    data = bytes(range(256)) * 300
    headers = {
        "Content-Encoding": "aws-chunked",
        "X-Amz-Decoded-Content-Length": str(len(data)),
        "X-Amz-Trailer": "x-amz-checksum-crc32",
        "X-Amz-Sdk-Checksum-Algorithm": "CRC32",
    }
    path = f"/datasets/flock/{name}/chunked.bin"
    put = hub.raw(
        alice,
        "PUT",
        path,
        frame(data, trailer=crc32(data)),
        headers,
        "STREAMING-UNSIGNED-PAYLOAD-TRAILER",
    )
    assert put.status_code == 200, put.text
    assert hub.s3(alice).get_object(Bucket="datasets", Key=path[10:])["Body"].read() == data
    assert "chunked.bin" in hub.upstream_keys(ds_uuid)

    # A body that fails its checksum is refused and never stored.
    bad = hub.raw(
        alice,
        "PUT",
        path + ".bad",
        frame(data, trailer=crc32(b"other")),
        headers,
        "STREAMING-UNSIGNED-PAYLOAD-TRAILER",
    )
    assert bad.status_code == 400
    assert "chunked.bin.bad" not in hub.upstream_keys(ds_uuid)

    # Signed chunks would need each chunk's signature checked.
    signed = hub.raw(alice, "PUT", path, frame(data), headers, "STREAMING-AWS4-HMAC-SHA256-PAYLOAD")
    assert signed.status_code == 501


def test_multipart_upload_round_trip(hub):
    name, ds_uuid = hub.dataset()
    s3 = hub.s3(hub.key("alice"))
    key = f"flock/{name}/data/big.bin"
    # A small last part on purpose: S3 only requires the other parts to be at least 5 MiB.
    parts_data = [bytes([i]) * (5 * 1024 * 1024) for i in range(2)] + [b"tail"]
    upload = s3.create_multipart_upload(Bucket="datasets", Key=key)
    assert upload["Bucket"] == "datasets" and upload["Key"] == key
    parts = []
    for number, data in enumerate(parts_data, 1):
        etag = s3.upload_part(
            Bucket="datasets", Key=key, UploadId=upload["UploadId"], PartNumber=number, Body=data
        )["ETag"]
        parts.append({"PartNumber": number, "ETag": etag})
    listed = s3.list_parts(Bucket="datasets", Key=key, UploadId=upload["UploadId"])
    assert listed["Key"] == key
    assert [p["PartNumber"] for p in listed["Parts"]] == [1, 2, 3]
    done = s3.complete_multipart_upload(
        Bucket="datasets", Key=key, UploadId=upload["UploadId"], MultipartUpload={"Parts": parts}
    )
    assert done["Key"] == key and done["Bucket"] == "datasets"
    direct = (
        hub.bucket.client()
        .get_object(Bucket=hub.bucket.bucket, Key=f"datasets/{ds_uuid}/data/big.bin")["Body"]
        .read()
    )
    body = s3.get_object(Bucket="datasets", Key=key)["Body"].read()
    assert body == direct == b"".join(parts_data)
    assert "data/big.bin" in hub.upstream_keys(ds_uuid)

    aborted = s3.create_multipart_upload(Bucket="datasets", Key=key + ".2")
    s3.abort_multipart_upload(Bucket="datasets", Key=key + ".2", UploadId=aborted["UploadId"])


def test_ranges_and_conditional_reads(hub):
    name, _ = hub.dataset()
    s3 = hub.s3(hub.key("alice"))
    key = f"flock/{name}/data.bin"
    data = bytes(range(256)) * 64
    s3.put_object(Bucket="datasets", Key=key, Body=data)
    got = s3.get_object(Bucket="datasets", Key=key, Range="bytes=1000-1099")
    assert got["Body"].read() == data[1000:1100]
    assert got["ContentRange"] == f"bytes 1000-1099/{len(data)}"
    assert got["ResponseMetadata"]["HTTPStatusCode"] == 206
    assert got["ContentLength"] == 100
    assert got["AcceptRanges"] == "bytes"
    etag = got["ETag"]
    with pytest.raises(ClientError) as error:
        s3.get_object(Bucket="datasets", Key=key, IfNoneMatch=etag)
    assert status(error.value) == 304
    with pytest.raises(ClientError) as error:
        s3.get_object(Bucket="datasets", Key=key, IfMatch='"nope"')
    assert status(error.value) == 412
    with pytest.raises(ClientError) as error:
        s3.get_object(Bucket="datasets", Key=key + ".missing")
    assert code(error.value) == "NoSuchKey"


def test_list_objects_pagination_keeps_order_and_times(hub):
    name, ds_uuid = hub.dataset()
    s3 = hub.s3(hub.key("alice"))
    root = f"flock/{name}/"
    names = sorted(
        [f"a/{i:02}" for i in range(7)] + ["b", "c/x y+z", "c/ü", "d/e/f", "_versions/1"]
    )
    for n in names:
        s3.put_object(Bucket="datasets", Key=root + n, Body=n.encode())
    keys, pages, token = [], 0, None
    while True:
        kwargs = {"ContinuationToken": token} if token else {}
        page = s3.list_objects_v2(Bucket="datasets", Prefix=root, MaxKeys=3, **kwargs)
        assert page["Prefix"] == root and page["Name"] == "datasets"
        keys += [o["Key"] for o in page["Contents"]]
        pages += 1
        if not page["IsTruncated"]:
            break
        token = page["NextContinuationToken"]
        assert ds_uuid not in token
    assert keys == [root + n for n in names]
    assert pages == 4

    upstream = {
        o["Key"]: o["LastModified"]
        for o in hub.bucket.client().list_objects_v2(
            Bucket=hub.bucket.bucket, Prefix=f"datasets/{ds_uuid}/"
        )["Contents"]
    }
    proxied = s3.list_objects_v2(Bucket="datasets", Prefix=root)["Contents"]
    assert {o["Key"]: o["LastModified"] for o in proxied} == {
        root + k.split("/", 2)[2]: t for k, t in upstream.items()
    }

    delimited = s3.list_objects_v2(Bucket="datasets", Prefix=root, Delimiter="/")
    assert [p["Prefix"] for p in delimited["CommonPrefixes"]] == [
        root + p for p in ("_versions/", "a/", "c/", "d/")
    ]
    assert [o["Key"] for o in delimited["Contents"]] == [root + "b"]
    after = s3.list_objects_v2(Bucket="datasets", Prefix=root + "a/", StartAfter=root + "a/04")
    assert [o["Key"] for o in after["Contents"]] == [root + "a/05", root + "a/06"]


def test_copy_object_within_a_dataset(hub):
    name, _ = hub.dataset()
    other, _ = hub.dataset()
    s3 = hub.s3(hub.key("alice"))
    s3.put_object(Bucket="datasets", Key=f"flock/{name}/a", Body=b"copy me")
    copied = s3.copy_object(
        Bucket="datasets", Key=f"flock/{name}/b", CopySource=f"datasets/flock/{name}/a"
    )
    assert copied["CopyObjectResult"]["ETag"]
    assert s3.get_object(Bucket="datasets", Key=f"flock/{name}/b")["Body"].read() == b"copy me"
    with pytest.raises(ClientError) as error:
        s3.copy_object(
            Bucket="datasets", Key=f"flock/{other}/b", CopySource=f"datasets/flock/{name}/a"
        )
    assert status(error.value) == 501


# ---------------------------------------------------------------------- commits (D45)

N_WRITERS = 8


def concurrent_appends(uri: str, options: list[dict[str, str]], writers: list[str]):
    """Append one batch per writer at the same time; returns the errors."""
    barrier = threading.Barrier(len(options))

    def append(i: int) -> None:
        barrier.wait()
        lance.write_dataset(
            rows_table(1000 * (i + 1), 10, writers[i]),
            uri,
            mode="append",
            storage_options=options[i],
        )

    with ThreadPoolExecutor(len(options)) as pool:
        futures = [pool.submit(append, i) for i in range(len(options))]
        return [f.exception() for f in futures if f.exception() is not None]


def test_concurrent_appends_through_the_proxy_all_land(hub):
    name, ds_uuid = hub.dataset()
    uri = f"s3://datasets/flock/{name}"
    admin = hub.storage_options(hub.key("admin"))
    lance.write_dataset(rows_table(0, 10, "admin"), uri, storage_options=admin)
    writers = [["alice", "bob", "carol"][i % 3] for i in range(N_WRITERS)]
    options = [hub.storage_options(hub.temp(w)) for w in writers]
    assert concurrent_appends(uri, options, writers) == []

    ds = lance.dataset(uri, storage_options=admin)
    assert ds.version == N_WRITERS + 1
    ids = sorted(ds.to_table(columns=["id"])["id"].to_pylist())
    expected = list(range(10)) + [1000 * (i + 1) + j for i in range(N_WRITERS) for j in range(10)]
    assert ids == expected
    # One commit row per version, each naming the writer whose rows that version added.
    recorded = commits(hub, ds_uuid)
    assert [v for v, _ in recorded] == list(range(1, N_WRITERS + 2))
    assert recorded[0] == (1, "admin")
    for version, who in recorded[1:]:
        before = lance.dataset(uri, version=version - 1, storage_options=admin).count_rows()
        after = lance.dataset(uri, version=version, storage_options=admin)
        added = after.to_table(columns=["writer"])["writer"].to_pylist()[before:]
        assert set(added) == {who}, version
    assert sorted(w for _, w in recorded[1:]) == sorted(writers)


def test_racing_manifest_puts_commit_once(hub):
    name, ds_uuid = hub.dataset()
    creds = [hub.key("alice"), hub.key("bob")]
    manifest = f"_versions/{2**64 - 1 - 1:020}.manifest"
    path = f"/datasets/flock/{name}/{manifest}"
    racers = 8
    barrier = threading.Barrier(racers)

    def put(i: int) -> int:
        barrier.wait()
        response = hub.raw(creds[i % 2], "PUT", path, f"m{i}".encode(), {"If-None-Match": "*"})
        return response.status_code

    with ThreadPoolExecutor(racers) as pool:
        statuses = list(pool.map(put, range(racers)))
    assert sorted(statuses) == [200] + [412] * (racers - 1)
    winner = statuses.index(200)
    assert commits(hub, ds_uuid) == [(1, ["alice", "bob"][winner % 2])]
    body = hub.bucket.client().get_object(
        Bucket=hub.bucket.bucket, Key=f"datasets/{ds_uuid}/{manifest}"
    )["Body"]
    assert body.read() == f"m{winner}".encode()
    # Buffered bodies are checked against their declared hash.
    bad = hub.raw(
        creds[0],
        "PUT",
        f"/datasets/flock/{name}/_versions/{2**64 - 1 - 2:020}.manifest",
        b"m",
        {"If-None-Match": "*"},
        payload_hash=hashlib.sha256(b"other").hexdigest(),
    )
    assert bad.status_code == 400
    assert len(commits(hub, ds_uuid)) == 1
