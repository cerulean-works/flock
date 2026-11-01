"""What the dashboard's browser does with the proxy (D69): presigned uploads, downloads and
multipart uploads, and CORS for the web origin only."""

import hashlib
import os
import xml.etree.ElementTree as ET

import httpx
import pytest

from tests.app.conftest import Flocks, S3Flock
from tests.conftest import S3Server
from tests.s3.conftest import S3Env, make_env

WEB = "http://dashboard.flock.test:5173"


@pytest.fixture(scope="module")
def env(make_module_flock: Flocks, s3_server: S3Server) -> S3Env:
    return make_env(make_module_flock, s3_server, web_url=WEB)


def presign(env: S3Env, bucket: str, user: str = "alice", **body) -> dict:
    got = env.api(user).post(f"/api/v1/orgs/flock/buckets/{bucket}/presign", body)
    assert got.status == 200, got.body
    return got.json()


def test_presigned_put_and_get(env):
    bucket = env.bucket()
    body = b"hello, browser"
    put = presign(env, bucket, method="put", key="docs/hello world.txt", size=len(body))
    sent = httpx.put(
        put["url"], content=body, headers={"content-type": "text/plain", "origin": WEB}
    )
    assert sent.status_code == 200, sent.text
    assert sent.headers["access-control-allow-origin"] == WEB
    assert "ETag" in sent.headers["access-control-expose-headers"]
    stored = env.upstream().get_object(Bucket=env.upstream_name(bucket), Key="docs/hello world.txt")
    assert stored["Body"].read() == body
    assert stored["ContentType"] == "text/plain"

    download = presign(env, bucket, method="get", key="docs/hello world.txt")
    got = httpx.get(download["url"])
    assert got.status_code == 200 and got.content == body
    assert got.headers["content-disposition"] == "attachment; filename*=UTF-8''hello%20world.txt"

    inline = presign(env, bucket, method="get", key="docs/hello world.txt", disposition="inline")
    ranged = httpx.get(inline["url"], headers={"range": "bytes=0-4", "origin": WEB})
    assert ranged.status_code == 206 and ranged.content == b"hello"
    assert ranged.headers["content-disposition"].startswith("inline;")
    assert ranged.headers["access-control-allow-origin"] == WEB

    # Previews may ask for a safe type, such as a PDF the uploader didn't label.
    shown = presign(
        env,
        bucket,
        method="get",
        key="docs/hello world.txt",
        disposition="inline",
        content_type="application/pdf",
    )
    assert httpx.get(shown["url"]).headers["content-type"] == "application/pdf"

    # Tampering with a presigned URL breaks its signature.
    tampered = download["url"].replace("attachment", "inline")
    assert httpx.get(tampered).status_code == 403


def test_a_browser_style_multipart_upload(env):
    bucket = env.bucket()
    data = os.urandom(17 * 1024 * 1024)
    key = "big/model.bin"
    started = presign(
        env,
        bucket,
        method="multipart",
        key=key,
        size=len(data),
        content_type="application/octet-stream",
    )
    part_size, count = started["part_size"], started["part_count"]
    assert (part_size, count) == (16 * 1024 * 1024, 2)
    upload_id = started["upload_id"]
    urls = presign(
        env, bucket, method="parts", key=key, upload_id=upload_id, parts=list(range(1, count + 1))
    )["parts"]
    etags = []
    for n in range(1, count + 1):
        chunk = data[(n - 1) * part_size : n * part_size]
        sent = httpx.put(urls[str(n)], content=chunk, headers={"origin": WEB}, timeout=60)
        assert sent.status_code == 200, sent.text
        etags.append(sent.headers["etag"])
    complete = presign(env, bucket, method="complete", key=key, upload_id=upload_id)
    xml = "<CompleteMultipartUpload>"
    for n, etag in enumerate(etags, 1):
        xml += f"<Part><PartNumber>{n}</PartNumber><ETag>{etag}</ETag></Part>"
    xml += "</CompleteMultipartUpload>"
    done = httpx.post(
        complete["url"], content=xml, headers={"content-type": "application/xml", "origin": WEB}
    )
    assert done.status_code == 200, done.text
    assert done.headers["access-control-allow-origin"] == WEB
    result = ET.fromstring(done.content)
    assert any(child.tag.endswith("Bucket") and child.text == bucket for child in result)
    stored = env.upstream().get_object(Bucket=env.upstream_name(bucket), Key=key)
    assert hashlib.sha256(stored["Body"].read()).digest() == hashlib.sha256(data).digest()


def test_an_aborted_multipart_upload_leaves_nothing(env):
    bucket = env.bucket()
    started = presign(env, bucket, method="multipart", key="x", size=10)
    upload_id = started["upload_id"]
    url = presign(env, bucket, method="parts", key="x", upload_id=upload_id, parts=[1])["parts"]
    assert httpx.put(url["1"], content=b"0123456789").status_code == 200
    assert presign(env, bucket, method="abort", key="x", upload_id=upload_id) == {}
    uploads = env.upstream().list_multipart_uploads(Bucket=env.upstream_name(bucket))
    assert uploads.get("Uploads", []) == []


def test_cors_preflight_for_the_web_origin_only(env):
    bucket = env.bucket()
    url = f"{env.proxy_url}/{bucket}/some/key"
    asked = {
        "origin": WEB,
        "access-control-request-method": "PUT",
        "access-control-request-headers": "content-type, x-amz-meta-run, x-evil",
    }
    allowed = httpx.options(url, headers=asked)
    assert allowed.status_code == 204
    assert allowed.headers["access-control-allow-origin"] == WEB
    assert allowed.headers["access-control-allow-methods"] == "GET, HEAD, PUT, POST, DELETE"
    assert allowed.headers["access-control-allow-headers"] == "content-type, x-amz-meta-run"

    other = httpx.options(url, headers={**asked, "origin": "https://evil.example"})
    assert other.status_code == 403
    assert "access-control-allow-origin" not in other.headers

    # Presigned URLs still work from other sites' requests (curl, say), without CORS headers.
    get = presign(env, bucket, method="put", key="k")
    sent = httpx.put(get["url"], content=b"x", headers={"origin": "https://evil.example"})
    assert sent.status_code == 200
    assert "access-control-allow-origin" not in sent.headers


def test_cors_is_off_without_a_web_url(make_flock: Flocks, s3_server: S3Server):
    flock: S3Flock = make_flock.s3(s3_server)
    got = httpx.options(
        f"{flock.proxy.url}/b/k",
        headers={"origin": WEB, "access-control-request-method": "GET"},
    )
    assert got.status_code == 403
    assert "access-control-allow-origin" not in got.headers
