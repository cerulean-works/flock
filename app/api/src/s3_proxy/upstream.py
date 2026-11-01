"""The real S3 server (Ceph RGW in production), reached with the global key.

Used by the proxy to forward requests, and by the API to create and purge buckets.
"""

import base64
import datetime as dt
import hashlib
import time
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterable, Mapping
from dataclasses import dataclass

import httpx

from app.api.src.s3_proxy import s3xml, sigv4
from app.api.src.settings import Settings

# DeleteObjects takes at most this many keys.
DELETE_BATCH = 1000


@dataclass(frozen=True)
class ListedObject:
    key: str
    size: int
    last_modified: float
    etag: str


@dataclass(frozen=True)
class Upload:
    """An unfinished multipart upload."""

    key: str
    upload_id: str
    initiated: float


@dataclass(frozen=True)
class Listing:
    objects: list[ListedObject]
    prefixes: list[str]
    cursor: str | None  # the continuation token of the next page, if there is one


def parse_time(text: str | None) -> float:
    """A listing's ``2026-10-27T12:00:00.000Z`` as a timestamp."""
    if not text:
        return 0.0
    return dt.datetime.fromisoformat(text).timestamp()


class UpstreamError(Exception):
    """An S3 error answered by the upstream server."""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status


def configured(settings: Settings) -> bool:
    """Whether S3 is on: the upstream server and its key are set."""
    return bool(
        settings.s3_upstream_endpoint
        and settings.s3_upstream_access_key_id
        and settings.s3_upstream_secret_access_key
    )


