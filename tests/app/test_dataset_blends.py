"""Blends end to end (023, D174–D178): made by the API's job, read back as users read datasets,
and checked against the sources they were sampled from, row by row."""

from collections import Counter
from collections.abc import Callable
from datetime import timedelta
from itertools import pairwise
from typing import Any

import pytest

from tests.app.conftest import Client, S3Flock
from tests.app.test_dataset_forks import blend, pins, wait_job
from tests.app.test_datasets import (
    DATASETS,
    Writer,
    flock,  # noqa: F401 - fixture
    get,
    keys,
    table,
    upstream_name,
    writer,  # noqa: F401 - fixture
)

lance = pytest.importorskip("lance")
pa = pytest.importorskip("pyarrow")

META = pa.struct([("x", pa.int64()), ("y", pa.string())])


def web_table(start: int, count: int) -> Any:
    ids = list(range(start, start + count))
    return pa.table(
        {
            "id": pa.array(ids, pa.int64()),
            "text": [f"web {i}" for i in ids],
            "lang": ["fr" if i % 3 == 0 else "en" for i in ids],
            "score": pa.array([i / 4 for i in ids], pa.float32()),
            "meta": pa.array([{"x": i, "y": f"w{i}"} for i in ids], META),
            "tags": pa.array([[f"t{i}", "web"] for i in ids], pa.list_(pa.string())),
        }
    )


def code_table(start: int, count: int) -> Any:
    ids = list(range(start, start + count))
    return pa.table(
        {
            "doc_id": pa.array(ids, pa.int32()),
            "body": [f"code {i}" for i in ids],
            "meta": pa.array([{"x": -i, "y": f"c{i}"} for i in ids], META),
            "quality": pa.array([i / 8 for i in ids], pa.float64()),
            "tags": pa.array([[f"c{i}"] for i in ids], pa.list_(pa.string())),
            "license": ["mit"] * count,
        }
    )


def column(name: str, type: str, nullable: bool = True, **froms: Any) -> dict[str, Any]:
    return {"name": name, "type": type, "nullable": nullable, "from": froms}


def rows_of(w: Writer, path: str, **kwargs: Any) -> list[dict[str, Any]]:
    return w.open(path).to_table(**kwargs).to_pylist()


def ids_from(rows: list[dict[str, Any]], source: str, key: str = "id") -> list[int]:
    return [r[key] for r in rows if r["source"] == source]


def preview(api: Client, body: dict[str, Any]) -> dict[str, Any]:
    got = api.post(f"{DATASETS}/blend/preview", body)
    assert got.status == 200, got.body
    return got.json()


def blend_files(flock: S3Flock, path: str) -> list[str]:  # noqa: F811
    """The data files in the blend's own folder: only what it wrote, not what it reads."""
    prefix = f"{path}/data/"
    return [k.removeprefix(prefix) for k in keys(flock, upstream_name(flock), prefix)]


# The map most tests use: web and code renamed onto one schema, with every kind of source.
MAP = [
    column("id", "int64", web={"column": "id"}, code={"column": "doc_id", "cast": True}),
    column("text", "string", web={"column": "text"}, code={"column": "body"}),
    column("source", "string", False, web={"value": "web"}, code={"value": "code"}),
]


# ---------------------------------------------------------------------- reading (D174)


