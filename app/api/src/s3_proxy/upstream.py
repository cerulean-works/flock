"""The real S3 server (Ceph RGW in production), reached with the global key.

Used by the proxy to forward requests, and by the API to create and purge buckets.
"""

import base64
import hashlib
import time
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterable, Mapping

import httpx

from app.api.src.s3_proxy import s3xml, sigv4
from app.api.src.settings import Settings

# DeleteObjects takes at most this many keys.
DELETE_BATCH = 1000


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

    async def _keys(self, bucket: str) -> list[str]:
        """Up to 1000 keys of ``bucket``."""
        response = await self.send("GET", bucket, params={"list-type": "2", "max-keys": "1000"})
        self._raise(response)
        root = ET.fromstring(response.content)
        return [
            element.text
            for element in root.iter()
            if s3xml.local(element.tag) == "Key" and element.text is not None
        ]

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
                    "POST",
                    bucket,
                    None,
                    {"delete": ""},
                    headers,
                    body,
                    hashlib.sha256(body).hexdigest(),
                )
                self._raise(response)
                if (failed := s3xml.parse_error(response.content)) is not None:
                    raise UpstreamError(*failed, 500)
                result = ET.fromstring(response.content)
                if any(s3xml.local(element.tag) == "Error" for element in result):
                    raise UpstreamError(
                        "InternalError", f"some objects of {bucket} weren't deleted", 500
                    )
                deleted += len(keys)
            response = await self.send("DELETE", bucket)
            self._raise(response)
        except UpstreamError as e:
            if e.code != "NoSuchBucket":
                raise
        return deleted
