import asyncio
import base64
import zlib

import pytest

from app.api.src.hub_proxy.chunked import ChunkedError, decode

DATA = b"hello, hub! " * 1000


def crc32(data: bytes) -> str:
    return base64.b64encode(zlib.crc32(data).to_bytes(4, "big")).decode()


def frame(data: bytes, size: int = 4096, trailer: str | None = None) -> bytes:
    out = b"".join(
        b"%x\r\n%s\r\n" % (len(data[i : i + size]), data[i : i + size])
        for i in range(0, len(data), size)
    )
    out += b"0\r\n"
    if trailer is not None:
        out += f"x-amz-checksum-crc32:{trailer}\r\n".encode()
    return out + b"\r\n"


def run(body: bytes, length: int, trailer: str | None = None, split: int = 7) -> bytes:
    """Decode ``body`` delivered in ``split``-byte pieces, as a network would."""

    async def stream():
        for i in range(0, len(body), split):
            yield body[i : i + split]

    async def collect() -> bytes:
        return b"".join([piece async for piece in decode(stream(), length, trailer)])

    return asyncio.run(collect())


@pytest.mark.parametrize("split", [1, 7, 4096, 1 << 20])
def test_decodes_whatever_the_network_splits(split):
    body = frame(DATA, trailer=crc32(DATA))
    assert run(body, len(DATA), "x-amz-checksum-crc32", split) == DATA


def test_without_a_trailer():
    assert run(frame(DATA), len(DATA)) == DATA


def test_unknown_checksums_pass_unchecked():
    body = frame(DATA)[:-2] + b"x-amz-checksum-crc64nvme:AAAAAAAAAAA=\r\n\r\n"
    assert run(body, len(DATA), "x-amz-checksum-crc64nvme") == DATA


def test_empty_body():
    assert run(frame(b"", trailer=crc32(b"")), 0, "x-amz-checksum-crc32") == b""


@pytest.mark.parametrize(
    ("body", "length"),
    [
        (frame(DATA, trailer=crc32(b"other")), len(DATA)),  # wrong checksum
        (frame(DATA), len(DATA)),  # missing checksum
        (frame(DATA, trailer=crc32(DATA)), len(DATA) + 1),  # shorter than declared
        (frame(DATA, trailer=crc32(DATA)), len(DATA) - 1),  # longer than declared
        (frame(DATA, trailer=crc32(DATA))[:100], len(DATA)),  # truncated
        (b"zz\r\nhi\r\n0\r\n\r\n", 2),  # bad size
        (b"2\r\nhiX0\r\n\r\n", 2),  # no CRLF after the data
    ],
)
def test_bad_bodies_are_refused(body, length):
    with pytest.raises(ChunkedError):
        run(body, length, "x-amz-checksum-crc32")


def test_last_piece_waits_for_the_checksum():
    """An upstream that got every declared byte would store the object, so a bad checksum
    must stop the body short."""
    body = frame(DATA, size=len(DATA) // 2, trailer=crc32(b"other"))
    received = []

    async def go():
        async def stream():
            yield body

        with pytest.raises(ChunkedError):
            async for piece in decode(stream(), len(DATA), "x-amz-checksum-crc32"):
                received.append(piece)

    asyncio.run(go())
    assert sum(map(len, received)) < len(DATA)
