"""The proxy's S3 operations (D61), through boto3, raw signed requests, the AWS CLI and Lance."""

import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote

import httpx
import pytest
from botocore.exceptions import ClientError

from tests.s3.conftest import Creds, S3Env, code, status
from tests.s3.test_chunked import crc32, frame


def test_objects_round_trip(env: S3Env):
    bucket = env.bucket()
    s3 = env.s3(env.key("alice"))
    key = "run-1/a b+c=d/ü.txt"
    s3.put_object(Bucket=bucket, Key=key, Body=b"hello world", ContentType="text/plain",
                  Metadata={"step": "7"})  # fmt: skip
    got = s3.get_object(Bucket=bucket, Key=key)
    assert got["Body"].read() == b"hello world"
    assert got["ContentType"] == "text/plain"
    assert got["Metadata"] == {"step": "7"}
    assert s3.head_object(Bucket=bucket, Key=key)["ContentLength"] == 11
    # Upstream, the object sits in the bucket's upstream name, under the same key.
    upstream = env.upstream().get_object(Bucket=env.upstream_name(bucket), Key=key)
    assert upstream["Body"].read() == b"hello world"

    s3.delete_object(Bucket=bucket, Key=key)
    with pytest.raises(ClientError) as error:
        s3.head_object(Bucket=bucket, Key=key)
    assert status(error.value) == 404
    with pytest.raises(ClientError) as error:
        s3.get_object(Bucket=bucket, Key=key)
    assert code(error.value) == "NoSuchKey"


def test_presigned_urls(env: S3Env):
    bucket = env.bucket()
    s3 = env.s3(env.key("alice"))
    s3.put_object(Bucket=bucket, Key="a b", Body=b"presigned")
    url = s3.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": "a b"})
    assert httpx.get(url).content == b"presigned"
    # The URL covers exactly that object.
    assert httpx.get(url.replace("a%20b", "a%20c")).status_code == 403


def test_bucket_operations(env: S3Env):
    bucket = env.bucket()
    s3 = env.s3(env.key("alice"))
    assert s3.head_bucket(Bucket=bucket)["ResponseMetadata"]["HTTPStatusCode"] == 200
    assert s3.get_bucket_location(Bucket=bucket)["LocationConstraint"] is None
    with pytest.raises(ClientError) as error:
        s3.head_bucket(Bucket=env.name("missing"))
    assert status(error.value) == 404


def test_listings_with_prefixes_and_pagination(env: S3Env):
    bucket = env.bucket()
    s3 = env.s3(env.key("alice"))
    names = sorted([f"a/{i:02}" for i in range(7)] + ["b", "c/x y+z", "c/ü", "d/e/f"])
    for name in names:
        s3.put_object(Bucket=bucket, Key=name, Body=name.encode())

    keys, pages, token = [], 0, None
    while True:
        kwargs = {"ContinuationToken": token} if token else {}
        page = s3.list_objects_v2(Bucket=bucket, MaxKeys=3, **kwargs)
        assert page["Name"] == bucket
        keys += [o["Key"] for o in page["Contents"]]
        pages += 1
        if not page["IsTruncated"]:
            break
        token = page["NextContinuationToken"]
    assert keys == names
    assert pages == 4

    delimited = s3.list_objects_v2(Bucket=bucket, Delimiter="/")
    assert [p["Prefix"] for p in delimited["CommonPrefixes"]] == ["a/", "c/", "d/"]
    assert [o["Key"] for o in delimited["Contents"]] == ["b"]
    after = s3.list_objects_v2(Bucket=bucket, Prefix="a/", StartAfter="a/04")
    assert [o["Key"] for o in after["Contents"]] == ["a/05", "a/06"]
    encoded = s3.list_objects_v2(Bucket=bucket, Prefix="c/", EncodingType="url")
    # Asked for explicitly, the encoding passes through for the caller to undo (servers differ
    # on whether they encode "/").
    keys = [o["Key"] for o in encoded["Contents"]]
    assert "c/x%20y%2Bz" in (keys[0], keys[0].replace("%2F", "/"))
    assert [unquote(k) for k in keys] == ["c/x y+z", "c/ü"]

    # ListObjects (v1), with markers.
    v1 = s3.list_objects(Bucket=bucket, Prefix="a/", MaxKeys=4)
    assert v1["Name"] == bucket
    assert [o["Key"] for o in v1["Contents"]] == ["a/00", "a/01", "a/02", "a/03"]
    assert v1["IsTruncated"]
    rest = s3.list_objects(Bucket=bucket, Prefix="a/", Marker="a/03")
    assert [o["Key"] for o in rest["Contents"]] == ["a/04", "a/05", "a/06"]


