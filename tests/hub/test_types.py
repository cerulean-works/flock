"""Every column type round-trips: features, iteration and random access, in both modes."""

import json

import datasets
import pyarrow as pa
import pytest

from flock.hub import HubConfig, load_dataset
from tests.hub.utils import all_types, write

EXPECTED_FEATURES = datasets.Features(
    {
        "i8": datasets.Value("int8"),
        "i16": datasets.Value("int16"),
        "i32": datasets.Value("int32"),
        "i64": datasets.Value("int64"),
        "u8": datasets.Value("uint8"),
        "u64": datasets.Value("uint64"),
        "f16": datasets.Value("float16"),
        "f32": datasets.Value("float32"),
        "f64": datasets.Value("float64"),
        "flag": datasets.Value("bool"),
        "s": datasets.Value("string"),
        "ls": datasets.Value("large_string"),
        "bin": datasets.Value("binary"),
        "lbin": datasets.Value("large_binary"),
        "blob": datasets.Value("large_binary"),
        "st": {"a": datasets.Value("int32"), "b": datasets.Value("string")},
        "l": datasets.List(datasets.Value("int32")),
        "emb": datasets.List(datasets.Value("float32"), length=4),
        "ts": datasets.Value("timestamp[us]"),
        "tstz": datasets.Value("timestamp[us, tz=UTC]"),
        "date": datasets.Value("date32"),
        "dec": datasets.Value("decimal128(10, 2)"),
        "dict": datasets.Value("string"),
        "meta": datasets.Value("string"),
    }
)

MODES = pytest.mark.parametrize("streaming", [True, False], ids=["streaming", "materialized"])


@pytest.fixture
def types_repo(hub: HubConfig) -> str:
    # Two rows per fragment, so a type also crosses a fragment boundary.
    write(hub, "flock/types", all_types(), max_rows_per_file=2)
    return "flock/types"


@MODES
def test_features(hub: HubConfig, cache_dir: str, types_repo: str, streaming: bool) -> None:
    ds = load_dataset(types_repo, streaming=streaming, hub_config=hub, cache_dir=cache_dir)
    assert ds.features == EXPECTED_FEATURES


@MODES
def test_values_round_trip(
    hub: HubConfig, cache_dir: str, types_repo: str, streaming: bool
) -> None:
    ds = load_dataset(types_repo, streaming=streaming, hub_config=hub, cache_dir=cache_dir)
    expected = all_types().to_pylist()
    got = list(ds)
    assert got == expected
    # Spot checks of the values that most often go wrong.
    assert got[2]["u64"] == 2**64 - 1
    assert got[0]["i64"] == -(2**63)
    assert got[0]["ls"] == "Grüße, 日本語, emoji 🦆"
    assert got[2]["s"] == "" and got[2]["bin"] == b"" and got[2]["blob"] == b""
    assert got[0]["tstz"].utcoffset().total_seconds() == 0
    assert got[0]["tstz"].isoformat() == "2026-01-02T03:04:05+00:00"
    assert got[0]["ts"].tzinfo is None
    assert got[0]["dict"] == "red"
    assert json.loads(got[0]["meta"]) == {"lang": "en", "tags": [1, 2]}


@MODES
def test_nulls_in_every_type(
    hub: HubConfig, cache_dir: str, types_repo: str, streaming: bool
) -> None:
    ds = load_dataset(types_repo, streaming=streaming, hub_config=hub, cache_dir=cache_dir)
    second = list(ds)[1]
    assert set(second.values()) == {None}
    assert ds[1] == second


@MODES
def test_random_access_matches_iteration(
    hub: HubConfig, cache_dir: str, types_repo: str, streaming: bool
) -> None:
    ds = load_dataset(types_repo, streaming=streaming, hub_config=hub, cache_dir=cache_dir)
    rows = list(ds)
    assert [ds[i] for i in range(3)] == rows
    batch = ds[0:3]
    assert [{k: batch[k][i] for k in batch} for i in range(3)] == rows


@MODES
def test_columns_of_each_type(
    hub: HubConfig, cache_dir: str, types_repo: str, streaming: bool
) -> None:
    columns = ["blob", "emb", "dict", "tstz"]
    ds = load_dataset(
        types_repo, streaming=streaming, columns=columns, hub_config=hub, cache_dir=cache_dir
    )
    assert list(ds.features or {}) == columns
    assert list(ds) == all_types().select(columns).to_pylist()
    assert ds[-1] == all_types().select(columns).to_pylist()[-1]


def test_cell_over_16_mb(hub: HubConfig, cache_dir: str) -> None:
    big_text = "é" * (9 * 1024 * 1024)  # 18 MB of UTF-8
    big_blob = bytes(range(256)) * (17 * 4096)  # 17 MB
    schema = pa.schema(
        [
            pa.field("id", pa.int64()),
            pa.field("text", pa.large_string()),
            pa.field("data", pa.large_binary(), metadata={"lance-encoding:blob": "true"}),
        ]
    )
    table = pa.table({"id": [0, 1], "text": [big_text, "small"], "data": [big_blob, b"x"]}, schema)
    write(hub, "flock/big", table)

    for streaming in (True, False):
        ds = load_dataset("flock/big", streaming=streaming, hub_config=hub, cache_dir=cache_dir)
        first, second = list(ds)
        assert first["text"] == big_text and first["data"] == big_blob
        assert second == {"id": 1, "text": "small", "data": b"x"}
        assert ds[0]["data"] == big_blob