def test_a_blend_reads_back_the_same_through_lance_and_the_api(
    flock: S3Flock,  # noqa: F811
    writer: Writer,  # noqa: F811
    wait_for: Callable[..., Any],
) -> None:
    writer.write("web", web_table(0, 300), max_rows_per_file=70)
    writer.write("code", code_table(0, 200), max_rows_per_file=60)
    writer.open("code").delete("doc_id < 20")
    api = flock.client()
    body = {
        "sources": [{"path": "web", "rows": 120}, {"path": "code", "rows": 80}],
        "columns": MAP,
        "seed": 42,
        "to": "mix",
    }
    done = blend(api, body, wait_for)
    assert done["result"] == {"path": "mix", "version": 2, "rows": 200}

    ds = writer.open("mix")
    full = ds.to_table()
    rows = full.to_pylist()
    assert ds.count_rows() == len(rows) == 200
    assert full.schema == pa.schema(
        [
            pa.field("id", pa.int64()),
            pa.field("text", pa.string()),
            pa.field("source", pa.string(), nullable=False),
        ]
    )
    # Every row is its source's row, unchanged, and no row is taken twice.
    assert all(r["text"] == f"{r['source']} {r['id']}" for r in rows)
    assert len(set(ids_from(rows, "web"))) == 120 and len(set(ids_from(rows, "code"))) == 80
    assert set(ids_from(rows, "web")) <= set(range(300))
    assert set(ids_from(rows, "code")) <= set(range(20, 200))  # deleted rows stay deleted

    # Lance's other ways in agree with the full scan.
    assert (
        pa.concat_tables(
            [pa.Table.from_batches([b]) for b in ds.to_batches(batch_size=17)]
        ).to_pylist()
        == rows
    )
    assert ds.take([0, 7, 199]).to_pylist() == [rows[0], rows[7], rows[199]]
    assert ds.to_table(filter="source = 'code'").to_pylist() == [
        r for r in rows if r["source"] == "code"
    ]
    assert ds.to_table(columns=["text"]).to_pylist() == [{"text": r["text"]} for r in rows]
    assert ds.count_rows("id < 100") == sum(r["id"] < 100 for r in rows)

    # And so do the Datasets tab's: its info, its pages of rows and its versions.
    info = get(api, f"{DATASETS}/info", path="mix").json()
    assert (info["rows"], info["columns"], info["version"]) == (200, 3, 2)
    assert [(f["name"], f["type"], f["nullable"]) for f in info["schema"]] == [
        ("id", "int64", True),
        ("text", "string", True),
        ("source", "string", False),
    ]
    paged = []
    for offset in range(0, 200, 50):
        page = get(api, f"{DATASETS}/rows", path="mix", offset=offset, limit=50).json()
        assert page["total"] == 200
        paged += [[cell["v"] for cell in row] for row in page["rows"]]
    assert paged == [[r["id"], r["text"], r["source"]] for r in rows]
    history = get(api, f"{DATASETS}/versions", path="mix").json()
    assert [(v["version"], v["rows"], v["tags"]) for v in history["versions"]] == [
        (2, 200, ["blend"]),
        (1, 500, []),  # the sources' fragments, before the rows not taken are deleted
    ]

    # The blend wrote only fill files for the cast and the values, one per fragment it lists:
    # every row it has is read from the sources' own files.
    written = blend_files(flock, "mix")
    assert written and all(f.startswith("flock-fill-") for f in written)
    assert len(written) == len(ds.get_fragments())


def test_fragments_of_different_sources_are_interleaved(
    flock: S3Flock,  # noqa: F811
    writer: Writer,  # noqa: F811
    wait_for: Callable[..., Any],
) -> None:
    writer.write("web", web_table(0, 300), max_rows_per_file=30)
    writer.write("code", code_table(0, 300), max_rows_per_file=30)
    api = flock.client()
    body = {
        "sources": [{"path": "web", "rows": 150}, {"path": "code", "rows": 150}],
        "columns": MAP,
        "seed": 42,
        "to": "mix",
    }
    blend(api, body, wait_for)
    # A scan in order isn't all of one source, then all of the other.
    order = [r["source"] for r in rows_of(writer, "mix", columns=["source"])]
    runs = 1 + sum(a != b for a, b in pairwise(order))
    assert runs >= 4, order


# ---------------------------------------------------------------------- sampling (D174)


def test_sampling_is_seeded_spread_out_and_independent_per_source(
    flock: S3Flock,  # noqa: F811
    writer: Writer,  # noqa: F811
    wait_for: Callable[..., Any],
) -> None:
    writer.write("web", web_table(0, 300), max_rows_per_file=70)
    writer.write("code", code_table(0, 200), max_rows_per_file=60)
    api = flock.client()

    def web_ids(to: str, web: int, code: int, seed: int) -> set[int]:
        sources = [{"path": "web", "rows": web}, {"path": "code", "rows": code}]
        blend(api, {"sources": sources, "columns": MAP, "seed": seed, "to": to}, wait_for)
        return set(ids_from(rows_of(writer, to), "web"))

    taken = web_ids("a", 150, 50, seed=42)
    assert len(taken) == 150
    # Spread over the source, not its first rows: each of its fragments gives about half.
    for start in range(0, 300, 70):
        size = min(70, 300 - start)
        got = len(taken & set(range(start, start + size)))
        assert abs(got - size / 2) <= 0.35 * size, (start, got)

    # The same seed takes the same rows, whatever the other sources ask for.
    assert web_ids("b", 150, 120, seed=42) == taken
    # A smaller amount of the same seed takes some of the same rows, and a larger one more.
    smaller = web_ids("c", 60, 50, seed=42)
    larger = web_ids("d", 280, 50, seed=42)
    assert len(smaller) == 60 and len(larger) == 280
    assert smaller < taken < larger
    # Another seed takes other rows: about a half of them again, by chance.
    other = web_ids("e", 150, 50, seed=43)
    assert len(other) == 150 and 40 <= len(other & taken) <= 110

    # Taking every row of a source takes exactly its live rows.
    writer.open("code").delete("doc_id % 4 = 0")
    sources = [{"path": "web", "rows": 10}, {"path": "code", "rows": 150}]
    blend(api, {"sources": sources, "columns": MAP, "seed": 1, "to": "all"}, wait_for)
    assert sorted(ids_from(rows_of(writer, "all"), "code")) == [i for i in range(200) if i % 4]