def test_aws_chunked_uploads_are_decoded(env: S3Env):
    """Recent AWS SDKs send uploads aws-chunked with a checksum trailer by default."""
    bucket = env.bucket()
    alice = env.key("alice")
    data = bytes(range(256)) * 300
    headers = {
        "Content-Encoding": "aws-chunked",
        "X-Amz-Decoded-Content-Length": str(len(data)),
        "X-Amz-Trailer": "x-amz-checksum-crc32",
        "X-Amz-Sdk-Checksum-Algorithm": "CRC32",
    }
    trailer = "STREAMING-UNSIGNED-PAYLOAD-TRAILER"
    put = env.raw(
        alice, "PUT", f"/{bucket}/chunked.bin", frame(data, trailer=crc32(data)), headers, trailer
    )
    assert put.status_code == 200, put.text
    assert env.s3(alice).get_object(Bucket=bucket, Key="chunked.bin")["Body"].read() == data

    # A body that fails its checksum is refused and never stored.
    bad = env.raw(
        alice, "PUT", f"/{bucket}/bad.bin", frame(data, trailer=crc32(b"x")), headers, trailer
    )
    assert bad.status_code == 400
    listed = env.upstream().list_objects_v2(Bucket=env.upstream_name(bucket))
    assert [o["Key"] for o in listed["Contents"]] == ["chunked.bin"]

    # Signed chunks would need each chunk's signature checked.
    signed = env.raw(
        alice, "PUT", f"/{bucket}/x", frame(data), headers, "STREAMING-AWS4-HMAC-SHA256-PAYLOAD"
    )
    assert signed.status_code == 501


def test_multipart_uploads(env: S3Env):
    bucket = env.bucket()
    s3 = env.s3(env.key("alice"))
    key = "data/big.bin"
    # A small last part on purpose: S3 only requires the other parts to be at least 5 MiB.
    parts_data = [bytes([i]) * (5 * 1024 * 1024) for i in range(2)] + [b"tail"]
    upload = s3.create_multipart_upload(Bucket=bucket, Key=key)
    assert upload["Bucket"] == bucket and upload["Key"] == key
    parts = []
    for number, data in enumerate(parts_data, 1):
        etag = s3.upload_part(
            Bucket=bucket, Key=key, UploadId=upload["UploadId"], PartNumber=number, Body=data
        )["ETag"]
        parts.append({"PartNumber": number, "ETag": etag})
    listed = s3.list_parts(Bucket=bucket, Key=key, UploadId=upload["UploadId"])
    assert listed["Bucket"] == bucket and listed["Key"] == key
    assert [p["PartNumber"] for p in listed["Parts"]] == [1, 2, 3]
    done = s3.complete_multipart_upload(
        Bucket=bucket, Key=key, UploadId=upload["UploadId"], MultipartUpload={"Parts": parts}
    )
    assert done["Key"] == key and done["Bucket"] == bucket
    assert done["Location"] == f"{env.proxy_url}/{bucket}/data/big.bin"
    assert s3.get_object(Bucket=bucket, Key=key)["Body"].read() == b"".join(parts_data)

    aborted = s3.create_multipart_upload(Bucket=bucket, Key=key + ".2")
    s3.abort_multipart_upload(Bucket=bucket, Key=key + ".2", UploadId=aborted["UploadId"])
    with pytest.raises(ClientError) as error:
        s3.list_parts(Bucket=bucket, Key=key + ".2", UploadId=aborted["UploadId"])
    assert code(error.value) in ("NoSuchUpload", "NoSuchKey")  # Ceph says NoSuchKey


def test_ranges_and_conditional_reads(env: S3Env):
    bucket = env.bucket()
    s3 = env.s3(env.key("alice"))
    data = bytes(range(256)) * 64
    s3.put_object(Bucket=bucket, Key="data.bin", Body=data)
    got = s3.get_object(Bucket=bucket, Key="data.bin", Range="bytes=1000-1099")
    assert got["Body"].read() == data[1000:1100]
    assert got["ContentRange"] == f"bytes 1000-1099/{len(data)}"
    assert got["ResponseMetadata"]["HTTPStatusCode"] == 206
    assert got["ContentLength"] == 100
    etag = got["ETag"]
    with pytest.raises(ClientError) as error:
        s3.get_object(Bucket=bucket, Key="data.bin", IfNoneMatch=etag)
    assert status(error.value) == 304
    with pytest.raises(ClientError) as error:
        s3.get_object(Bucket=bucket, Key="data.bin", IfMatch='"nope"')
    assert status(error.value) == 412