class Upstream:
    """Requests to the upstream S3 server, path-style and signed with the global key."""

    def __init__(self, settings: Settings, max_connections: int = 64) -> None:
        if not configured(settings):
            raise RuntimeError(
                "FLOCK_S3_UPSTREAM_ENDPOINT, FLOCK_S3_UPSTREAM_ACCESS_KEY_ID and "
                "FLOCK_S3_UPSTREAM_SECRET_ACCESS_KEY must all be set to use S3"
            )
        endpoint = settings.s3_upstream_endpoint or ""
        if "://" not in endpoint:
            endpoint = f"https://{endpoint}"
        self.endpoint = endpoint.rstrip("/")
        self.host = httpx.URL(self.endpoint).netloc.decode()
        self.region = settings.s3_upstream_region
        self.access_key_id = settings.s3_upstream_access_key_id or ""
        self.secret_access_key = settings.s3_upstream_secret_access_key or ""
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, read=300.0),
            limits=httpx.Limits(
                max_connections=max_connections, max_keepalive_connections=max_connections // 4
            ),
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def send(
        self,
        method: str,
        bucket: str | None,
        key: str | None = None,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        content: bytes | AsyncIterable[bytes] | None = None,
        payload_hash: str = sigv4.EMPTY_SHA256,
        stream: bool = False,
    ) -> httpx.Response:
        """A request for ``bucket`` (None: the service) or one of its objects, signed globally."""
        path = "/"
        if bucket is not None:
            path += sigv4.uri_encode(bucket, slash=False)
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
            self.access_key_id,
            self.secret_access_key,
            self.region,
            time.time(),
        )
        url = self.endpoint + path + (f"?{query}" if query else "")
        request = self.client.build_request(method, url, headers=signed, content=content)
        return await self.client.send(request, stream=stream)

    @staticmethod
    def _raise(response: httpx.Response) -> None:
        if response.status_code >= 300:
            code, message = s3xml.parse_error(response.content) or (
                "InternalError",
                f"the S3 server answered {response.status_code}",
            )
            raise UpstreamError(code, message, response.status_code)

    async def bucket_exists(self, bucket: str) -> bool:
        response = await self.send("HEAD", bucket)
        if response.status_code == 404:
            return False
        if response.status_code != 200:
            raise UpstreamError("InternalError", f"HEAD answered {response.status_code}", 502)
        return True

    async def create_bucket(self, bucket: str) -> None:
        body = b""
        if self.region != "us-east-1":
            root = ET.Element(f"{{{s3xml.NS}}}CreateBucketConfiguration")
            ET.SubElement(root, f"{{{s3xml.NS}}}LocationConstraint").text = self.region
            body = s3xml.dump(root)
        self._raise(
            await self.send(
                "PUT", bucket, content=body, payload_hash=hashlib.sha256(body).hexdigest()
            )
        )

    async def list_objects(
        self,
        bucket: str,
        prefix: str = "",
        delimiter: str | None = None,
        token: str | None = None,
        limit: int = 1000,
    ) -> Listing:
        """One page of ListObjectsV2: objects and, with ``delimiter``, common prefixes."""
        params = {"list-type": "2", "max-keys": str(limit)}
        if prefix:
            params["prefix"] = prefix
        if delimiter:
            params["delimiter"] = delimiter
        if token:
            params["continuation-token"] = token
        response = await self.send("GET", bucket, params=params)
        self._raise(response)
        root = ET.fromstring(response.content)
        objects, prefixes, top = [], [], {}
        for child in root:
            fields = {s3xml.local(field.tag): field for field in child}
            name = s3xml.local(child.tag)
            top[name] = child.text or ""
            if name == "Contents" and (key := fields.get("Key")) is not None:
                objects.append(
                    ListedObject(
                        key.text or "",
                        int(fields["Size"].text or 0) if "Size" in fields else 0,
                        parse_time(fields["LastModified"].text) if "LastModified" in fields else 0,
                        (fields["ETag"].text or "") if "ETag" in fields else "",
                    )
                )
            elif name == "CommonPrefixes" and (common := fields.get("Prefix")) is not None:
                prefixes.append(common.text or "")
        truncated = top.get("IsTruncated", "").lower() == "true"
        return Listing(objects, prefixes, top.get("NextContinuationToken") if truncated else None)

    async def head(self, bucket: str, key: str) -> httpx.Response | None:
        """An object's HEAD answer, or None if there's no such object."""
        response = await self.send("HEAD", bucket, key)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise UpstreamError("InternalError", f"HEAD answered {response.status_code}", 502)
        return response

    async def put_object(
        self, bucket: str, key: str, data: bytes, content_type: str | None = None
    ) -> None:
        headers = {"content-length": str(len(data))}
        if content_type:
            headers["content-type"] = content_type
        payload_hash = hashlib.sha256(data).hexdigest()
        self._raise(await self.send("PUT", bucket, key, None, headers, data, payload_hash))

    async def get_object(self, bucket: str, key: str) -> httpx.Response | None:
        """An object's GET answer, streamed (close it), or None if there's no such object."""
        response = await self.send("GET", bucket, key, stream=True)
        if response.status_code == 404:
            await response.aclose()
            return None
        if response.status_code != 200:
            await response.aread()
            await response.aclose()
            self._raise(response)
        return response

    async def put_empty(self, bucket: str, key: str) -> None:
        self._raise(await self.send("PUT", bucket, key, headers={"content-length": "0"}))

    async def delete_keys(self, bucket: str, keys: list[str]) -> None:
        """DeleteObjects for up to ``DELETE_BATCH`` keys; raises if any isn't deleted."""
        root = ET.Element("Delete")
        ET.SubElement(root, "Quiet").text = "true"
        for key in keys:
            ET.SubElement(ET.SubElement(root, "Object"), "Key").text = key
        body = s3xml.dump(root)
        headers = {
            "content-type": "application/xml",
            "content-md5": base64.b64encode(hashlib.md5(body).digest()).decode(),
        }
        response = await self.send(
            "POST", bucket, None, {"delete": ""}, headers, body, hashlib.sha256(body).hexdigest()
        )
        self._raise(response)
        if (failed := s3xml.parse_error(response.content)) is not None:
            raise UpstreamError(*failed, 500)
        result = ET.fromstring(response.content)
        if any(s3xml.local(element.tag) == "Error" for element in result):
            raise UpstreamError("InternalError", f"some objects of {bucket} weren't deleted", 500)

    async def create_upload(
        self,
        bucket: str,
        key: str,
        content_type: str | None,
        metadata: Mapping[str, str] | None = None,
    ) -> str:
        """CreateMultipartUpload, with ``metadata`` as ``x-amz-meta-*``; returns the upload id."""
        headers = {"content-type": content_type} if content_type else {}
        headers.update({f"x-amz-meta-{name}": value for name, value in (metadata or {}).items()})
        response = await self.send("POST", bucket, key, {"uploads": ""}, headers)
        self._raise(response)
        root = ET.fromstring(response.content)
        upload_id = next((c.text for c in root if s3xml.local(c.tag) == "UploadId"), None)
        if not upload_id:
            raise UpstreamError("InternalError", "no UploadId in the answer", 502)
        return upload_id

    async def abort_upload(self, bucket: str, key: str, upload_id: str) -> None:
        response = await self.send("DELETE", bucket, key, {"uploadId": upload_id})
        if response.status_code != 404:
            self._raise(response)

    @staticmethod
    def _copy_result(response: httpx.Response) -> str:
        """The ETag of a CopyObject or UploadPartCopy answer. Both can answer 200 with an error
        in the body, when the copy fails after it started."""
        Upstream._raise(response)
        if (failed := s3xml.parse_error(response.content)) is not None:
            raise UpstreamError(*failed, 500)
        root = ET.fromstring(response.content)
        etag = next((c.text for c in root if s3xml.local(c.tag) == "ETag"), None)
        if not etag:
            raise UpstreamError("InternalError", "no ETag in the copy's answer", 502)
        return etag

    @staticmethod
    def _source(bucket: str, key: str) -> str:
        return sigv4.uri_encode(f"/{bucket}/{key}")

    async def copy_object(self, source_bucket: str, source_key: str, bucket: str, key: str) -> None:
        """CopyObject, server side: ``source_key`` of ``source_bucket`` to ``key`` of ``bucket``
        (objects up to 5 GiB; see ``copy_large``)."""
        headers = {"x-amz-copy-source": self._source(source_bucket, source_key)}
        self._copy_result(await self.send("PUT", bucket, key, headers=headers))

    async def copy_large(
        self,
        source_bucket: str,
        source_key: str,
        size: int,
        bucket: str,
        key: str,
        part: int,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        """A multipart copy (UploadPartCopy) of an object of ``size`` bytes, in parts of
        ``part`` bytes, for objects too large for one CopyObject. Parts carry no metadata, so
        the source's is given as ``metadata``. Aborted if a part fails."""
        upload_id = await self.create_upload(bucket, key, None, metadata)
        try:
            etags = []
            for number, start in enumerate(range(0, size, part), start=1):
                end = min(start + part, size) - 1
                headers = {
                    "x-amz-copy-source": self._source(source_bucket, source_key),
                    "x-amz-copy-source-range": f"bytes={start}-{end}",
                }
                params = {"partNumber": str(number), "uploadId": upload_id}
                response = await self.send("PUT", bucket, key, params, headers)
                etags.append(self._copy_result(response))
            await self.complete_upload(bucket, key, upload_id, etags)
        except BaseException:
            await self.abort_upload(bucket, key, upload_id)
            raise

    async def complete_upload(
        self, bucket: str, key: str, upload_id: str, etags: list[str]
    ) -> None:
        """CompleteMultipartUpload, with the ETags of parts 1, 2, … in order."""
        root = ET.Element("CompleteMultipartUpload")
        for number, etag in enumerate(etags, start=1):
            part = ET.SubElement(root, "Part")
            ET.SubElement(part, "PartNumber").text = str(number)
            ET.SubElement(part, "ETag").text = etag
        body = s3xml.dump(root)
        response = await self.send(
            "POST",
            bucket,
            key,
            {"uploadId": upload_id},
            {"content-type": "application/xml"},
            body,
            hashlib.sha256(body).hexdigest(),
        )
        self._raise(response)
        if (failed := s3xml.parse_error(response.content)) is not None:
            raise UpstreamError(*failed, 500)

    async def get_range(self, bucket: str, key: str, length: int, start: int = 0) -> bytes | None:
        """``length`` bytes of an object from ``start`` (fewer at its end), or None if there's
        no such object."""
        headers = {"range": f"bytes={start}-{start + length - 1}"}
        response = await self.send("GET", bucket, key, headers=headers)
        if response.status_code == 404:
            return None
        if response.status_code == 416:  # an empty object
            return b""
        self._raise(response)
        return response.content

    async def list_all(
        self, bucket: str, prefix: str, limit: int | None = None
    ) -> list[ListedObject]:
        """Every object under ``prefix``; with ``limit``, stops once more than that are found
        (and returns them, so the caller sees the limit was passed)."""
        objects: list[ListedObject] = []
        token = None
        while True:
            page = await self.list_objects(bucket, prefix, None, token)
            objects.extend(page.objects)
            token = page.cursor
            if token is None or (limit is not None and len(objects) > limit):
                return objects

    async def list_uploads(self, bucket: str, prefix: str = "") -> list[Upload]:
        """Every unfinished multipart upload under ``prefix`` (ListMultipartUploads)."""
        uploads: list[Upload] = []
        markers: dict[str, str] = {}
        while True:
            params = {"uploads": "", **({"prefix": prefix} if prefix else {}), **markers}
            response = await self.send("GET", bucket, params=params)
            self._raise(response)
            root = ET.fromstring(response.content)
            top = {s3xml.local(child.tag): child.text or "" for child in root}
            for upload in root:
                if s3xml.local(upload.tag) != "Upload":
                    continue
                fields = {s3xml.local(child.tag): child.text or "" for child in upload}
                uploads.append(
                    Upload(fields["Key"], fields["UploadId"], parse_time(fields.get("Initiated")))
                )
            if top.get("IsTruncated", "").lower() != "true":
                return uploads
            markers = {
                "key-marker": top.get("NextKeyMarker", ""),
                "upload-id-marker": top.get("NextUploadIdMarker", ""),
            }

    async def _keys(self, bucket: str) -> list[str]:
        """Up to 1000 keys of ``bucket``."""
        return [o.key for o in (await self.list_objects(bucket)).objects]

    async def _abort_uploads(self, bucket: str) -> None:
        response = await self.send("GET", bucket, params={"uploads": ""})
        self._raise(response)
        root = ET.fromstring(response.content)
        for upload in root.iter():
            if s3xml.local(upload.tag) != "Upload":
                continue
            fields = {s3xml.local(child.tag): child.text or "" for child in upload}
            aborted = await self.send(
                "DELETE", bucket, fields["Key"], {"uploadId": fields["UploadId"]}
            )
            if aborted.status_code != 404:
                self._raise(aborted)

    async def purge_bucket(self, bucket: str) -> int:
        """Empty ``bucket`` (objects and unfinished multipart uploads) and delete it. Returns
        how many objects were deleted; a bucket that is already gone counts as done."""
        deleted = 0
        try:
            await self._abort_uploads(bucket)
            while keys := await self._keys(bucket):
                await self.delete_keys(bucket, keys)
                deleted += len(keys)
            response = await self.send("DELETE", bucket)
            self._raise(response)
        except UpstreamError as e:
            if e.code != "NoSuchBucket":
                raise
        return deleted