def test_percentages_split_the_total_exactly(
    flock: S3Flock,  # noqa: F811
    writer: Writer,  # noqa: F811
    wait_for: Callable[..., Any],
) -> None:
    writer.write("a", table(0, 300))
    writer.write("b", table(1000, 300))
    writer.write("c", table(2000, 300))
    api = flock.client()

    def counts(to: str, fractions: list[float], total: int) -> list[int]:
        sources = [{"path": p, "fraction": f} for p, f in zip("abc", fractions, strict=False)]
        blend(api, {"sources": sources, "total": total, "seed": 9, "to": to}, wait_for)
        ids = writer.open(to).to_table()["id"].to_pylist()
        assert len(ids) == len(set(ids)) == total
        return [sum(lo <= i < lo + 300 for i in ids) for lo in (0, 1000, 2000)][: len(sources)]

    # The rows left after rounding down go to the largest remainders, ties in order.
    assert counts("thirds", [1 / 3, 1 / 3, 1 / 3], 100) == [34, 33, 33]
    assert counts("split", [0.66, 0.34], 101) == [67, 34]
    # 66.6% and 33.3% are within 0.1% of 100%, and are scaled to it.
    assert counts("close", [0.666, 0.333], 300) == [200, 100]

    origin = get(api, f"{DATASETS}/info", path="split").json()["origin"]
    assert origin["spec"] == {"mode": "fraction", "total": 101}
    assert [(s["path"], s["fraction"], s["rows"]) for s in origin["sources"]] == [
        ("a", 0.66, 67),
        ("b", 0.34, 34),
    ]

    off = {"sources": [{"path": "a", "fraction": 0.6}, {"path": "b", "fraction": 0.3}]}
    assert preview(api, {**off, "total": 10})["problems"] == [
        "The percentages add up to 90%, not 100%."
    ]
    refused = api.post(f"{DATASETS}/blend", {**off, "total": 10, "seed": 1, "to": "off"})
    assert refused.status == 422
    mixed = {"sources": [{"path": "a", "fraction": 0.5}, {"path": "b", "rows": 5}]}
    assert api.post(f"{DATASETS}/blend/preview", mixed).status == 422
    no_total = {"sources": [{"path": "a", "fraction": 0.5}, {"path": "b", "fraction": 0.5}]}
    assert preview(api, no_total)["problems"] == ["Enter the total rows to split."]