def test_if_none_match_writes_once(env: S3Env):
    """Conditional writes are forwarded, so the S3 server settles races between writers."""
    bucket = env.bucket()
    s3 = env.s3(env.key("alice"))
    s3.put_object(Bucket=bucket, Key="once", Body=b"first", IfNoneMatch="*")
    with pytest.raises(ClientError) as error:
        s3.put_object(Bucket=bucket, Key="once", Body=b"second", IfNoneMatch="*")
    assert status(error.value) == 412
    assert s3.get_object(Bucket=bucket, Key="once")["Body"].read() == b"first"

    writers = 8
    barrier = threading.Barrier(writers)
    clients = [env.s3(env.key(user)) for user in ["alice", "bob"] * (writers // 2)]

    def race(i: int) -> bool:
        barrier.wait()
        try:
            clients[i].put_object(
                Bucket=bucket, Key="race", Body=f"writer {i}".encode(), IfNoneMatch="*"
            )
        except ClientError as e:
            assert status(e) in (409, 412), e.response
            return False
        return True

    with ThreadPoolExecutor(writers) as pool:
        won = list(pool.map(race, range(writers)))
    assert won.count(True) == 1
    body = s3.get_object(Bucket=bucket, Key="race")["Body"].read()
    assert body == f"writer {won.index(True)}".encode()


def test_copies_between_the_orgs_buckets(env: S3Env):
    source, target = env.bucket(), env.bucket()
    other = env.bucket(org="lab")
    s3 = env.s3(env.key("alice"))
    s3.put_object(Bucket=source, Key="a b", Body=b"copy me" * 1000)
    copied = s3.copy_object(Bucket=source, Key="b", CopySource=f"{source}/a b")
    assert copied["CopyObjectResult"]["ETag"]
    s3.copy_object(Bucket=target, Key="c", CopySource={"Bucket": source, "Key": "a b"})
    assert s3.get_object(Bucket=target, Key="c")["Body"].read() == b"copy me" * 1000

    # UploadPartCopy.
    upload = s3.create_multipart_upload(Bucket=target, Key="parts")
    part = s3.upload_part_copy(
        Bucket=target,
        Key="parts",
        UploadId=upload["UploadId"],
        PartNumber=1,
        CopySource={"Bucket": source, "Key": "a b"},
        CopySourceRange="bytes=0-6",
    )
    s3.complete_multipart_upload(
        Bucket=target,
        Key="parts",
        UploadId=upload["UploadId"],
        MultipartUpload={"Parts": [{"PartNumber": 1, "ETag": part["CopyPartResult"]["ETag"]}]},
    )
    assert s3.get_object(Bucket=target, Key="parts")["Body"].read() == b"copy me"

    # Another org's bucket is no source, even when the name exists there.
    with pytest.raises(ClientError) as error:
        s3.copy_object(Bucket=target, Key="d", CopySource={"Bucket": other, "Key": "x"})
    assert code(error.value) == "NoSuchBucket"


def test_delete_objects(env: S3Env):
    bucket = env.bucket()
    s3 = env.s3(env.key("alice"))
    for key in ("a", "b", "c d"):
        s3.put_object(Bucket=bucket, Key=key, Body=b"x")
    deleted = s3.delete_objects(
        Bucket=bucket, Delete={"Objects": [{"Key": "a"}, {"Key": "c d"}, {"Key": "missing"}]}
    )
    assert sorted(d["Key"] for d in deleted["Deleted"]) == ["a", "c d", "missing"]
    assert [o["Key"] for o in s3.list_objects_v2(Bucket=bucket)["Contents"]] == ["b"]


def test_unsupported_requests(env: S3Env):
    bucket = env.bucket()
    s3 = env.s3(env.key("alice"))
    s3.put_object(Bucket=bucket, Key="x", Body=b"x")
    for call in (
        lambda: s3.get_object_acl(Bucket=bucket, Key="x"),
        lambda: s3.put_object_acl(Bucket=bucket, Key="x", ACL="public-read"),
        lambda: s3.get_bucket_versioning(Bucket=bucket),
        lambda: s3.list_multipart_uploads(Bucket=bucket),
    ):
        with pytest.raises(ClientError) as error:
            call()
        assert status(error.value) == 501, error.value.response
        assert code(error.value) == "NotImplemented"


def test_bad_credentials_are_refused(env: S3Env):
    bucket = env.bucket()
    good = env.key("alice")
    env.s3(good).put_object(Bucket=bucket, Key="x", Body=b"x")

    def refused(creds: Creds) -> ClientError:
        with pytest.raises(ClientError) as error:
            env.s3(creds).get_object(Bucket=bucket, Key="x")
        return error.value

    assert code(refused(Creds(good.access_key_id, "wrong"))) == "SignatureDoesNotMatch"
    assert code(refused(Creds("FLKNOSUCHKEYNOSUCHKE", "x"))) == "InvalidAccessKeyId"
    assert code(refused(Creds("AKIAIOSFODNN7EXAMPLE", "x"))) == "InvalidAccessKeyId"
    unsigned = httpx.get(f"{env.proxy_url}/{bucket}/x")
    assert unsigned.status_code == 403


def test_an_expired_key_is_refused(env: S3Env):
    bucket = env.bucket()
    creds = env.key("alice", ttl=1)
    env.s3(creds).put_object(Bucket=bucket, Key="x", Body=b"x")
    time.sleep(1.2)
    with pytest.raises(ClientError) as error:
        env.s3(creds).get_object(Bucket=bucket, Key="x")
    assert code(error.value) == "ExpiredToken"


def test_lowering_the_maximum_refuses_older_keys_at_once(env: S3Env):
    bucket = env.bucket(org="lab")
    old, new = env.key("olga"), env.key("olga")
    env.s3(old).put_object(Bucket=bucket, Key="x", Body=b"x")
    env.sql("UPDATE s3_keys SET created_at = now() - interval '2 days' "
            "WHERE access_key_id = $1", old.access_key_id)  # fmt: skip
    env.s3(old).get_object(Bucket=bucket, Key="x")  # still within 30 days
    lab = env.api("admin")
    try:
        assert lab.patch("/api/v1/orgs/lab", {"s3_key_max_ttl": 86400}).status == 200
        with pytest.raises(ClientError) as error:
            env.s3(old).get_object(Bucket=bucket, Key="x")
        assert code(error.value) == "ExpiredToken"
        assert env.s3(new).get_object(Bucket=bucket, Key="x")["Body"].read() == b"x"
    finally:
        lab.patch("/api/v1/orgs/lab", {"s3_key_max_ttl": 30 * 86400})


def test_a_revoked_key_is_cut_off_at_once(env: S3Env):
    bucket = env.bucket()
    creds = env.key("alice")
    s3 = env.s3(creds)
    s3.put_object(Bucket=bucket, Key="x", Body=b"x")
    s3.get_object(Bucket=bucket, Key="x")  # now cached by the proxy
    alice = env.api("alice")
    key_id = next(
        k["id"]
        for k in alice.get("/api/v1/me/s3-keys").json()["keys"]
        if k["access_key_id"] == creds.access_key_id
    )
    assert alice.delete(f"/api/v1/me/s3-keys/{key_id}").status == 204
    with pytest.raises(ClientError) as error:
        s3.get_object(Bucket=bucket, Key="x")
    assert code(error.value) == "InvalidAccessKeyId"


def test_a_removed_member_is_cut_off_at_once(env: S3Env):
    user = env.flock.add_user(f"{env.name('leaver')}@flock.test")
    bucket = env.bucket()
    creds = Creds(**{
        k: v for k, v in env.flock.credentials(user.api).items()
        if k in ("access_key_id", "secret_access_key")
    })  # fmt: skip
    s3 = env.s3(creds)
    s3.put_object(Bucket=bucket, Key="x", Body=b"x")
    removed = env.api("admin").delete(f"/api/v1/orgs/flock/members/{user.id}")
    assert removed.status == 204
    with pytest.raises(ClientError) as error:
        s3.get_object(Bucket=bucket, Key="x")
    assert code(error.value) == "AccessDenied"


def test_read_only_keys_cannot_write(env: S3Env):
    bucket = env.bucket()
    env.s3(env.key("alice")).put_object(Bucket=bucket, Key="x", Body=b"x")
    s3 = env.s3(env.key("alice", read_only=True))
    assert s3.get_object(Bucket=bucket, Key="x")["Body"].read() == b"x"
    assert [o["Key"] for o in s3.list_objects_v2(Bucket=bucket)["Contents"]] == ["x"]
    for call in (
        lambda: s3.put_object(Bucket=bucket, Key="y", Body=b"y"),
        lambda: s3.delete_object(Bucket=bucket, Key="x"),
        lambda: s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": "x"}]}),
        lambda: s3.create_multipart_upload(Bucket=bucket, Key="z"),
        lambda: s3.copy_object(Bucket=bucket, Key="z", CopySource=f"{bucket}/x"),
    ):
        with pytest.raises(ClientError) as error:
            call()
        assert code(error.value) == "AccessDenied"


def test_last_used_is_recorded_once_a_minute(env: S3Env, wait_for):
    bucket = env.bucket()
    creds = env.key("alice")
    s3 = env.s3(creds)

    def last_used():
        rows = env.sql(
            "SELECT last_used_at FROM s3_keys WHERE access_key_id = $1", creds.access_key_id
        )
        return rows[0]["last_used_at"]

    assert last_used() is None
    s3.put_object(Bucket=bucket, Key="x", Body=b"x")
    first = wait_for(last_used)
    s3.get_object(Bucket=bucket, Key="x")
    time.sleep(0.2)
    assert last_used() == first


def test_the_aws_cli(env: S3Env, tmp_path):
    aws = shutil.which("aws")
    if aws is None:
        pytest.skip("the AWS CLI isn't installed")
    bucket = env.bucket()
    creds = env.key("alice")
    environ = {
        **{k: v for k, v in os.environ.items() if not k.startswith("AWS_")},
        "AWS_ACCESS_KEY_ID": creds.access_key_id,
        "AWS_SECRET_ACCESS_KEY": creds.secret_access_key,
        "AWS_ENDPOINT_URL": env.proxy_url,
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_CONFIG_FILE": str(tmp_path / "config"),
        "AWS_SHARED_CREDENTIALS_FILE": str(tmp_path / "credentials"),
    }
    (tmp_path / "config").write_text("[default]\ns3 =\n  addressing_style = path\n")
    small, large = tmp_path / "small.txt", tmp_path / "large.bin"
    small.write_text("hello from the AWS CLI\n")
    large.write_bytes(os.urandom(20 * 1024 * 1024))  # multipart, in 8 MiB parts

    def run(*args: str) -> str:
        done = subprocess.run(
            [aws, *args], env=environ, capture_output=True, text=True, timeout=120, check=False
        )
        assert done.returncode == 0, done.stderr
        return done.stdout

    run("s3", "cp", str(small), f"s3://{bucket}/dir/small.txt")
    run("s3", "cp", str(large), f"s3://{bucket}/dir/large.bin")
    listing = run("s3", "ls", f"s3://{bucket}/dir/")
    assert "small.txt" in listing and "large.bin" in listing
    assert bucket in run("s3", "ls")
    run("s3", "cp", f"s3://{bucket}/dir/large.bin", str(tmp_path / "back.bin"))
    assert (tmp_path / "back.bin").read_bytes() == large.read_bytes()
    run("s3", "rm", "--recursive", f"s3://{bucket}/dir/")
    assert run("s3", "ls", f"s3://{bucket}/") == ""


def test_lance_through_the_proxy(env: S3Env):
    lance = pytest.importorskip("lance")
    pa = pytest.importorskip("pyarrow")
    bucket = env.bucket()
    alice, bob = env.storage_options(env.key("alice")), env.storage_options(env.key("bob"))
    uri = f"s3://{bucket}/datasets/rows"
    table = pa.table(
        {"id": pa.array(range(100), pa.int64()), "text": [f"row {i}" for i in range(100)]}
    )
    lance.write_dataset(table, uri, storage_options=alice)
    lance.write_dataset(table, uri, mode="append", storage_options=bob)
    ds = lance.dataset(uri, storage_options=bob)
    assert ds.version == 2
    assert ds.count_rows() == 200
    assert ds.take([0, 199]).to_pydict()["id"] == [0, 99]
    upstream = env.upstream().list_objects_v2(Bucket=env.upstream_name(bucket))
    assert any(o["Key"].startswith("datasets/rows/_versions/") for o in upstream["Contents"])
