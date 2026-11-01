"""Decoding aws-chunked bodies with an unsigned trailer (``STREAMING-UNSIGNED-PAYLOAD-TRAILER``).

Recent AWS SDKs and the aws CLI send uploads this way by default: the body is framed in chunks
(``<hex size>\\r\\n<data>\\r\\n``, ending with a zero-size chunk) and followed by trailing headers
that carry a checksum of the data, such as ``x-amz-checksum-crc32``. The proxy unframes the body
and forwards plain bytes, so the upstream needn't support the encoding; it checks the trailing
checksum itself when it knows the algorithm.
"""

import base64
import hashlib
import zlib
from collections.abc import AsyncIterable, AsyncIterator

from app.api.src.s3_proxy.sigv4 import STREAMING_UNSIGNED_TRAILER

__all__ = ["STREAMING_UNSIGNED_TRAILER", "ChunkedError", "decode"]

# Larger chunk-size lines or trailers than any SDK sends mean a malformed body.
MAX_LINE = 4096


class ChunkedError(ValueError):
    """The body isn't valid aws-chunked framing, or its trailing checksum doesn't match."""


class _Crc32:
    def __init__(self) -> None:
        self.value = 0

    def update(self, data: bytes) -> None:
        self.value = zlib.crc32(data, self.value)

    def digest(self) -> bytes:
        return self.value.to_bytes(4, "big")


def _checker(name: str):
    """A running checksum for the trailer header ``name``, or None if the proxy can't check it
    (CRC32C and CRC64NVME need libraries the API doesn't have; TLS still protects the bytes)."""
    if name == "x-amz-checksum-crc32":
        return _Crc32()
    if name == "x-amz-checksum-sha1":
        return hashlib.sha1()
    if name == "x-amz-checksum-sha256":
        return hashlib.sha256()
    return None


async def decode(
    stream: AsyncIterable[bytes], decoded_length: int, trailer: str | None
) -> AsyncIterator[bytes]:
    """
    Yield the data of an aws-chunked body.

    Args:
        stream: the raw request body.
        decoded_length: ``x-amz-decoded-content-length``; the data must be exactly this long.
        trailer: the ``x-amz-trailer`` header (the trailing checksum's name), if any.

    Raises:
        ChunkedError: on bad framing, a length other than ``decoded_length``, or a trailing
            checksum that doesn't match the data.
    """
    name = (trailer or "").strip().lower()
    checksum = _checker(name) if name else None
    buffer = bytearray()
    source = aiter(stream)
    done = False

    async def fill() -> bool:
        nonlocal done
        if done:
            return False
        try:
            buffer.extend(await anext(source))
        except StopAsyncIteration:
            done = True
            return False
        return True

    async def line() -> bytes:
        while (end := buffer.find(b"\r\n")) < 0:
            if len(buffer) > MAX_LINE or not await fill():
                raise ChunkedError("truncated or malformed chunk framing")
        out = bytes(buffer[:end])
        del buffer[: end + 2]
        return out

    # The last piece is held back until the trailer checks out: once the upstream has every
    # byte of the declared length, it stores the object whatever happens next.
    pending = b""
    total = 0
    while True:
        header = await line()
        try:
            size = int(header.split(b";", 1)[0], 16)
        except ValueError:
            raise ChunkedError("bad chunk size") from None
        if size == 0:
            break
        remaining = size
        while remaining:
            if not buffer and not await fill():
                raise ChunkedError("truncated chunk")
            piece = bytes(buffer[:remaining])
            del buffer[: len(piece)]
            remaining -= len(piece)
            total += len(piece)
            if total > decoded_length:
                raise ChunkedError("the body is longer than x-amz-decoded-content-length")
            if checksum is not None:
                checksum.update(piece)
            if pending:
                yield pending
            pending = piece
        if await line() != b"":
            raise ChunkedError("chunk data is not followed by CRLF")
    if total != decoded_length:
        raise ChunkedError("the body is shorter than x-amz-decoded-content-length")

    trailers: dict[str, str] = {}
    while header := await line():
        key, _, value = header.decode("latin-1").partition(":")
        trailers[key.strip().lower()] = value.strip()
    if checksum is not None:
        sent = trailers.get(name)
        if sent is None or base64.b64decode(sent) != checksum.digest():
            raise ChunkedError(f"the body does not match its {name} trailer")
    if pending:
        yield pending