def test_oversampling_repeats_every_live_row_evenly(
    flock: S3Flock,  # noqa: F811
    writer: Writer,  # noqa: F811
    wait_for: Callable[..., Any],
) -> None:
    writer.write("big", table(0, 100))
    writer.write("small", table(1000, 30), max_rows_per_file=12)
    writer.open("small").delete("id < 1005")  # 25 rows left
    api = flock.client()
    columns = [
        column("id", "int64", big={"column": "id"}, small={"column": "id"}),
        column("source", "string", False, big={"value": "big"}, small={"value": "small"}),
    ]
    sources = [{"path": "big", "rows": 10}, {"path": "small", "rows": 60}]
    body = {"sources": sources, "columns": columns, "seed": 3, "to": "up"}
    assert preview(api, body)["problems"] == [
        (
            "small has 25 rows, fewer than the 60 asked for. Ask for fewer, or allow taking a "
            "source more than once."
        )
    ]
    assert api.post(f"{DATASETS}/blend", body).status == 422

    blend(api, {**body, "replace": True}, wait_for)
    small = Counter(ids_from(rows_of(writer, "up"), "small"))
    # 60 of 25 rows is every row twice, and 10 of them a third time.
    assert set(small) == set(range(1005, 1030))
    assert sorted(Counter(small.values()).items()) == [(2, 15), (3, 10)]
    # Copies of a fragment share its fill file: one per source fragment, not per copy.
    assert len(blend_files(flock, "up")) == len(writer.open("small").get_fragments()) + 1

    exact = {**body, "replace": True, "to": "twice"}
    exact["sources"] = [{"path": "big", "rows": 10}, {"path": "small", "rows": 50}]
    blend(api, exact, wait_for)
    assert set(Counter(ids_from(rows_of(writer, "twice"), "small")).values()) == {2}
    origin = get(api, f"{DATASETS}/info", path="twice").json()["origin"]
    assert origin["replace"] is True
    assert [(s["rows"], s["live"]) for s in origin["sources"]] == [(10, 100), (50, 25)]


# ---------------------------------------------------------------------- column maps (D175)


def test_the_automatic_map_matches_by_name_and_names_what_it_cant(
    flock: S3Flock,  # noqa: F811
    writer: Writer,  # noqa: F811
) -> None:
    writer.write("web", web_table(0, 20))
    more = web_table(100, 20)
    more = (
        more.drop_columns(["lang"])
        .set_column(0, "id", more["id"].cast(pa.int32()))  # widens to int64
        .set_column(2, "score", more["score"].cast(pa.float64()))  # can't narrow
        .append_column("extra", pa.array([1] * 20, pa.int8()))
    )
    writer.write("more", more)
    api = flock.client()
    sources = [{"path": "web", "rows": 5}, {"path": "more", "rows": 5}]
    got = preview(api, {"sources": sources})
    assert got["automatic"] is True
    assert [(c["name"], c["type"], c["from"]["more"]) for c in got["columns"]] == [
        ("id", "int64", {"column": "id", "cast": True}),
        ("text", "string", {"column": "text"}),
        ("lang", "string", {"null": True}),
        ("score", "float", {"column": "score"}),
        ("meta", str(META), {"column": "meta"}),
        ("tags", "list<item: string>", {"column": "tags"}),
    ]
    assert all(c["from"]["web"] == {"column": c["name"]} for c in got["columns"])
    assert next(c for c in got["columns"] if c["name"] == "lang")["nullable"] is True
    assert got["unused"] == {"web": [], "more": [{"name": "extra", "type": "int8"}]}
    assert got["column_problems"] == [
        "score: score in more is double, which can't become float without losing values."
    ]
    assert got["problems"] == got["column_problems"]
    assert [(w["column"], w["source"], w["kind"]) for w in got["writes"]] == [
        ("id", "more", "cast")
    ]


