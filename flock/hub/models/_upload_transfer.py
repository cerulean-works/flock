"""Send the files in an upload journal through the S3 proxy."""

import base64
import hashlib
import threading
from dataclasses import dataclass, field
from typing import Any

from flock.hub.models._common import BUCKET, ModelError, Progress, TransferPool, concurrency
from flock.hub.models._upload_journal import Journal


def _s3_not_found(error: Exception) -> bool:
    response = getattr(error, "response", None) or {}
    code = response.get("Error", {}).get("Code")
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in ("404", "NoSuchKey", "NoSuchUpload", "NotFound") or status == 404


@dataclass
class _MultipartUpload:
    upload_id: str
    part_count: int
    etags: dict[int, str] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class UploadTransfer:
    """Send files and multipart parts through one pool, with at most one completion per file."""

    def __init__(self, journal: Journal, s3: Any, progress: Progress | None) -> None:
        self.journal = journal
        self.s3 = s3
        self.progress = progress
        self.part_size = journal.part_size
        self.prefix = f"{journal.ref.name}/u/{journal.ulid}/"

    def send_files(self, *, resume: bool) -> None:
        pool = TransferPool(concurrency())
        for path, file in sorted(self.journal.files.items()):
            if not file["done"]:
                pool.submit(self._send_file, pool, path, resume)
        pool.wait()

    def _send_file(self, pool: TransferPool, path: str, resume: bool) -> None:
        file = self.journal.files[path]
        key = self.prefix + path
        # A request can reach S3 before the process records it in the journal.
        if resume and self._object_matches(path, key):
            self.journal.record_transfer(path, upload_id=None, done=True)
            return
        if file["size"] <= self.part_size:
            self._put_file(path, key)
            return

        upload_id = file["upload_id"]
        stored_parts = None
        if resume and upload_id is not None:
            stored_parts = self._list_parts(key, upload_id)
        if upload_id is None or stored_parts is None:
            created = self.s3.create_multipart_upload(
                Bucket=BUCKET, Key=key, Metadata={"sha256": file["sha256"]}
            )
            upload_id = created["UploadId"]
            self.journal.record_transfer(path, upload_id=upload_id)
            stored_parts = {}

        part_count = (file["size"] + self.part_size - 1) // self.part_size
        multipart = _MultipartUpload(upload_id, part_count)
        for number in range(1, part_count + 1):
            pool.submit(self._send_part, path, key, multipart, number, stored_parts.get(number))

    def _object_matches(self, path: str, key: str) -> bool:
        """Check whether S3 already has a complete object with the declared size and hash."""
        file = self.journal.files[path]
        try:
            head = self.s3.head_object(Bucket=BUCKET, Key=key)
        except Exception as error:
            if _s3_not_found(error):
                return False
            raise
        return (
            head["ContentLength"] == file["size"]
            and head.get("Metadata", {}).get("sha256") == file["sha256"]
        )

    def _list_parts(self, key: str, upload_id: str) -> dict[int, str] | None:
        """Map existing part numbers to ETags. A missing multipart upload returns None."""
        etags = {}
        try:
            pages = self.s3.get_paginator("list_parts").paginate(
                Bucket=BUCKET, Key=key, UploadId=upload_id
            )
            for page in pages:
                for part in page.get("Parts", []):
                    etags[part["PartNumber"]] = part["ETag"].strip('"')
        except Exception as error:
            if _s3_not_found(error):
                return None
            raise
        return etags

    def _put_file(self, path: str, key: str) -> None:
        file = self.journal.files[path]
        data = (self.journal.source / path).read_bytes()
        if len(data) != file["size"]:
            raise ModelError(f"{path} changed while it was being uploaded")
        self.s3.put_object(
            Bucket=BUCKET,
            Key=key,
            Body=data,
            ContentMD5=base64.b64encode(hashlib.md5(data).digest()).decode(),
            Metadata={"sha256": file["sha256"]},
        )
        self.journal.record_transfer(path, upload_id=None, done=True)
        if self.progress is not None:
            self.progress(len(data))

    def _send_part(
        self,
        path: str,
        key: str,
        multipart: _MultipartUpload,
        number: int,
        stored_etag: str | None,
    ) -> None:
        offset = (number - 1) * self.part_size
        with (self.journal.source / path).open("rb") as file:
            file.seek(offset)
            data = file.read(self.part_size)

        md5 = hashlib.md5(data)
        etag = md5.hexdigest()
        # The proxy's S3 backend uses the MD5 as the part ETag. Reuse matching parts.
        if stored_etag != etag:
            answer = self.s3.upload_part(
                Bucket=BUCKET,
                Key=key,
                UploadId=multipart.upload_id,
                PartNumber=number,
                Body=data,
                ContentMD5=base64.b64encode(md5.digest()).decode(),
            )
            etag = answer["ETag"].strip('"')
            if self.progress is not None:
                self.progress(len(data))

        with multipart.lock:
            multipart.etags[number] = etag
            if len(multipart.etags) < multipart.part_count:
                return

        # Only the worker that records the final part reaches completion.
        parts = [
            {"PartNumber": number, "ETag": f'"{etag}"'}
            for number, etag in sorted(multipart.etags.items())
        ]
        self.s3.complete_multipart_upload(
            Bucket=BUCKET,
            Key=key,
            UploadId=multipart.upload_id,
            MultipartUpload={"Parts": parts},
        )
        self.journal.record_transfer(path, upload_id=None, done=True)
