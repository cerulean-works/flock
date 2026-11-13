"""Rename the tensors in safetensors files without reading their data (D123).

A safetensors file is an 8-byte little-endian header length, a JSON header naming each tensor
with its dtype, shape and byte range, and then the tensors' raw bytes. The byte ranges count
from the end of the header, so renaming tensors only rewrites the header, and the data is
copied unchanged. Nothing here imports torch or safetensors.

```python
from flock.hub.models.headers import hf_to_flock, rename_file

rename_file("upstream/model.safetensors", "staged/model.safetensors", hf_to_flock)
```
"""

import json
import os
import shutil
import struct
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import IO, Any

HF_PREFIX = "model."
"""What a Hugging Face tensor name gets in front of it in a Flock version (D126):
`HFCompatibleModel` holds the transformers model as `self.model`."""

MAX_HEADER = 100 * 2**20
"""The largest header the safetensors library reads."""

METADATA = "__metadata__"
CHUNK = 16 * 2**20

# safetensors dtype names, as torch names them.
DTYPES = {
    "BOOL": "bool",
    "U8": "uint8",
    "I8": "int8",
    "I16": "int16",
    "U16": "uint16",
    "I32": "int32",
    "U32": "uint32",
    "I64": "int64",
    "U64": "uint64",
    "F16": "float16",
    "BF16": "bfloat16",
    "F32": "float32",
    "F64": "float64",
    "F8_E4M3": "float8_e4m3fn",
    "F8_E5M2": "float8_e5m2",
}

type Rename = Callable[[str], str]


@dataclass(frozen=True)
class TensorInfo:
    """A tensor as a safetensors header describes it.

    Args:
        dtype (`str`): The safetensors dtype, such as `BF16`.
        shape (`tuple[int, ...]`): The tensor's shape.
    """

    dtype: str
    shape: tuple[int, ...]


def hf_to_flock(name: str) -> str:
    """Return a Hugging Face tensor name as `HFCompatibleModel`'s state dict names it.

    Args:
        name (`str`): A name in a Hugging Face checkpoint, such as `lm_head.weight`.

    Returns:
        `str`: The name with `model.` in front, such as `model.lm_head.weight`.
    """
    return HF_PREFIX + name


def read_header(stream: IO[bytes]) -> bytes:
    """Read a safetensors header from the start of a stream, leaving the stream at the data.

    Args:
        stream (`IO[bytes]`): A binary stream at the start of a safetensors file. It needn't
            be seekable, so an HTTP response body works.

    Returns:
        `bytes`: The JSON header, padding included.

    Raises:
        `ValueError`: If the stream ends early or the header is too large to be safetensors.
    """
    prefix = _read_exactly(stream, 8)
    (length,) = struct.unpack("<Q", prefix)
    if length > MAX_HEADER:
        raise ValueError(f"the safetensors header says it's {length} bytes, more than allowed")
    return _read_exactly(stream, length)


def _read_exactly(stream: IO[bytes], size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            raise ValueError(f"the file ended {size - len(data)} bytes into its safetensors header")
        data += chunk
    return bytes(data)


def _parse(header: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(header)
    except ValueError:
        raise ValueError("the safetensors header isn't JSON") from None
    if not isinstance(parsed, dict):
        raise ValueError("the safetensors header isn't a JSON object")  # noqa: TRY004
    return parsed


def tensors(header: bytes) -> dict[str, TensorInfo]:
    """List the tensors a safetensors header describes.

    Args:
        header (`bytes`): The JSON header, as [`read_header`] returns it.

    Returns:
        `dict[str, TensorInfo]`: Each tensor's name mapped to its dtype and shape, in the
        header's order.
    """
    return {
        name: TensorInfo(entry["dtype"], tuple(entry["shape"]))
        for name, entry in _parse(header).items()
        if name != METADATA
    }


def rename_header(header: bytes, rename: Rename) -> bytes:
    """Rename the tensors in a safetensors header.

    Byte ranges count from the end of the header, so they stay as they are, and the data that
    follows the old header follows the new one unchanged.

    Args:
        header (`bytes`): The JSON header, as [`read_header`] returns it.
        rename (`Callable[[str], str]`): Maps each tensor name to its new name, such as
            [`hf_to_flock`].

    Returns:
        `bytes`: What to write before the data: the 8-byte length and the new JSON header,
        padded with spaces so the data starts at a multiple of 8 bytes, as safetensors writes.

    Raises:
        `ValueError`: If the header isn't safetensors JSON, or two tensors get the same name.
    """
    parsed = _parse(header)
    renamed: dict[str, Any] = {}
    for name, entry in parsed.items():
        new = name if name == METADATA else rename(name)
        if new in renamed:
            raise ValueError(f"renaming {name} gives {new}, which another tensor already has")
        renamed[new] = entry
    encoded = json.dumps(renamed, separators=(",", ":")).encode()
    encoded += b" " * (-len(encoded) % 8)
    return struct.pack("<Q", len(encoded)) + encoded


def rename_stream(source: IO[bytes], target: IO[bytes], rename: Rename) -> dict[str, TensorInfo]:
    """Copy a safetensors stream to another with its tensors renamed, a chunk at a time.

    Args:
        source (`IO[bytes]`): A binary stream at the start of a safetensors file.
        target (`IO[bytes]`): Where the renamed file is written.
        rename (`Callable[[str], str]`): Maps each tensor name to its new name.

    Returns:
        `dict[str, TensorInfo]`: The renamed file's tensors.
    """
    header = read_header(source)
    new = rename_header(header, rename)
    target.write(new)
    shutil.copyfileobj(source, target, CHUNK)
    return tensors(new[8:])


def rename_file(
    source: str | os.PathLike[str], target: str | os.PathLike[str], rename: Rename
) -> dict[str, TensorInfo]:
    """Write a copy of a safetensors file with its tensors renamed.

    Args:
        source (`str` or `os.PathLike`): The safetensors file to read.
        target (`str` or `os.PathLike`): The file to write. Its folder must exist.
        rename (`Callable[[str], str]`): Maps each tensor name to its new name.

    Returns:
        `dict[str, TensorInfo]`: The written file's tensors.
    """
    with open(source, "rb") as reader, open(target, "wb") as writer:
        return rename_stream(reader, writer, rename)


def rename_index(index: Mapping[str, Any], rename: Rename) -> dict[str, Any]:
    """Rename the tensors in a safetensors index's `weight_map`.

    Args:
        index (`Mapping[str, Any]`): A parsed `model.safetensors.index.json`.
        rename (`Callable[[str], str]`): Maps each tensor name to its new name.

    Returns:
        `dict[str, Any]`: The index with each `weight_map` key renamed. Shard file names and
        other keys stay as they are.

    Raises:
        `ValueError`: If the index has no `weight_map`, or two tensors get the same name.
    """
    weight_map = index.get("weight_map")
    if not isinstance(weight_map, Mapping):
        raise ValueError("the safetensors index has no weight_map")  # noqa: TRY004
    renamed: dict[str, str] = {}
    for name, shard in weight_map.items():
        new = rename(name)
        if new in renamed:
            raise ValueError(f"renaming {name} gives {new}, which another tensor already has")
        renamed[new] = shard
    return {**index, "weight_map": renamed}