def test_a_column_map_renames_reorders_casts_copies_and_fills(
    flock: S3Flock,  # noqa: F811
    writer: Writer,  # noqa: F811
    wait_for: Callable[..., Any],
) -> None:
    writer.write("web", web_table(0, 200), max_rows_per_file=45)
    writer.write("code", code_table(0, 100), max_rows_per_file=30)
    api = flock.client()
    columns = [
        # Renamed in both sources, and in another order than either source's.
        column("body", "large_string",
               web={"column": "text", "cast": True}, code={"column": "body", "cast": True}),
        column("doc", "int64", web={"column": "id"}, code={"column": "doc_id", "cast": True}),
        # Nested columns keep their children.
        column("meta", str(META), web={"column": "meta"}, code={"column": "meta"}),
        column("tags", "list<item: string>", web={"column": "tags"}, code={"column": "tags"}),
        # One source column into two outputs: the second is written out as a copy.
        column("doc_again", "int64", web={"column": "id"}, code={"null": True}),
        column("weight", "double",
               web={"column": "score", "cast": True}, code={"column": "quality"}),
        column("lang", "string", web={"column": "lang"}, code={"null": True}),
        column("is_code", "bool", False, web={"value": False}, code={"value": True}),
        column("epoch", "int32", False, web={"value": 1}, code={"value": 2}),
        column("mix", "double", False, web={"value": 0.25}, code={"value": 0.75}),
    ]  # fmt: skip
    sources = [{"path": "web", "rows": 90}, {"path": "code", "rows": 60}]
    body = {"sources": sources, "columns": columns, "seed": 11, "to": "mapped"}
    checked = preview(api, body)
    assert checked["problems"] == []
    assert sorted((w["column"], w["source"], w["kind"]) for w in checked["writes"]) == sorted(
        [
            ("body", "web", "cast"),
            ("body", "code", "cast"),
            ("doc", "code", "cast"),
            ("doc_again", "web", "copy"),
            ("weight", "web", "cast"),
            ("is_code", "web", "value"),
            ("is_code", "code", "value"),
            ("epoch", "web", "value"),
            ("epoch", "code", "value"),
            ("mix", "web", "value"),
            ("mix", "code", "value"),
        ]
    )
    # Only code's license goes unused; web gives every one of its columns.
    assert [c["name"] for c in checked["unused"]["code"]] == ["license"]
    assert checked["unused"]["web"] == []
    blend(api, body, wait_for)

    ds = writer.open("mapped")
    assert ds.schema == pa.schema(
        [
            pa.field("body", pa.large_string()),
            pa.field("doc", pa.int64()),
            pa.field("meta", META),
            pa.field("tags", pa.list_(pa.string())),
            pa.field("doc_again", pa.int64()),
            pa.field("weight", pa.float64()),
            pa.field("lang", pa.string()),
            pa.field("is_code", pa.bool_(), nullable=False),
            pa.field("epoch", pa.int32(), nullable=False),
            pa.field("mix", pa.float64(), nullable=False),
        ]
    )
    rows = ds.to_table().to_pylist()
    web = [r for r in rows if not r["is_code"]]
    code = [r for r in rows if r["is_code"]]
    assert (len(web), len(code)) == (90, 60)
    for r in web:
        i = r["doc"]
        assert r == {
            "body": f"web {i}", "doc": i, "meta": {"x": i, "y": f"w{i}"},
            "tags": [f"t{i}", "web"], "doc_again": i, "weight": i / 4,
            "lang": "fr" if i % 3 == 0 else "en", "is_code": False, "epoch": 1, "mix": 0.25,
        }  # fmt: skip
    for r in code:
        i = r["doc"]
        assert r == {
            "body": f"code {i}", "doc": i, "meta": {"x": -i, "y": f"c{i}"},
            "tags": [f"c{i}"], "doc_again": None, "weight": i / 8, "lang": None,
            "is_code": True, "epoch": 2, "mix": 0.75,
        }  # fmt: skip
    # A projection of a nested column's child, and a filter on a filled column, read right.
    assert ds.to_table(columns={"y": "meta.y"}, filter="is_code").to_pylist() == [
        {"y": f"c{r['doc']}"} for r in code
    ]
    assert get(api, f"{DATASETS}/info", path="mapped").json()["origin"]["columns"] == columns


