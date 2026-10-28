import datetime as dt
import hashlib
import time
from urllib.parse import urlsplit

import pytest
from botocore.auth import S3SigV4Auth, S3SigV4QueryAuth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials

from app.api.src.s3_proxy import sigv4
from app.api.src.s3_proxy.sigv4 import SigV4Error

# The examples in S3's "Signature Calculations for the Authorization Header" and "Query
# Parameters" documentation.
AKID = "AKIAIOSFODNN7EXAMPLE"
SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
HOST = "examplebucket.s3.amazonaws.com"
WHEN = dt.datetime(2013, 5, 24, tzinfo=dt.UTC).timestamp()


def authorization(signed: str, signature: str) -> str:
    return (
        f"AWS4-HMAC-SHA256 Credential={AKID}/20130524/us-east-1/s3/aws4_request,"
        f"SignedHeaders={signed},Signature={signature}"
    )


AWS_EXAMPLES = {
    "get-object": (
        "GET",
        "/test.txt",
        "",
        [("Host", HOST), ("Range", "bytes=0-9"), ("x-amz-content-sha256", sigv4.EMPTY_SHA256),
         ("x-amz-date", "20130524T000000Z")],
        "host;range;x-amz-content-sha256;x-amz-date",
        "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41",
    ),
    "put-object": (
        "PUT",
        "/test%24file.text",
        "",
        [("Host", HOST), ("Date", "Fri, 24 May 2013 00:00:00 GMT"),
         ("x-amz-date", "20130524T000000Z"), ("x-amz-storage-class", "REDUCED_REDUNDANCY"),
         ("x-amz-content-sha256", hashlib.sha256(b"Welcome to Amazon S3.").hexdigest())],
        "date;host;x-amz-content-sha256;x-amz-date;x-amz-storage-class",
        "98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd",
    ),
    "get-bucket-lifecycle": (
        "GET",
        "/",
        "lifecycle",
        [("Host", HOST), ("x-amz-date", "20130524T000000Z"),
         ("x-amz-content-sha256", sigv4.EMPTY_SHA256)],
        "host;x-amz-content-sha256;x-amz-date",
        "fea454ca298b7da1c68078a5d1bdbfbbe0d65c699e0f91ac7a200a0136783543",
    ),
    "list-objects": (
        "GET",
        "/",
        "max-keys=2&prefix=J",
        [("Host", HOST), ("x-amz-date", "20130524T000000Z"),
         ("x-amz-content-sha256", sigv4.EMPTY_SHA256)],
        "host;x-amz-content-sha256;x-amz-date",
        "34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7",
    ),
}  # fmt: skip


@pytest.mark.parametrize("example", AWS_EXAMPLES)
def test_aws_header_examples(example):
    method, path, query, headers, signed, signature = AWS_EXAMPLES[example]
    headers = [*headers, ("Authorization", authorization(signed, signature))]
    request = sigv4.parse_request(method, path, query, headers, WHEN)
    assert request.access_key_id == AKID
    assert request.verify(SECRET)
    assert not request.verify(SECRET + "x")
    # Any change to what was signed breaks it.
    tampered = [(n, "bytes=0-10" if n == "Range" else v) for n, v in headers]
    if example == "get-object":
        assert not sigv4.parse_request(method, path, query, tampered, WHEN).verify(SECRET)
    assert not sigv4.parse_request(method, path + "x", query, headers, WHEN).verify(SECRET)


def test_aws_presigned_example():
    query = (
        "X-Amz-Algorithm=AWS4-HMAC-SHA256"
        f"&X-Amz-Credential={AKID}%2F20130524%2Fus-east-1%2Fs3%2Faws4_request"
        "&X-Amz-Date=20130524T000000Z&X-Amz-Expires=86400&X-Amz-SignedHeaders=host"
        "&X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404"
    )
    request = sigv4.parse_request("GET", "/test.txt", query, [("Host", HOST)], WHEN + 3600)
    assert request.verify(SECRET)
    assert request.payload_hash == sigv4.UNSIGNED_PAYLOAD
    with pytest.raises(SigV4Error, match="expired"):
        sigv4.parse_request("GET", "/test.txt", query, [("Host", HOST)], WHEN + 86401)


def test_our_signer_matches_the_aws_example():
    method, path, query, _, _, signature = AWS_EXAMPLES["get-object"]
    out = sigv4.sign(
        method, HOST, path, sigv4.parse_query(query), {"Range": "bytes=0-9"},
        sigv4.EMPTY_SHA256, AKID, SECRET, "us-east-1", WHEN,
    )  # fmt: skip
    assert out["authorization"].endswith(f"Signature={signature}")


