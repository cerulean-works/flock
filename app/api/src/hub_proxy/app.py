"""The hub's S3 proxy (D41, D43-D45, D47): ``uvicorn app.api.src.hub_proxy.app:app``.

Clients see one bucket, ``datasets``, holding ``<org>/<dataset>/<lance path>``. Each request's
SigV4 signature is checked against the caller's own credentials, its permissions against the
caller's roles, and it is then forwarded to ``$FLOCK_HUB_BUCKET/datasets/<uuid>/<lance path>``
upstream, re-signed with the global key. Bodies stream both ways, except manifests, which are
committed atomically under a Postgres advisory lock.
"""

import base64
import contextlib
import hashlib
import logging
import time
import xml.etree.ElementTree as ET
from collections.abc import AsyncGenerator, AsyncIterable, Mapping
from dataclasses import dataclass
from urllib.parse import unquote

import asyncpg
import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

from app.api.src.hub_proxy import s3xml, sigv4
from app.api.src.hub_proxy.auth import Access, Caller, Dataset, Directory
from app.api.src.hub_proxy.config import ProxyConfig
from app.api.src.hub_proxy.sigv4 import SigV4Error

log = logging.getLogger(__name__)

BUCKET = "datasets"
# When the virtual bucket was "created", for ListBuckets.
BUCKET_CREATED = "2026-01-01T00:00:00.000Z"
# Manifests are buffered to commit them atomically (D45); they are small.
MAX_MANIFEST = 64 * 1024 * 1024
# DeleteObjects bodies are buffered to rewrite their keys; S3 caps them at 1000 keys.
MAX_DELETE_BODY = 4 * 1024 * 1024
U64_MAX = 2**64 - 1
# The most manifests read to decide whether a transaction file is committed. A lost commit race
# is only a few versions behind; rewriting an old transaction file is refused unchecked.
MAX_TRANSACTION_SCAN = 32

# Client headers forwarded upstream; others (ACLs, storage classes, object lock...) aren't the
# client's to set.
FORWARD_HEADERS = frozenset(
    {
        "cache-control",
        "content-disposition",
        "content-encoding",
        "content-language",
        "content-length",
        "content-md5",
        "content-type",
        "expires",
        "if-match",
        "if-modified-since",
        "if-none-match",
        "if-unmodified-since",
        "range",
        "x-amz-copy-source-range",
        "x-amz-metadata-directive",
        "x-amz-sdk-checksum-algorithm",
    }
)
FORWARD_PREFIXES = ("x-amz-meta-", "x-amz-checksum-", "x-amz-copy-source-if-")
RESPONSE_HEADERS = frozenset(
    {
        "accept-ranges",
        "cache-control",
        "content-disposition",
        "content-encoding",
        "content-language",
        "content-length",
        "content-range",
        "content-type",
        "etag",
        "expires",
        "last-modified",
        "x-amz-mp-parts-count",
    }
)
RESPONSE_PREFIXES = ("x-amz-meta-", "x-amz-checksum-")
# Query parameters each object operation accepts; anything else is an unsupported subresource.
GET_PARAMS = frozenset(
    {
        "partNumber",
        "response-cache-control",
        "response-content-disposition",
        "response-content-encoding",
        "response-content-language",
        "response-content-type",
        "response-expires",
    }
)
LIST_PARTS_PARAMS = frozenset({"uploadId", "max-parts", "part-number-marker"})
LIST_PARAMS = frozenset(
    {
        "list-type",
        "prefix",
        "delimiter",
        "max-keys",
        "continuation-token",
        "start-after",
        "encoding-type",
        "fetch-owner",
    }
)


class S3Error(SigV4Error):
    """A request refused with an S3 error."""


def manifest_version(filename: str) -> int | None:
    """The version a Lance manifest's file name stands for, if it is one.

    V2 names are ``{u64::MAX - version:020}.manifest``, V1 names ``{version}.manifest``.
    """
    stem = filename.removesuffix(".manifest")
    if stem == filename or not stem.isascii() or not stem.isdigit():
        return None
    number = int(stem)
    version = U64_MAX - number if len(stem) == 20 and number <= U64_MAX else number
    return version if 0 < version < 2**63 else None


@dataclass
class Call:
    """One authenticated request."""

    request: Request
    method: str
    key: str  # the decoded object key in the virtual bucket ("" for the bucket itself)
    params: dict[str, str]  # without SigV4 parameters
    headers: Headers
    caller: Caller
    payload_hash: str
    resource: str