def test_every_column_map_problem_is_named_and_nothing_is_written(
    flock: S3Flock,  # noqa: F811
    writer: Writer,  # noqa: F811
) -> None:
    writer.write("web", web_table(0, 20))
    writer.write("code", code_table(0, 20))
    writer.write("nn", pa.table({"k": pa.array([1, 2], pa.int64())}, schema=pa.schema(
        [pa.field("k", pa.int64(), nullable=False)])))  # fmt: skip
    api = flock.client()
    columns = [
        column("", "string", web={"column": "text"}, code={"column": "body"}),
        column("dup", "string", web={"column": "text"}, code={"column": "body"}),
        column("dup", "string", web={"column": "text"}, code={"column": "body"}),
        column("odd", "decimal-ish", web={"column": "text"}, code={"column": "body"}),
        column("half", "string", web={"column": "text"}),
        column("gone", "string", web={"column": "nope"}, code={"column": "body"}),
        column("narrow", "int16", web={"column": "id"}, code={"column": "doc_id"}),
        column("inexact", "double", web={"column": "id"}, code={"null": True}),
        column("word", "int64", web={"value": "seven"}, code={"value": 7}),
        column("nested", str(META), web={"value": 1}, code={"column": "meta"}),
        column("strict", "string", False, web={"null": True}, code={"value": "x"}),
        column("loose", "string", False, web={"column": "text"}, code={"value": "x"}),
        column("vague", "string", web={"column": "text"}, code={"what": 1}),
    ]
    sources = [{"path": "web", "rows": 5}, {"path": "code", "rows": 5}]
    body = {"sources": sources, "columns": columns, "seed": 1, "to": "never"}
    got = preview(api, body)
    assert (
        got["problems"]
        == got["column_problems"]
        == [
            "Every column needs a name.",
            "Two columns are named dup.",
            "odd: decimal-ish isn't a column type Flock knows.",
            "half: say where code gets it from.",
            "gone: web has no column nope.",
            "narrow: id in web is int64, which can't become int16 without losing values.",
            "narrow: doc_id in code is int32, which can't become int16 without losing values.",
            "inexact: id in web is int64, which can't become double without losing values.",
            "word: 'seven' for web isn't a int64 value.",
            f"nested: a {META} column can't be filled with a value.",
            (
                "strict can't be null, but web gives it null. Make it nullable, or take a "
                "column or a value."
            ),
            "loose can't be null, but text in web can hold nulls. Make it nullable.",
            "vague: say where code gets it from.",
        ]
    )
    refused = api.post(f"{DATASETS}/blend", body)
    assert refused.status == 422
    assert "Every column needs a name." in refused.json()["error"]
    assert keys(flock, upstream_name(flock), "never/") == []
    assert pins(writer, "web") == {} and pins(writer, "code") == {}

    # A source that gives only nulls has no rows of its own to give: refused, not built.
    only_nulls = [column("id", "int64", web={"column": "id"}, code={"null": True})]
    assert preview(api, {"sources": sources, "columns": only_nulls})["problems"] == [
        "code gives the blend only nulls. Take at least one of its columns, or a value."
    ]
    assert preview(api, {"sources": sources, "columns": []})["problems"] == [
        "A blend needs at least one column."
    ]
    # A column that can't hold nulls can be a non-nullable output.
    strict = [
        column("k", "int64", False, nn={"column": "k"}, web={"value": 0}),
    ]
    nn = [{"path": "nn", "rows": 2}, {"path": "web", "rows": 2}]
    assert preview(api, {"sources": nn, "columns": strict})["problems"] == []


# ---------------------------------------------------------------------- sources (D173, D177)


def test_a_blend_reads_its_sources_pinned_versions_whatever_happens_to_them_later(
    flock: S3Flock,  # noqa: F811
    writer: Writer,  # noqa: F811
    wait_for: Callable[..., Any],
) -> None:
    writer.write("web", table(0, 100), max_rows_per_file=25)
    writer.write("web", table(100, 100), mode="append")
    writer.write("more", table(1000, 50))
    api = flock.client()
    sources = [{"path": "web", "version": 1, "rows": 100}, {"path": "more", "rows": 20}]
    blend(api, {"sources": sources, "seed": 5, "to": "mix"}, wait_for)
    before = sorted(map(str, rows_of(writer, "mix")))
    ids = writer.open("mix").to_table()["id"].to_pylist()
    assert sorted(i for i in ids if i < 1000) == list(range(100))  # version 1's rows only
    origin = get(api, f"{DATASETS}/info", path="mix").json()["origin"]
    assert [(s["path"], s["version"]) for s in origin["sources"]] == [("web", 1), ("more", 1)]
    assert list(pins(writer, "web").values()) == [1]

    # The sources change, are compacted, and their old versions cleaned up: the blend still
    # reads the rows it took, from the files its pins kept.
    writer.open("web").delete("id < 50")
    writer.write("web", table(500, 10), mode="append")
    writer.open("more").update({"text": "'changed'"})
    for path in ("web", "more"):
        writer.open(path).optimize.compact_files(target_rows_per_fragment=10_000)
        writer.open(path).cleanup_old_versions(
            older_than=timedelta(0), error_if_tagged_old_versions=False
        )
    assert writer.open("web").count_rows() == 160
    assert sorted(map(str, rows_of(writer, "mix"))) == before

    # The sources can't move or go while the blend reads them; the blend itself can.
    assert api.post(f"{DATASETS}/delete", {"path": "web"}).status == 409
    assert api.post(f"{DATASETS}/rename", {"path": "more", "to": "elsewhere"}).status == 409
    assert api.post(f"{DATASETS}/rename", {"path": "mix", "to": "mixes/mix"}).status == 200
    assert sorted(map(str, rows_of(writer, "mixes/mix"))) == before