def botocore_request(url: str, method: str = "GET", headers=None, body=b"", presign=False):
    """Sign with botocore (an independent implementation); returns parse_request's inputs."""
    request = AWSRequest(method=method, url=url, data=body, headers=headers or {})
    credentials = Credentials("FLKTESTTESTTESTTESTT", "s3cr3t")
    if presign:
        S3SigV4QueryAuth(credentials, "s3", "eu-west-9", expires=60).add_auth(request)
    else:
        request.headers["x-amz-content-sha256"] = hashlib.sha256(body).hexdigest()
        S3SigV4Auth(credentials, "s3", "eu-west-9").add_auth(request)
    prepared = request.prepare()
    parts = urlsplit(prepared.url)
    headers = [("Host", parts.netloc), *prepared.headers.items()]
    return method, parts.path, parts.query, headers


@pytest.mark.parametrize("presign", [False, True])
@pytest.mark.parametrize(
    "path",
    ["/checkpoints/run-1/model.safetensors", "/datasets/a b/c+d=e$f/%/ü~x", "/"],
)
def test_botocore_signatures_verify(path, presign):
    url = "http://127.0.0.1:9000" + sigv4.uri_encode(path)
    query = "?list-type=2&prefix=a%2Fb%20c&continuation-token=x%3D%3D&delimiter=%2F"
    method, raw_path, raw_query, headers = botocore_request(url + query, presign=presign)
    request = sigv4.parse_request(method, raw_path, raw_query, headers, time.time())
    assert request.access_key_id == "FLKTESTTESTTESTTESTT"
    assert request.scope.split("/")[1] == "eu-west-9"
    assert request.verify("s3cr3t")
    assert not request.verify("s3cr3T")


def test_payload_hash_is_signed_and_streaming_refused():
    method, path, query, headers = botocore_request("http://h/datasets/o/d/x", "PUT", body=b"hello")
    request = sigv4.parse_request(method, path, query, headers, time.time())
    assert request.payload_hash == hashlib.sha256(b"hello").hexdigest()
    assert request.verify("s3cr3t")
    swapped = [
        (n, sigv4.UNSIGNED_PAYLOAD if n.lower() == "x-amz-content-sha256" else v)
        for n, v in headers
    ]
    assert not sigv4.parse_request(method, path, query, swapped, time.time()).verify("s3cr3t")
    streaming = [
        (n, "STREAMING-AWS4-HMAC-SHA256-PAYLOAD" if n.lower() == "x-amz-content-sha256" else v)
        for n, v in headers
    ]
    with pytest.raises(SigV4Error) as error:
        sigv4.parse_request(method, path, query, streaming, time.time())
    assert error.value.status == 501 and error.value.code == "NotImplemented"


def test_clock_skew_and_malformed_requests():
    method, path, query, headers = botocore_request("http://h/datasets/o/d/x")
    now = time.time()
    assert sigv4.parse_request(method, path, query, headers, now + 14 * 60).verify("s3cr3t")
    with pytest.raises(SigV4Error) as error:
        sigv4.parse_request(method, path, query, headers, now + 16 * 60)
    assert error.value.code == "RequestTimeTooSkewed"
    with pytest.raises(SigV4Error, match="not signed"):
        sigv4.parse_request("GET", "/", "", [("Host", "h")], now)
    with pytest.raises(SigV4Error):
        sigv4.parse_request("GET", "/", "", [("Authorization", "AWS AKID:sig")], now)
    unsigned_host = [
        (n, v.replace("host;", "")) if n == "Authorization" else (n, v) for n, v in headers
    ]
    with pytest.raises(SigV4Error, match="host"):
        sigv4.parse_request(method, path, query, unsigned_host, now)


def test_signer_round_trips_through_the_verifier():
    now = time.time()
    path = sigv4.uri_encode("/bucket/datasets/2b0c/_versions/18446744073709551614.manifest")
    params = [("uploadId", "a/b+c"), ("partNumber", "3")]
    out = sigv4.sign(
        "PUT", "127.0.0.1:7480", path, params, {"Content-Length": "5", "If-None-Match": "*"},
        sigv4.UNSIGNED_PAYLOAD, "global", "global-secret", "us-east-1", now,
    )  # fmt: skip
    raw_query = "&".join(
        f"{sigv4.uri_encode(k, False)}={sigv4.uri_encode(v, False)}" for k, v in params
    )
    request = sigv4.parse_request("PUT", path, raw_query, list(out.items()), now)
    assert request.verify("global-secret")
    assert request.payload_hash == sigv4.UNSIGNED_PAYLOAD
