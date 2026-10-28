"""Flock's S3 proxy (D53, D54, D61, D69): ``uvicorn app.api.src.s3_proxy.app:app``.

Clients sign requests with an S3 key from Flock and address their org's buckets by name,
path-style. Each request's signature is checked against the key's derived secret, the key's
expiry and its user's membership, then the request is forwarded to the bucket's upstream name
re-signed with the global key. Object keys pass through untouched, and bodies stream both
ways; only the small XML answers that name the bucket are rewritten.
"""

import base64
import contextlib
import hashlib
import logging
import time
from collections.abc import AsyncGenerator, AsyncIterable
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

import asyncpg
import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect, Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

from app.api.src.core.bus import Bus
from app.api.src.s3_proxy import chunked, s3xml, sigv4
from app.api.src.s3_proxy.auth import Bucket, Caller, Directory
from app.api.src.s3_proxy.sigv4 import SigV4Error
from app.api.src.s3_proxy.upstream import Upstream
from app.api.src.settings import Settings

log = logging.getLogger(__name__)

# Bodies the proxy buffers (DeleteObjects, multipart completions) are small; S3 caps them too.
MAX_XML_BODY = 4 * 1024 * 1024

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
# Query parameters each operation accepts; anything else is an unsupported subresource.
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
LIST_V2_PARAMS = frozenset(
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
LIST_V1_PARAMS = frozenset({"prefix", "delimiter", "max-keys", "marker", "encoding-type"})
UPLOAD_PART_PARAMS = frozenset({"uploadId", "partNumber"})
# Request headers a browser may send cross-origin (beyond x-amz-*), and response headers its
# scripts may read: ETag is what completing a multipart upload needs.
CORS_HEADERS = FORWARD_HEADERS | {"authorization"}
CORS_EXPOSE = (
    "ETag, Content-Length, Content-Range, Content-Type, Content-Disposition, Accept-Ranges, "
    "Last-Modified"
)


class S3Error(SigV4Error):
    """A request refused with an S3 error."""


@dataclass
class Call:
    """One authenticated request, for a bucket of the caller's org."""

    request: Request
    method: str
    bucket: Bucket
    key: str  # the decoded object key ("" for the bucket itself)
    params: dict[str, str]  # without SigV4 parameters
    headers: Headers
    caller: Caller
    payload_hash: str
    resource: str

    @property
    def head(self) -> bool:
        return self.method == "HEAD"


class Proxy:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.upstream = Upstream(settings, max_connections=512)
        self._pool: asyncpg.Pool | None = None
        self._directory: Directory | None = None
        self.bus = Bus(settings.listen_database_url or settings.database_url)
        web = urlsplit(settings.web_url or "")
        self.web_origin = f"{web.scheme}://{web.netloc}" if web.scheme and web.netloc else None

    @property
    def directory(self) -> Directory:
        assert self._directory is not None, "the proxy is not started"
        return self._directory

    async def start(self) -> None:
        self._pool = await asyncpg.create_pool(
            self.settings.database_url, min_size=1, max_size=self.settings.pool_size
        )
        self._directory = Directory(self._pool, self.bus)
        await self._directory.start()
        # Serve as soon as the bus listens (or failed to; the directory is uncached until then).
        await self.bus.start()

    async def close(self) -> None:
        await self.upstream.close()
        await self.bus.close()
        if self._directory is not None:
            await self._directory.close()
        if self._pool is not None:
            await self._pool.close()

    # ------------------------------------------------------------------ entry point

    async def handle(self, request: Request) -> Response:
        # CORS (D69): only the dashboard's pages may call the proxy from a browser.
        origin = request.headers.get("origin")
        allowed = origin is not None and origin == self.web_origin
        if request.method == "OPTIONS":
            if not allowed:
                return s3xml.error(
                    "AccessForbidden", "CORSResponse: This CORS request is not allowed.", 403
                )
            return Response(status_code=204, headers=preflight(request, origin or ""))
        response = await self._handle(request)
        # A request refused before its body was read: the body must go before the connection
        # can carry another request. Small ones are read; otherwise the connection closes.
        refused = response.status_code >= 400 and request.method in ("PUT", "POST")
        if refused and not await discard(request):
            response.headers["connection"] = "close"
        if allowed:
            response.headers.update(
                {
                    "access-control-allow-origin": origin or "",
                    "access-control-expose-headers": CORS_EXPOSE,
                    "vary": "Origin",
                }
            )
        return response

    async def _handle(self, request: Request) -> Response:
        method = request.method
        raw_path = request.scope.get("raw_path", b"").decode("latin-1") or sigv4.uri_encode(
            request.scope["path"]
        )
        raw_query = request.scope.get("query_string", b"").decode("latin-1")
        path = unquote(raw_path)
        head = method == "HEAD"
        if path == "/healthz" and method == "GET":  # for the container's healthcheck
            return Response(b"ok", media_type="text/plain")
        bucket_name, _, key = path[1:].partition("/")
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
            if not bucket_name:
                if method != "GET" or params:
                    raise S3Error("NotImplemented", "only ListBuckets is supported here", 501)
                buckets = sorted(caller.org.buckets.values(), key=lambda b: b.name)
                return s3xml.list_buckets((b.name, iso(b.created_at)) for b in buckets)
            if not key and not params and method in ("PUT", "DELETE"):
                raise S3Error(
                    "NotImplemented",
                    "buckets are created and deleted in Flock: use flock s3 buckets, or the "
                    "organization's page",
                    501,
                )
            bucket = caller.org.buckets.get(bucket_name)
            if bucket is None:
                raise S3Error("NoSuchBucket", "The specified bucket does not exist", 404)
            call = Call(
                request, method, bucket, key, params, request.headers, caller,
                signed.payload_hash, path,
            )  # fmt: skip
            return await (self._bucket(call) if not key else self._object(call))
        except SigV4Error as e:
            named = bucket_name if e.code == "NoSuchBucket" else None
            return s3xml.error(e.code, e.message, e.status, path, head, named)
        except chunked.ChunkedError as e:
            return s3xml.error("IncompleteBody", str(e), 400, path, head)
        except httpx.HTTPError as e:
            log.warning("upstream request failed: %r", e)
            return s3xml.error("ServiceUnavailable", "the storage backend failed", 503, path, head)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _writable(call: Call) -> None:
        if call.caller.read_only:
            raise S3Error("AccessDenied", "Access Denied: this key is read-only")

    def _forwarded(self, call: Call) -> dict[str, str]:
        out = {}
        for name, value in call.headers.items():
            if name in FORWARD_HEADERS or name.startswith(FORWARD_PREFIXES):
                out[name] = value
        if self._chunked(call):
            # The upstream gets the decoded body (see `_body`), without the framing or the
            # checksum that only the trailer carried.
            out["content-length"] = str(self._length(call))
            encoding = [
                e for e in out.pop("content-encoding", "").split(",") if e.strip() != "aws-chunked"
            ]
            if any(e.strip() for e in encoding):
                out["content-encoding"] = ",".join(encoding)
            out.pop("x-amz-sdk-checksum-algorithm", None)
        return out

    @staticmethod
    def _chunked(call: Call) -> bool:
        return call.payload_hash == chunked.STREAMING_UNSIGNED_TRAILER

    def _body(self, call: Call) -> AsyncIterable[bytes]:
        """The request body as the upstream should get it: aws-chunked bodies decoded."""
        if self._chunked(call):
            return chunked.decode(
                call.request.stream(), self._length(call), call.headers.get("x-amz-trailer")
            )
        return call.request.stream()

    def _upstream_hash(self, call: Call) -> str:
        """The payload hash to sign upstream requests with that forward the client's body."""
        return sigv4.UNSIGNED_PAYLOAD if self._chunked(call) else call.payload_hash

    async def _read(self, call: Call, limit: int = MAX_XML_BODY) -> bytes:
        """The whole (decoded) body, checked against the client's payload hash."""
        chunks, size = [], 0
        async for chunk in self._body(call):
            size += len(chunk)
            if size > limit:
                raise S3Error("EntityTooLarge", "the body is too large", 400)
            chunks.append(chunk)
        body = b"".join(chunks)
        unsigned = call.payload_hash in (sigv4.UNSIGNED_PAYLOAD, chunked.STREAMING_UNSIGNED_TRAILER)
        if not unsigned and hashlib.sha256(body).hexdigest() != call.payload_hash:
            raise S3Error(
                "XAmzContentSHA256Mismatch", "the body does not match x-amz-content-sha256", 400
            )
        return body

    @staticmethod
    def _length(call: Call) -> int:
        """The body's length, after decoding for aws-chunked bodies."""
        header = (
            "x-amz-decoded-content-length"
            if call.payload_hash == chunked.STREAMING_UNSIGNED_TRAILER
            else "content-length"
        )
        try:
            return int(call.headers[header])
        except KeyError, ValueError:
            raise S3Error("MissingContentLength", "Content-Length is required", 411) from None

    @staticmethod
    def _only(call: Call, allowed: frozenset[str] | set[str]) -> None:
        extra = call.params.keys() - allowed
        if extra:
            raise S3Error(
                "NotImplemented", f"unsupported subresource or parameter: {min(extra)}", 501
            )

    async def _send(
        self,
        call: Call,
        method: str,
        key: str | None,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        content: bytes | AsyncIterable[bytes] | None = None,
        payload_hash: str = sigv4.EMPTY_SHA256,
        stream: bool = False,
    ) -> httpx.Response:
        return await self.upstream.send(
            method,
            call.bucket.upstream_name,
            key,
            params,
            headers,
            content,
            payload_hash,
            stream=stream,
        )

    @staticmethod
    def _headers(response: httpx.Response) -> dict[str, str]:
        return {
            name: value
            for name, value in response.headers.items()
            if name in RESPONSE_HEADERS or name.startswith(RESPONSE_PREFIXES)
        }

    @staticmethod
    def _error(call: Call, status: int, body: bytes) -> Response:
        """An upstream error, passed on with the bucket called by the client's name."""
        code, message = s3xml.parse_error(body) or ("InternalError", "the storage backend failed")
        message = message.replace(call.bucket.upstream_name, call.bucket.name)
        named = call.bucket.name if code == "NoSuchBucket" else None
        return s3xml.error(code, message, status, call.resource, call.head, named)

    async def _relay(self, call: Call, response: httpx.Response) -> Response:
        """Stream an upstream answer back as it is (errors with the bucket renamed)."""
        if response.status_code >= 300 and response.status_code != 304:
            body = await response.aread()
            await response.aclose()
            return self._error(call, response.status_code, body)
        headers = self._headers(response)
        if call.head or response.status_code in (204, 304):
            await response.aclose()
            return Response(status_code=response.status_code, headers=headers)
        return StreamingResponse(
            response.aiter_raw(),
            response.status_code,
            headers,
            background=BackgroundTask(response.aclose),
        )

    async def _renamed(self, call: Call, response: httpx.Response, location: bool = False):
        """A small upstream XML answer with the bucket called by the client's name."""
        body = await response.aread()
        if response.status_code >= 300:
            return self._error(call, response.status_code, body)
        # CompleteMultipartUpload and the copies can fail after a 200.
        if (failed := s3xml.parse_error(body)) is not None:
            return s3xml.error(*failed, 500, call.resource)
        url = None
        if location:
            base = self.settings.s3_public_url or str(call.request.base_url)
            url = f"{base.rstrip('/')}/{call.bucket.name}/{sigv4.uri_encode(call.key)}"
        renamed = s3xml.rename(body, call.bucket.upstream_name, call.bucket.name, url)
        return Response(
            renamed, 200, self._headers(response) | {"content-length": str(len(renamed))}
        )

    # ------------------------------------------------------------------ buckets

    async def _bucket(self, call: Call) -> Response:
        method, params = call.method, call.params
        if method == "HEAD" and not params:  # HeadBucket
            return Response(status_code=200, headers={"x-amz-bucket-region": self.upstream.region})
        if method == "GET" and params.keys() == {"location"}:
            return s3xml.location(self.upstream.region)
        if method == "GET" and params.get("list-type") == "2":
            self._only(call, LIST_V2_PARAMS)
            return await self._list(call)
        if method == "GET" and params.keys() <= LIST_V1_PARAMS:
            return await self._list(call)
        if method == "POST" and params.keys() == {"delete"}:
            return await self._delete_objects(call)
        raise S3Error("NotImplemented", "this bucket operation is not supported", 501)

    async def _list(self, call: Call) -> Response:
        """ListObjects and ListObjectsV2: keys, prefixes and tokens pass through as they are."""
        params = {k: v for k, v in call.params.items() if k != "fetch-owner"}
        return await self._renamed(call, await self._send(call, "GET", None, params))

    async def _delete_objects(self, call: Call) -> Response:
        """DeleteObjects; the body is buffered to send it with its MD5, which S3 requires."""
        self._writable(call)
        body = await self._read(call)
        headers = {
            "content-type": "application/xml",
            "content-md5": base64.b64encode(hashlib.md5(body).digest()).decode(),
        }
        response = await self._send(
            call, "POST", None, {"delete": ""}, headers, body, hashlib.sha256(body).hexdigest()
        )
        return await self._renamed(call, response)

    # ------------------------------------------------------------------ objects

    async def _object(self, call: Call) -> Response:
        method, params = call.method, call.params
        if method in ("GET", "HEAD"):
            if "uploadId" in params and method == "GET":
                self._only(call, LIST_PARTS_PARAMS)
                return await self._renamed(call, await self._send(call, "GET", call.key, params))
            self._only(call, GET_PARAMS)
            response = await self._send(
                call, method, call.key, params, self._forwarded(call), stream=True
            )
            return await self._relay(call, response)
        self._writable(call)
        if method == "PUT":
            if "uploadId" in params or "partNumber" in params:
                self._only(call, UPLOAD_PART_PARAMS)
                if "x-amz-copy-source" in call.headers:  # UploadPartCopy
                    return await self._copy(call)
                return await self._put(call)
            self._only(call, set())
            if "x-amz-copy-source" in call.headers:
                return await self._copy(call)
            return await self._put(call)
        if method == "POST":
            if params.keys() == {"uploads"}:
                response = await self._send(
                    call, "POST", call.key, {"uploads": ""}, self._forwarded(call)
                )
                return await self._renamed(call, response)
            if params.keys() == {"uploadId"}:
                body = await self._read(call)
                headers = self._forwarded(call)
                headers["content-length"] = str(len(body))
                headers.pop("x-amz-sdk-checksum-algorithm", None)
                response = await self._send(
                    call, "POST", call.key, params, headers, body, hashlib.sha256(body).hexdigest()
                )
                return await self._renamed(call, response, location=True)
        if method == "DELETE" and (not params or params.keys() == {"uploadId"}):
            return await self._relay(call, await self._send(call, "DELETE", call.key, params))
        raise S3Error("NotImplemented", "this operation is not supported", 501)

    async def _put(self, call: Call) -> Response:
        """PutObject and UploadPart, streamed."""
        length = self._length(call)
        response = await self._send(
            call,
            "PUT",
            call.key,
            call.params,
            self._forwarded(call),
            self._body(call) if length else b"",
            self._upstream_hash(call),
            stream=True,
        )
        return await self._relay(call, response)

    async def _copy(self, call: Call) -> Response:
        """CopyObject and UploadPartCopy, from a bucket of the same org."""
        source = unquote(call.headers["x-amz-copy-source"]).lstrip("/")
        source, _, version = source.partition("?")
        source_bucket, _, source_key = source.partition("/")
        if version:
            raise S3Error("NotImplemented", "copying a version of an object is not supported", 501)
        found = call.caller.org.buckets.get(source_bucket)
        if found is None or not source_key:
            raise S3Error("NoSuchBucket", "The specified bucket does not exist", 404)
        headers = self._forwarded(call)
        headers["x-amz-copy-source"] = sigv4.uri_encode(f"/{found.upstream_name}/{source_key}")
        response = await self._send(call, "PUT", call.key, call.params, headers)
        return await self._renamed(call, response)


def preflight(request: Request, origin: str) -> dict[str, str]:
    """The answer to a CORS preflight from ``origin``: the methods S3 clients use, and the
    headers asked for that are S3's."""
    asked = request.headers.get("access-control-request-headers", "")
    headers = [
        name
        for name in (h.strip().lower() for h in asked.split(","))
        if name in CORS_HEADERS or name.startswith("x-amz-")
    ]
    return {
        "access-control-allow-origin": origin,
        "access-control-allow-methods": "GET, HEAD, PUT, POST, DELETE",
        **({"access-control-allow-headers": ", ".join(headers)} if headers else {}),
        "access-control-max-age": "3600",
        "vary": "Origin",
    }


async def discard(request: Request, limit: int = MAX_XML_BODY) -> bool:
    """Read what is left of the request's body, unless more than ``limit`` bytes are; returns
    whether it is all read."""
    size = 0
    try:
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                return False
    except RuntimeError:  # already read
        return True
    except ClientDisconnect:
        return False
    return True


def iso(timestamp: float) -> str:
    """A time as S3 writes it in listings."""
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(timestamp))


def create_app(settings: Settings | None = None) -> Starlette:
    """The proxy as an ASGI app; ``settings`` default to the environment's, read at startup."""

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncGenerator[None]:
        proxy = Proxy(settings or Settings.load())
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