def test_blends_of_forks_and_of_blends_read_through_every_layer(
    flock: S3Flock,  # noqa: F811
    writer: Writer,  # noqa: F811
    wait_for: Callable[..., Any],
) -> None:
    writer.write("web", table(0, 100), max_rows_per_file=30)
    writer.write("more", table(1000, 60), max_rows_per_file=25)
    api = flock.client()
    # A fork with edits of its own: some source rows deleted, some new rows of its own.
    assert api.post(f"{DATASETS}/fork", {"path": "web", "to": "fork"}).status == 201
    writer.open("fork").delete("id >= 80")
    writer.write("fork", table(5000, 10), mode="append")
    fork_rows = sorted(map(str, rows_of(writer, "fork")))

    tagged = [
        column("id", "int64", fork={"column": "id"}, more={"column": "id"}),
        column("text", "string", fork={"column": "text"}, more={"column": "text"}),
        column("from", "string", False, fork={"value": "fork"}, more={"value": "more"}),
    ]
    sources = [{"path": "fork", "rows": 90}, {"path": "more", "rows": 30}]
    blend(api, {"sources": sources, "columns": tagged, "seed": 2, "to": "inner"}, wait_for)
    inner = rows_of(writer, "inner")
    # Every live row of the fork, read through it from web's files and its own.
    assert (
        sorted(str({"id": r["id"], "text": r["text"]}) for r in inner if r["from"] == "fork")
        == fork_rows
    )

    # A blend of that blend: its deletion files, fill files and sources all carry through.
    writer.write("other", table(9000, 40))
    outer_map = [
        column("id", "int64", inner={"column": "id"}, other={"column": "id"}),
        column("from", "string", True, inner={"column": "from"}, other={"null": True}),
    ]
    sources = [{"path": "inner", "rows": 70}, {"path": "other", "rows": 40}]
    blend(api, {"sources": sources, "columns": outer_map, "seed": 8, "to": "outer"}, wait_for)
    outer = rows_of(writer, "outer")
    by_id = {r["id"]: r["from"] for r in inner}
    from_inner = [r for r in outer if r["from"] is not None]
    assert len(from_inner) == 70 and len({r["id"] for r in from_inner}) == 70
    assert all(by_id[r["id"]] == r["from"] for r in from_inner)
    assert sorted(r["id"] for r in outer if r["from"] is None) == list(range(9000, 9040))

    # Each layer pins the one below it, so nothing under the outer blend can go.
    assert list(pins(writer, "inner").values()) == [2]
    for path in ("inner", "fork", "web", "more"):
        assert api.post(f"{DATASETS}/delete", {"path": path}).status == 409, path
    assert get(api, f"{DATASETS}/info", path="inner").json()["dependents"]["paths"] == ["outer"]


# ---------------------------------------------------------------------- after the blend (D178)


def test_editing_or_forking_a_blend_leaves_its_sources_alone(
    flock: S3Flock,  # noqa: F811
    writer: Writer,  # noqa: F811
    wait_for: Callable[..., Any],
) -> None:
    writer.write("web", web_table(0, 100), max_rows_per_file=40)
    writer.write("code", code_table(0, 100), max_rows_per_file=40)
    sources_before = {p: sorted(map(str, rows_of(writer, p))) for p in ("web", "code")}
    api = flock.client()
    sources = [{"path": "web", "rows": 50}, {"path": "code", "rows": 50}]
    blend(api, {"sources": sources, "columns": MAP, "seed": 4, "to": "mix"}, wait_for)
    blended = rows_of(writer, "mix")

    assert api.post(f"{DATASETS}/fork", {"path": "mix", "to": "try"}).status == 201
    writer.open("try").delete("source = 'web'")
    assert writer.open("try").count_rows() == 50

    ds = writer.open("mix")
    ds.delete("source = 'code' AND id < 50")
    ds = writer.open("mix")
    ds.update({"text": "upper(text)"}, where="source = 'web'")
    writer.write(
        "mix",
        pa.table(
            {"id": pa.array([-1], pa.int64()), "text": ["new"], "source": ["mine"]},
            schema=ds.schema,
        ),
        mode="append",
    )
    edited = rows_of(writer, "mix")
    expected = [
        {**r, "text": r["text"].upper()} if r["source"] == "web" else r
        for r in blended
        if not (r["source"] == "code" and r["id"] < 50)
    ] + [{"id": -1, "text": "new", "source": "mine"}]
    assert sorted(map(str, edited)) == sorted(map(str, expected))

    assert {p: sorted(map(str, rows_of(writer, p))) for p in ("web", "code")} == sources_before
    tagged = writer.open("mix", version="blend").to_table().to_pylist()
    assert sorted(map(str, tagged)) == sorted(map(str, blended))
    assert writer.open("try").count_rows() == 50