@dataclass(frozen=True)
class Target:
    """An object key resolved to its dataset."""

    dataset: Dataset
    access: Access
    rest: str  # the Lance path inside the dataset

    @property
    def root(self) -> str:
        return f"{self.dataset.org}/{self.dataset.name}/"

    @property
    def upstream(self) -> str:
        return f"datasets/{self.dataset.uuid}/{self.rest}"


class Proxy:
    def __init__(self, config: ProxyConfig) -> None:
        self.config = config
        self.endpoint = config.s3_endpoint.rstrip("/")
        self.host = httpx.URL(self.endpoint).netloc.decode()
        self._pool: asyncpg.Pool | None = None
        self._directory: Directory | None = None
        self._client: httpx.AsyncClient | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        assert self._pool is not None, "the proxy is not started"
        return self._pool

    @property
    def directory(self) -> Directory:
        assert self._directory is not None, "the proxy is not started"
        return self._directory

    @property
    def client(self) -> httpx.AsyncClient:
        assert self._client is not None, "the proxy is not started"
        return self._client

    async def start(self) -> None:
        self._pool = await asyncpg.create_pool(
            self.config.database_url, min_size=1, max_size=self.config.pool_size
        )
        self._directory = Directory(self._pool, self.config.database_url, self.config.mirror_org)
        await self._directory.start()
        # Lance reads with many concurrent ranged GETs, so allow plenty of connections.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, read=300.0),
            limits=httpx.Limits(max_connections=512, max_keepalive_connections=128),
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
        if self._directory is not None:
            await self._directory.close()
        if self._pool is not None:
            await self._pool.close()

    # ------------------------------------------------------------------ entry point

    async def handle(self, request: Request) -> Response:
        method = request.method
        raw_path = request.scope.get("raw_path", b"").decode("latin-1") or sigv4.uri_encode(
            request.scope["path"]
        )
        raw_query = request.scope.get("query_string", b"").decode("latin-1")
        path = unquote(raw_path)
        head = method == "HEAD"
        if path == "/healthz" and method == "GET":  # for the container's healthcheck
            return Response(b"ok", media_type="text/plain")
        try:
            now = time.time()
            headers = [
                (k.decode("latin-1"), v.decode("latin-1")) for k, v in request.scope["headers"]
            ]
            signed = sigv4.parse_request(method, raw_path, raw_query, headers, now)
            caller = await self.directory.authenticate(signed, now)
            params = {
                k: v
                for k, v in sigv4.parse_query(raw_query)
                if k not in sigv4.AUTH_PARAMS and k != "x-id"  # x-id: botocore's operation name
            }
            if path == "/":
                if method != "GET":
                    raise S3Error("MethodNotAllowed", "only ListBuckets is supported here", 405)
                return s3xml.list_buckets(BUCKET, BUCKET_CREATED)
            bucket, _, key = path[1:].partition("/")
            if bucket != BUCKET:
                raise S3Error("NoSuchBucket", "the only bucket is 'datasets'", 404)
            call = Call(
                request, method, key, params, request.headers, caller, signed.payload_hash, path
            )
            return await (self._bucket(call) if not key else self._object(call))
        except SigV4Error as e:
            return s3xml.error(e.code, e.message, e.status, path, head)
        except httpx.HTTPError as e:
            log.warning("upstream request failed: %r", e)
            return s3xml.error("ServiceUnavailable", "the storage backend failed", 503, path, head)

    # ------------------------------------------------------------------ helpers

    async def _resolve(self, call: Call, key: str, need: str) -> Target:
        """The dataset ``key`` is in, if the caller may ``need`` (read, write, delete) it."""
        org, _, rest = key.partition("/")
        name, slash, rest = rest.partition("/")
        if not org or not name or not slash:
            raise S3Error("AccessDenied", "Access Denied")
        dataset = await self.directory.dataset(org, name)
        if dataset is None:
            raise S3Error("AccessDenied", "Access Denied")
        access = await self.directory.access(call.caller, dataset)
        # The same answer as for a missing dataset when it can't even be read (D44).
        if not getattr(access, need):
            raise S3Error("AccessDenied", "Access Denied")
        return Target(dataset, access, rest)

    def _forwarded(self, call: Call) -> dict[str, str]:
        out = {}
        for name, value in call.headers.items():
            if name in FORWARD_HEADERS or name.startswith(FORWARD_PREFIXES):
                out[name] = value
        return out

    async def _send(
        self,
        method: str,
        key: str | None,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        content: bytes | AsyncIterable[bytes] | None = None,
        payload_hash: str = sigv4.EMPTY_SHA256,
        stream: bool = False,
    ) -> httpx.Response:
        """A request to the upstream bucket (``key`` None for the bucket), signed globally."""
        path = f"/{sigv4.uri_encode(self.config.bucket)}"
        if key is not None:
            path += "/" + sigv4.uri_encode(key)
        items = list((params or {}).items())
        query = "&".join(
            f"{sigv4.uri_encode(k, False)}={sigv4.uri_encode(v, False)}" for k, v in items
        )
        # Ask for the bytes as stored: a compressed response would break ranges and lengths.
        signed = sigv4.sign(
            method,
            self.host,
            path,
            items,
            {**(headers or {}), "accept-encoding": "identity"},
            payload_hash,
            self.config.s3_access_key_id,
            self.config.s3_secret_access_key,
            self.config.s3_region,
            time.time(),
        )
        url = self.endpoint + path + (f"?{query}" if query else "")
        request = self.client.build_request(method, url, headers=signed, content=content)
        return await self.client.send(request, stream=stream)

    async def _exists(self, key: str) -> bool:
        response = await self._send("HEAD", key)
        if response.status_code == 404:
            return False
        if response.status_code == 200:
            return True
        raise S3Error("ServiceUnavailable", "could not check the object upstream", 503)

    def _headers(self, response: httpx.Response) -> dict[str, str]:
        return {
            name: value
            for name, value in response.headers.items()
            if name in RESPONSE_HEADERS or name.startswith(RESPONSE_PREFIXES)
        }

    def _upstream_error(self, call: Call, status: int, body: bytes) -> Response:
        parsed = s3xml.parse_error(body) or ("InternalError", "the storage backend failed")
        code, message = parsed
        if code == "NoSuchBucket":  # the hub bucket itself is missing: our problem, not theirs
            code, message, status = "InternalError", "the hub bucket does not exist", 500
        return s3xml.error(code, message, status, call.resource, call.method == "HEAD")

    async def _relay(self, call: Call, response: httpx.Response) -> Response:
        """Stream an upstream answer back as it is (errors become our own S3 errors)."""
        if response.status_code >= 300 and response.status_code != 304:
            body = await response.aread()
            await response.aclose()
            return self._upstream_error(call, response.status_code, body)
        headers = self._headers(response)
        if call.method == "HEAD" or response.status_code in (204, 304):
            await response.aclose()
            return Response(status_code=response.status_code, headers=headers)
        return StreamingResponse(
            response.aiter_raw(),
            response.status_code,
            headers,
            background=BackgroundTask(response.aclose),
        )

    async def _rewritten(
        self, call: Call, response: httpx.Response, target: Target, **kwargs
    ) -> Response:
        """An upstream XML answer for one dataset, with its keys mapped back to the client's."""
        body = await response.aread()
        if response.status_code >= 300:
            return self._upstream_error(call, response.status_code, body)
        # CompleteMultipartUpload and CopyObject can fail after a 200.
        if (failed := s3xml.parse_error(body)) is not None:
            return s3xml.error(*failed, 500, call.resource)
        upstream_root = f"datasets/{target.dataset.uuid}/"

        def key(upstream: str) -> str | None:
            if not upstream.startswith(upstream_root):
                return None
            return target.root + upstream[len(upstream_root) :]

        root, _ = s3xml.rewrite(body, key, BUCKET, **kwargs)
        return s3xml.xml_response(root)

    @staticmethod
    async def _read(request: Request, limit: int) -> bytes:
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise S3Error("EntityTooLarge", "the body is too large", 400)
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _check_hash(call: Call, body: bytes) -> None:
        """Bodies the proxy buffers are checked here, since the upstream won't see them as sent."""
        unsigned = call.payload_hash == sigv4.UNSIGNED_PAYLOAD
        if not unsigned and hashlib.sha256(body).hexdigest() != call.payload_hash:
            raise S3Error(
                "XAmzContentSHA256Mismatch", "the body does not match x-amz-content-sha256", 400
            )

    @staticmethod
    def _length(call: Call) -> int:
        try:
            return int(call.headers["content-length"])
        except KeyError, ValueError:
            raise S3Error("MissingContentLength", "Content-Length is required", 411) from None

    @staticmethod
    def _only(call: Call, allowed: frozenset[str] | set[str]) -> None:
        extra = call.params.keys() - allowed
        if extra:
            raise S3Error(
                "NotImplemented", f"unsupported subresource or parameter: {min(extra)}", 501
            )

    # ------------------------------------------------------------------ the bucket

    async def _bucket(self, call: Call) -> Response:
        if call.method == "HEAD" and not call.params:  # HeadBucket
            return Response(status_code=200)
        if call.method == "GET" and call.params.get("list-type") == "2":
            return await self._list(call)
        if call.method == "POST" and call.params.keys() == {"delete"}:
            return await self._delete_objects(call)
        raise S3Error("NotImplemented", "this bucket operation is not supported", 501)

    def _token(self, upstream: str) -> str:
        return base64.urlsafe_b64encode(upstream.encode()).decode()

    async def _list(self, call: Call) -> Response:
        """ListObjectsV2 inside one dataset; listing above a dataset is refused (D41)."""
        self._only(call, LIST_PARAMS)
        prefix = call.params.get("prefix", "")
        target = await self._resolve(call, prefix, "read")
        params = {k: v for k, v in call.params.items() if k != "fetch-owner"}
        params["prefix"] = target.upstream
        if "start-after" in params:
            start = params["start-after"]
            if not start.startswith(target.root):
                raise S3Error("InvalidArgument", "start-after must be inside the dataset", 400)
            params["start-after"] = f"datasets/{target.dataset.uuid}/{start[len(target.root) :]}"
        if "continuation-token" in params:
            try:
                token = base64.urlsafe_b64decode(params["continuation-token"].encode()).decode()
            except ValueError:
                raise S3Error("InvalidArgument", "invalid continuation token", 400) from None
            params["continuation-token"] = token
        response = await self._send("GET", None, params)
        return await self._rewritten(
            call,
            response,
            target,
            url_encoded=params.get("encoding-type") == "url",
            token=self._token,
        )

    async def _delete_objects(self, call: Call) -> Response:
        body = await self._read(call.request, MAX_DELETE_BODY)
        self._check_hash(call, body)
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            raise S3Error("MalformedXML", "the XML is not well-formed", 400) from None
        datasets: dict[str, Target] = {}
        for element in root.iter():
            if s3xml.local(element.tag) == "Key":
                target = await self._resolve(call, element.text or "", "write")
                # Tags are labels, not history: writers may delete them (Lance does so in bulk).
                if not (target.access.delete or target.rest.startswith("_refs/tags/")):
                    raise S3Error("AccessDenied", "Access Denied")
                datasets[target.dataset.uuid] = target
                element.text = target.upstream
        rewritten = s3xml.dump(root)
        headers = {
            "content-type": "application/xml",
            "content-md5": base64.b64encode(hashlib.md5(rewritten).digest()).decode(),
        }
        payload_hash = hashlib.sha256(rewritten).hexdigest()
        response = await self._send("POST", None, {"delete": ""}, headers, rewritten, payload_hash)
        answer = await response.aread()
        if response.status_code >= 300:
            return self._upstream_error(call, response.status_code, answer)

        def key(upstream: str) -> str | None:
            _, uuid, rest = (upstream.split("/", 2) + ["", ""])[:3]
            target = datasets.get(uuid)
            return target.root + rest if target is not None else None

        result, _ = s3xml.rewrite(answer, key, BUCKET)
        return s3xml.xml_response(result)

    # ------------------------------------------------------------------ objects

    async def _object(self, call: Call) -> Response:
        method, params = call.method, call.params
        if method in ("GET", "HEAD"):
            if "uploadId" in params and method == "GET":
                return await self._list_parts(call)
            return await self._get(call)
        if method == "PUT":
            if "uploadId" in params or "partNumber" in params:
                return await self._upload_part(call)
            if "x-amz-copy-source" in call.headers:
                return await self._copy(call)
            return await self._put(call)
        if method == "POST":
            if params.keys() == {"uploads"}:
                return await self._create_upload(call)
            if params.keys() == {"uploadId"}:
                return await self._complete_upload(call)
        if method == "DELETE":
            if params.keys() == {"uploadId"}:
                target = await self._resolve(call, call.key, "write")
                return await self._relay(call, await self._send("DELETE", target.upstream, params))
            if not params:
                target = await self._resolve(call, call.key, "write")
                # Writers may delete tags (labels, not history; members tag under D44) and a
                # transaction file no version refers to: Lance deletes its own after losing a
                # commit race, before rewriting it.
                if not (
                    target.access.delete
                    or target.rest.startswith("_refs/tags/")
                    or (
                        target.rest.startswith("_transactions/")
                        and not await self._transaction_committed(target)
                    )
                ):
                    raise S3Error("AccessDenied", "Access Denied")
                return await self._relay(call, await self._send("DELETE", target.upstream))
        raise S3Error("NotImplemented", "this operation is not supported", 501)

    async def _get(self, call: Call) -> Response:
        self._only(call, GET_PARAMS)
        target = await self._resolve(call, call.key, "read")
        response = await self._send(
            call.method,
            target.upstream,
            call.params,
            self._forwarded(call),
            stream=True,
        )
        return await self._relay(call, response)

    async def _guard(self, call: Call, target: Target, if_none_match: bool) -> None:
        """Refuse to overwrite history (D43) and emulate ``If-None-Match: *`` (D45)."""
        rest = target.rest
        if rest.startswith("_versions/") and rest.endswith(".manifest"):
            raise S3Error("AccessDenied", "manifests can only be written by a plain PUT")
        protected = rest.startswith(("_versions/", "_transactions/"))
        if (protected or if_none_match) and await self._exists(target.upstream):
            if if_none_match:
                raise S3Error(
                    "PreconditionFailed", "At least one of the preconditions did not hold", 412
                )
            # A transaction file is history once a version refers to it; until then it is the
            # writer's scratch (Lance rewrites it under the same name after a lost race).
            if rest.startswith("_versions/") or await self._transaction_committed(target):
                raise S3Error("AccessDenied", f"{rest} already exists and can't be overwritten")

    async def _transaction_committed(self, target: Target) -> bool:
        """Whether a committed manifest refers to the transaction file ``target``.

        Lance names transaction files ``{read_version}-{uuid}.txn`` and each manifest holds its
        transaction file's name, so only manifests newer than the read version are searched.
        Anything that can't be checked cheaply counts as committed.
        """
        name = target.rest.removeprefix("_transactions/")
        read_version = name.partition("-")[0]
        if "/" in name or not read_version.isascii() or not read_version.isdigit():
            return True
        manifests = []
        for key in await self._list_keys(f"datasets/{target.dataset.uuid}/_versions/"):
            version = manifest_version(key.rpartition("/")[2])
            if version is not None and version > int(read_version):
                manifests.append(key)
        if len(manifests) > MAX_TRANSACTION_SCAN:
            return True
        for key in manifests:
            response = await self._send("GET", key)
            if response.status_code != 200 or name.encode() in response.content:
                return True
        return False

    async def _list_keys(self, prefix: str) -> list[str]:
        """Every upstream key under ``prefix``."""
        keys: list[str] = []
        params = {"list-type": "2", "prefix": prefix}
        while True:
            response = await self._send("GET", None, params)
            if response.status_code != 200:
                raise S3Error("ServiceUnavailable", "could not list objects upstream", 503)
            root = ET.fromstring(response.content)
            token = None
            for element in root.iter():
                name = s3xml.local(element.tag)
                if name == "Key" and element.text:
                    keys.append(element.text)
                elif name == "NextContinuationToken":
                    token = element.text
            if not token:
                return keys
            params["continuation-token"] = token

    async def _put(self, call: Call) -> Response:
        self._only(call, set())
        target = await self._resolve(call, call.key, "write")
        if_none_match = call.headers.get("if-none-match", "").strip() == "*"
        rest = target.rest
        if rest.startswith("_versions/") and rest.endswith(".manifest"):
            return await self._commit(call, target, if_none_match)
        length = self._length(call)
        await self._guard(call, target, if_none_match)
        response = await self._send(
            "PUT",
            target.upstream,
            None,
            self._forwarded(call),
            call.request.stream() if length else b"",
            call.payload_hash,
            stream=True,
        )
        return await self._relay(call, response)

    async def _commit(self, call: Call, target: Target, if_none_match: bool) -> Response:
        """Write a manifest only if no other commit took its version (D45), and record who
        made it (D42)."""
        if not if_none_match:
            raise S3Error("AccessDenied", "manifests must be written with If-None-Match: *")
        version = manifest_version(target.rest.removeprefix("_versions/"))
        if version is None:
            raise S3Error("InvalidArgument", "not a Lance manifest name", 400)
        if self._length(call) > MAX_MANIFEST:
            raise S3Error("EntityTooLarge", "manifests are limited to 64 MiB", 400)
        body = await self._read(call.request, MAX_MANIFEST)
        self._check_hash(call, body)
        # Raising inside the transaction rolls the commit row back.
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('hub:' || $1::text, 0))",
                target.dataset.uuid,
            )
            if await self._exists(target.upstream):
                raise S3Error(
                    "PreconditionFailed", "At least one of the preconditions did not hold", 412
                )
            # The row goes in first, so a failed upload leaves neither; a row for a version
            # whose manifest is gone (deleted by an admin) describes another commit.
            await conn.execute(
                """
                INSERT INTO dataset_commits (dataset_pk, version, user_pk, created_at)
                VALUES ($1, $2, $3, now())
                ON CONFLICT (dataset_pk, version) DO UPDATE SET
                    user_pk = excluded.user_pk, created_at = excluded.created_at,
                    op = NULL, message = NULL, rows = NULL, bytes = NULL, fragments = NULL,
                    pruned = false
                """,
                target.dataset.pk,
                version,
                call.caller.user,
            )
            response = await self._send(
                "PUT", target.upstream, None, self._forwarded(call), body, call.payload_hash
            )
            if response.status_code != 200:
                code, message = s3xml.parse_error(response.content) or (
                    "InternalError",
                    "the storage backend failed",
                )
                raise S3Error(code, message, response.status_code)
        return Response(status_code=200, headers=self._headers(response))

    async def _copy(self, call: Call) -> Response:
        """CopyObject, within one dataset."""
        self._only(call, set())
        target = await self._resolve(call, call.key, "write")
        source = unquote(call.headers["x-amz-copy-source"]).lstrip("/")
        source, _, version = source.partition("?")
        bucket, _, source_key = source.partition("/")
        if version or bucket != BUCKET or not source_key.startswith(target.root):
            raise S3Error("NotImplemented", "objects can only be copied within a dataset", 501)
        source_upstream = f"datasets/{target.dataset.uuid}/{source_key[len(target.root) :]}"
        await self._guard(call, target, call.headers.get("if-none-match", "").strip() == "*")
        headers = self._forwarded(call)
        headers["x-amz-copy-source"] = sigv4.uri_encode(f"/{self.config.bucket}/{source_upstream}")
        response = await self._send("PUT", target.upstream, None, headers)
        return await self._rewritten(call, response, target)

    async def _create_upload(self, call: Call) -> Response:
        target = await self._resolve(call, call.key, "write")
        await self._guard(call, target, False)
        response = await self._send("POST", target.upstream, {"uploads": ""}, self._forwarded(call))
        return await self._rewritten(call, response, target)

    async def _upload_part(self, call: Call) -> Response:
        self._only(call, {"uploadId", "partNumber"})
        if "x-amz-copy-source" in call.headers:
            raise S3Error("NotImplemented", "UploadPartCopy is not supported", 501)
        target = await self._resolve(call, call.key, "write")
        length = self._length(call)
        response = await self._send(
            "PUT",
            target.upstream,
            call.params,
            self._forwarded(call),
            call.request.stream() if length else b"",
            call.payload_hash,
            stream=True,
        )
        return await self._relay(call, response)

    async def _complete_upload(self, call: Call) -> Response:
        target = await self._resolve(call, call.key, "write")
        await self._guard(call, target, call.headers.get("if-none-match", "").strip() == "*")
        response = await self._send(
            "POST",
            target.upstream,
            call.params,
            self._forwarded(call),
            call.request.stream(),
            call.payload_hash,
        )
        return await self._rewritten(call, response, target)

    async def _list_parts(self, call: Call) -> Response:
        self._only(call, LIST_PARTS_PARAMS)
        target = await self._resolve(call, call.key, "read")
        response = await self._send("GET", target.upstream, call.params)
        return await self._rewritten(call, response, target)


def create_app(config: ProxyConfig | None = None) -> Starlette:
    """The proxy as an ASGI app; ``config`` defaults to the environment's, read at startup."""

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncGenerator[None]:
        proxy = Proxy(config or ProxyConfig.load())
        await proxy.start()
        app.state.proxy = proxy
        try:
            yield
        finally:
            await proxy.close()

    async def endpoint(request: Request) -> Response:
        return await request.app.state.proxy.handle(request)

    methods = ["GET", "HEAD", "PUT", "POST", "DELETE", "OPTIONS", "PATCH"]
    return Starlette(routes=[Route("/{path:path}", endpoint, methods=methods)], lifespan=lifespan)


app = create_app()
