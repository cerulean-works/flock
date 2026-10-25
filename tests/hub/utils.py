"""Helpers for the hub tests: small Lance datasets and HF's own Lance builder to compare with."""

import datetime
import decimal
import json
from collections.abc import Iterator
from typing import Any

import datasets
import lance
import pyarrow as pa
from lance.dataset import LanceDataset

from flock.hub import HubConfig


def write(
    hub: HubConfig,
    repo_id: str,
    table: pa.Table,
    *,
    mode: str = "create",
    max_rows_per_file: int = 1_000_000,
) -> LanceDataset:
    """Writes `table` straight with Lance, in the hub's format, and returns the dataset."""
    org, name = repo_id.split("/")
    return lance.write_dataset(
        table,
        hub.dataset_uri(org, name),
        mode=mode,
        max_rows_per_file=max_rows_per_file,
        data_storage_version="2.2",
        enable_stable_row_ids=True,
        storage_options=hub.lance_storage_options(),
    )


def uri(hub: HubConfig, repo_id: str) -> str:
    org, name = repo_id.split("/")
    return hub.dataset_uri(org, name)


def numbers(n: int, start: int = 0) -> pa.Table:
    """`n` rows of `id` (int64), `text` (string) and `score` (float64)."""
    ids = list(range(start, start + n))
    return pa.table({"id": ids, "text": [f"row {i}" for i in ids], "score": [i / 4 for i in ids]})


def hf_lance(
    dataset_uri: str,
    streaming: bool,
    cache_dir: str,
    **config: Any,
) -> datasets.Dataset | datasets.IterableDataset:
    """The latest version of a local Lance dataset, loaded with HF's packaged Lance builder."""
    loaded = datasets.load_dataset(
        "lance",
        data_files=f"{dataset_uri}/_versions/*.manifest",
        streaming=streaming,
        cache_dir=cache_dir,
        **config,
    )
    split = loaded["train"]
    assert isinstance(split, datasets.Dataset | datasets.IterableDataset)
    return split


def all_types() -> pa.Table:
    """Three rows covering every column type the hub keeps, with nulls in every column."""
    blob = pa.field("blob", pa.large_binary(), metadata={"lance-encoding:blob": "true"})
    schema = pa.schema(
        [
            pa.field("i8", pa.int8()),
            pa.field("i16", pa.int16()),
            pa.field("i32", pa.int32()),
            pa.field("i64", pa.int64()),
            pa.field("u8", pa.uint8()),
            pa.field("u64", pa.uint64()),
            pa.field("f16", pa.float16()),
            pa.field("f32", pa.float32()),
            pa.field("f64", pa.float64()),
            pa.field("flag", pa.bool_()),
            pa.field("s", pa.string()),
            pa.field("ls", pa.large_string()),
            pa.field("bin", pa.binary()),
            pa.field("lbin", pa.large_binary()),
            blob,
            pa.field("st", pa.struct([("a", pa.int32()), ("b", pa.string())])),
            pa.field("l", pa.list_(pa.int32())),
            pa.field("emb", pa.list_(pa.float32(), 4)),
            pa.field("ts", pa.timestamp("us")),
            pa.field("tstz", pa.timestamp("us", tz="UTC")),
            pa.field("date", pa.date32()),
            pa.field("dec", pa.decimal128(10, 2)),
            pa.field("dict", pa.dictionary(pa.int32(), pa.string())),
            pa.field("meta", pa.string()),
        ]
    )
    data: dict[str, Any] = {
        "i8": [-128, None, 127],
        "i16": [-(2**15), None, 2**15 - 1],
        "i32": [-(2**31), None, 2**31 - 1],
        "i64": [-(2**63), None, 2**63 - 1],
        "u8": [0, None, 255],
        "u64": [0, None, 2**64 - 1],
        "f16": pa.array([1.5, None, -2.0], pa.float16()),
        "f32": [1.25, None, -3.5],
        "f64": [1e300, None, -1e-300],
        "flag": [True, None, False],
        "s": ["plain", None, ""],
        "ls": ["Grüße, 日本語, emoji 🦆", None, ""],
        "bin": [b"\x00\x01\x02", None, b""],
        "lbin": [b"zz-large", None, b""],
        "blob": [b"blob bytes", None, b""],
        "st": [{"a": 1, "b": "x"}, None, {"a": None, "b": None}],
        "l": [[1, 2, 3], None, []],
        "emb": [[0.5, 1.0, 1.5, 2.0], None, [0.0, 0.0, 0.0, 0.0]],
        "ts": [datetime.datetime(2026, 1, 2, 3, 4, 5, 6), None, datetime.datetime(1970, 1, 1)],
        "tstz": [datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.UTC), None, None],
        "date": [datetime.date(2026, 10, 25), None, datetime.date(1, 1, 1)],
        "dec": [decimal.Decimal("12345678.90"), None, decimal.Decimal("-0.01")],
        "dict": pa.array(["red", None, "red"]).dictionary_encode(),
        "meta": [json.dumps({"lang": "en", "tags": [1, 2]}), None, json.dumps({})],
    }
    return pa.table(data, schema=schema)


def without(table: pa.Table, *names: str) -> pa.Table:
    return table.drop_columns(list(names))


def rows(dataset: Any) -> list[dict[str, Any]]:
    return list(dataset)


def chunks(items: list, size: int) -> Iterator[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