def test_blending_again_from_the_origin_and_making_it_independent_keep_its_rows(
    flock: S3Flock,  # noqa: F811
    writer: Writer,  # noqa: F811
    wait_for: Callable[..., Any],
) -> None:
    writer.write("web", web_table(0, 200), max_rows_per_file=50)
    writer.write("code", code_table(0, 120), max_rows_per_file=50)
    api = flock.client()
    sources = [{"path": "web", "fraction": 0.7}, {"path": "code", "fraction": 0.3}]
    body = {"sources": sources, "total": 150, "columns": MAP, "seed": 2**63 - 1, "to": "mix"}
    blend(api, body, wait_for)
    rows = sorted(map(str, rows_of(writer, "mix")))
    assert len(rows) == 150

    # What the Blend again dialog sends, from the origin alone, makes the same rows.
    origin = get(api, f"{DATASETS}/info", path="mix").json()["origin"]
    again = {
        "sources": [
            {"path": s["path"], "version": s["version"], "fraction": s["fraction"]}
            for s in origin["sources"]
        ],
        "total": origin["spec"]["total"],
        "columns": origin["columns"],
        "seed": origin["seed"],
        "replace": origin["replace"],
        "to": "mix-again",
    }
    blend(api, again, wait_for)
    assert sorted(map(str, rows_of(writer, "mix-again"))) == rows

    started = api.post(f"{DATASETS}/independent", {"path": "mix"})
    assert started.status == 202, started.body
    assert wait_job(api, started.json(), wait_for)["state"] == "finished"
    ds = writer.open("mix")
    assert sorted(map(str, ds.to_table().to_pylist())) == rows
    assert all(df.base_id is None for f in ds.get_fragments() for df in f.metadata.files)
    assert [v["version"] for v in ds.versions()] == [ds.version]  # its history is gone
    # mix-again still reads the sources; mix no longer does.
    assert get(api, f"{DATASETS}/info", path="web").json()["dependents"]["paths"] == ["mix-again"]


def test_blend_requests_are_checked_before_anything_is_written(
    flock: S3Flock,  # noqa: F811
    writer: Writer,  # noqa: F811
    wait_for: Callable[..., Any],
) -> None:
    writer.write("a", table(0, 20))
    writer.write("b", table(100, 20))
    writer.write("taken", table(0, 1))
    api = flock.client()
    two = [{"path": "a", "rows": 5}, {"path": "b", "rows": 5}]

    for i, seed in enumerate((0, 2**63 - 1)):
        blend(api, {"sources": two, "seed": seed, "to": f"s{i}"}, wait_for)
    for seed in (-1, 2**63, None):
        refused = api.post(f"{DATASETS}/blend", {"sources": two, "seed": seed, "to": "bad"})
        assert refused.status == 422, seed

    taken = api.post(f"{DATASETS}/blend", {"sources": two, "seed": 1, "to": "taken"})
    assert taken.status == 409 and taken.json()["taken"] is True
    assert writer.open("taken").count_rows() == 1
    assert preview(api, {"sources": two, "to": "taken"})["problems"][-1].startswith(
        "Something already exists at taken"
    )

    assert preview(api, {"sources": two[:1]})["problems"] == ["A blend needs at least two sources."]
    assert api.post(f"{DATASETS}/blend/preview", {"sources": [two[0], two[0]]}).status == 422
    zero = [{"path": "a", "rows": 0}, {"path": "b", "rows": 5}]
    assert preview(api, {"sources": zero})["problems"] == [
        "Take at least one row from each source."
    ]
    missing = [{"path": "a", "rows": 1}, {"path": "nowhere", "rows": 1}]
    assert api.post(f"{DATASETS}/blend/preview", {"sources": missing}).status == 404
    old = [{"path": "a", "version": 9, "rows": 1}, {"path": "b", "rows": 1}]
    assert api.post(f"{DATASETS}/blend/preview", {"sources": old}).status == 404
    assert keys(flock, upstream_name(flock), "bad/") == []
    assert sorted(pins(writer, "a").values()) == [1, 1]  # the two seeds' blends only
