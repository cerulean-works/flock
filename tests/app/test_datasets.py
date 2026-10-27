"""The Datasets tab (013): the managed flock-datasets bucket (D85), finding datasets (D87), the
dataset page's reads (D88-D92), the trash (D93), renames (D95) and tags (D96). Datasets are
written by Lance through the S3 proxy, as users write them."""

import asyncio
import base64
import hashlib
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlencode

import asyncpg
import pytest

from app.api.src.core.datasets import Datasets
from tests.app.conftest import Client, Flocks, S3Flock
from tests.conftest import S3Server

lance = pytest.importorskip("lance")
pa = pytest.importorskip("pyarrow")

BUCKETS = "/api/v1/orgs/flock/buckets"
DATASETS = "/api/v1/orgs/flock/datasets"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
JPEG = b"\xff\xd8\xff\xe0" + b"\x01" * 40


# ---------------------------------------------------------------------- helpers


def sql(flock: S3Flock, query: str, *args: Any) -> list[asyncpg.Record]:
    async def run() -> list[asyncpg.Record]:
        conn = await asyncpg.connect(flock.database_url)
        try:
            return await conn.fetch(query, *args)
        finally:
            await conn.close()

    return asyncio.run(run())


def options(flock: S3Flock, client: Client | None = None, org: str = "flock") -> dict[str, str]:
    """Lance's storage options for writing through the proxy with a user's ephemeral key."""
    creds = flock.credentials(client, org=org)
    return {
        "aws_endpoint": flock.proxy.url,
        "aws_access_key_id": creds["access_key_id"],
        "aws_secret_access_key": creds["secret_access_key"],
        "aws_region": "us-east-1",
        "allow_http": "true",
    }


def table(start: int, count: int) -> Any:
    ids = list(range(start, start + count))
    return pa.table({"id": pa.array(ids, pa.int64()), "text": [f"row {i}" for i in ids]})


class Writer:
    """Writes Lance datasets into the flock's flock-datasets, through the proxy."""

    def __init__(self, flock: S3Flock, org: str = "flock") -> None:
        self.flock = flock
        self.options = options(flock, org=org)

    def uri(self, path: str) -> str:
        return f"s3://flock-datasets/{path}"

    def write(self, path: str, data: Any, mode: str = "create", **kwargs: Any) -> Any:
        return lance.write_dataset(
            data, self.uri(path), mode=mode, storage_options=self.options, **kwargs
        )

    def open(self, path: str, version: int | str | None = None) -> Any:
        return lance.dataset(self.uri(path), version=version, storage_options=self.options)


def upstream_name(flock: S3Flock, org: str = "flock") -> str:
    return f"{flock.bucket_prefix}{flock.store.orgs[org].pk:05d}-flock-datasets"


def keys(flock: S3Flock, bucket: str, prefix: str = "") -> list[str]:
    pages = (
        flock.s3.client().get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)
    )
    return sorted(o["Key"] for page in pages for o in page.get("Contents", []))


def put(flock: S3Flock, key: str, body: bytes, org: str = "flock") -> None:
    flock.s3.client().put_object(Bucket=upstream_name(flock, org), Key=key, Body=body)


def datasets(flock: S3Flock) -> Datasets:
    return flock.store.datasets


def tune(monkeypatch: pytest.MonkeyPatch, flock: S3Flock, **values: Any) -> None:
    """Lower one of ``Datasets``' limits for a test (on the running flock)."""
    for name, value in values.items():
        monkeypatch.setattr(datasets(flock), name, value)


def get(client: Client, url: str, **params: Any) -> Any:
    """A GET with query ``params`` (which may be named ``path``, unlike ``Client.get``'s)."""
    return client.request("GET", url, params=params or None)


def delete(client: Client, url: str, **params: Any) -> Any:
    return client.request("DELETE", url, params=params or None)


def query(url: str, **params: Any) -> str:
    return f"{url}?{urlencode(params, doseq=True)}"


def wrap(
    monkeypatch: pytest.MonkeyPatch, flock: S3Flock, method: str, before: Callable[..., Any]
) -> list[tuple]:
    """Spy on one of the upstream client's methods: ``before`` sees each call's arguments."""
    upstream = flock.store.s3.upstream
    assert upstream is not None
    original = getattr(upstream, method)
    calls: list[tuple] = []

    async def spy(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        result = before(*args)
        if asyncio.iscoroutine(result):
            await result
        return await original(*args, **kwargs)

    monkeypatch.setattr(upstream, method, spy)
    return calls


@pytest.fixture
def flock(make_flock: Flocks, s3_server: S3Server) -> S3Flock:
    return make_flock.s3(s3_server)


@pytest.fixture
def writer(flock: S3Flock) -> Writer:
    return Writer(flock)


# ---------------------------------------------------------------------- the managed bucket (D85)


def test_managed_bucket_is_created_with_the_org(flock: S3Flock):
    api = flock.client()
    (bucket,) = api.get(BUCKETS).json()["buckets"]
    assert bucket["name"] == "flock-datasets"
    assert bucket["managed"] == "datasets"
    assert bucket["created_by"] is None
    (event,) = api.get(f"{BUCKETS}/flock-datasets").json()["events"]
    assert event["kind"] == "created" and event["user"] is None
    assert event["detail"] == {"via": "flock"}
    assert flock.s3.client().head_bucket(Bucket=upstream_name(flock))

    # A new org gets its own.
    assert api.post("/api/v1/orgs", {"name": "lab"}).status == 201
    listed = api.get("/api/v1/orgs/lab/buckets").json()["buckets"]
    assert [(b["name"], b["managed"]) for b in listed] == [("flock-datasets", "datasets")]
    assert flock.s3.client().head_bucket(Bucket=upstream_name(flock, "lab"))


def test_startup_creates_missing_buckets_and_adopts_existing_ones(
    make_flock: Flocks, s3_server: S3Server, wait_for
):
    # Two orgs on a server without S3: one already had a bucket named flock-datasets (from
    # before names were reserved), the other none.
    plain = make_flock()
    api = plain.client()
    assert api.post("/api/v1/orgs", {"name": "lab"}).status == 201
    prefix = s3_server.bucket_prefix()
    pk = plain.store.orgs["flock"].pk
    old = f"{prefix}{pk:05d}-flock-datasets"
    s3_server.client().create_bucket(Bucket=old)
    s3_server.client().put_object(Bucket=old, Key="mine.txt", Body=b"kept")

    async def insert(conn: asyncpg.Connection) -> int:
        return await conn.fetchval(
            "INSERT INTO s3_buckets (org_pk, name, upstream_name, created_at) "
            "VALUES ($1, 'flock-datasets', $2, now()) RETURNING pk",
            pk,
            old,
        )

    async def run() -> int:
        conn = await asyncpg.connect(plain.database_url)
        try:
            return await insert(conn)
        finally:
            await conn.close()

    bucket_pk = asyncio.run(run())
    plain.stop()
    make_flock.servers.remove(plain)

    flock = S3Flock(
        plain.database_url,
        plain.files_dir,
        s3_server,
        bucket_prefix=prefix,
        api_key=plain.api_key,
        token=plain.token,
    )
    make_flock.servers.append(flock)
    api = flock.client()

    def managed(org: str) -> list:
        return [
            (b["name"], b["managed"])
            for b in api.get(f"/api/v1/orgs/{org}/buckets").json()["buckets"]
        ]

    wait_for(lambda: managed("lab") == [("flock-datasets", "datasets")])
    assert managed("flock") == [("flock-datasets", "datasets")]
    # Adopted, not replaced: the same row and the same objects.
    rows = sql(flock, "SELECT pk FROM s3_buckets WHERE managed = 'datasets' AND org_pk = $1", pk)
    assert [r["pk"] for r in rows] == [bucket_pk]
    assert keys(flock, old) == ["mine.txt"]
    # The tab finds its files.
    files = get(api, f"{DATASETS}/at").json()["files"]
    assert [f["name"] for f in files] == ["mine.txt"]


def test_the_datasets_tab_creates_a_missing_bucket(flock: S3Flock):
    """When creating it failed earlier, opening the tab tries again."""
    # As if the org's creation had failed to make it: no row, no bucket upstream.
    flock.s3.client().delete_bucket(Bucket=upstream_name(flock))
    sql(flock, "DELETE FROM s3_buckets WHERE managed = 'datasets'")
    assert flock.client().get(BUCKETS).json()["buckets"] == []
    listing = get(flock.client(), f"{DATASETS}/at")
    assert listing.status == 200, listing.body
    assert listing.json()["datasets"] == []
    assert [b["name"] for b in flock.client().get(BUCKETS).json()["buckets"]] == ["flock-datasets"]


def test_managed_bucket_cant_be_deleted_and_flock_names_are_reserved(flock: S3Flock):
    api = flock.client()
    refused = api.delete(f"{BUCKETS}/flock-datasets")
    assert refused.status == 409
    assert refused.json()["error"] == "flock-datasets is managed by Flock and can't be deleted"
    assert [b["name"] for b in api.get(BUCKETS).json()["buckets"]] == ["flock-datasets"]

    named = api.post(BUCKETS, {"name": "flock-models"})
    assert named.status == 422
    assert named.json()["error"] == "Names starting with flock- are reserved."
    assert api.post(BUCKETS, {"name": "flocked"}).status == 201


# ---------------------------------------------------------------------- folders (D87)


def test_listing_finds_datasets_at_any_depth(
    flock: S3Flock, writer: Writer, monkeypatch: pytest.MonkeyPatch
):
    writer.write("top", table(0, 5))
    writer.write("eval/mmlu", table(0, 5))
    writer.write("eval/mmlu", table(5, 5), mode="append")
    writer.write("eval/deep/x/y", table(0, 3))
    writer.write("eval/old", table(0, 3), enable_v2_manifest_paths=False)
    put(flock, "notes.txt", b"hello")
    put(flock, "eval/README.md", b"# Eval")
    put(flock, "eval/empty/", b"")
    api = flock.client()

    lists = wrap(monkeypatch, flock, "list_objects", lambda *a: None)
    root = get(api, f"{DATASETS}/at").json()
    # One LIST for the folder, and one per subfolder.
    assert len(lists) == 1 + 2
    assert root["kind"] == "folder" and root["path"] == ""
    assert [(d["name"], d["path"], d["version"]) for d in root["datasets"]] == [("top", "top", 1)]
    assert root["datasets"][0]["updated_at"] > 0
    assert [f["path"] for f in root["folders"]] == ["eval"]
    assert [(f["name"], f["size"]) for f in root["files"]] == [("notes.txt", 5)]
    assert root["cursor"] is None

    inside = get(api, f"{DATASETS}/at", path="eval").json()
    assert [(d["path"], d["version"]) for d in inside["datasets"]] == [
        ("eval/mmlu", 2),
        ("eval/old", None),  # the older manifest naming: a dataset, its version unknown
    ]
    assert [f["path"] for f in inside["folders"]] == ["eval/deep", "eval/empty"]
    assert [f["key"] for f in inside["files"]] == ["eval/README.md"]

    deep = get(api, f"{DATASETS}/at", path="eval/deep/x").json()
    assert [d["path"] for d in deep["datasets"]] == ["eval/deep/x/y"]

    dataset = get(api, f"{DATASETS}/at", path="eval/mmlu").json()
    assert dataset == {
        "kind": "dataset",
        "path": "eval/mmlu",
        "version": 2,
        "updated_at": dataset["updated_at"],
    }
    # A dataset's internals aren't browsed here.
    assert get(api, f"{DATASETS}/at", path="eval/mmlu/data").status == 404
    assert get(api, f"{DATASETS}/at", path="eval/mmlu/_versions").status == 404
    empty = get(api, f"{DATASETS}/at", path="eval/empty").json()
    assert (empty["datasets"], empty["folders"], empty["files"]) == ([], [], [])
    assert get(api, f"{DATASETS}/at", path="nowhere").status == 404
    assert get(api, f"{DATASETS}/at", path="/eval").status == 422


def test_listing_pages(flock: S3Flock, writer: Writer, monkeypatch: pytest.MonkeyPatch):
    tune(monkeypatch, flock, PAGE=3)
    writer.write("d1", table(0, 1))
    writer.write("d2", table(0, 1))
    for name in ("a.txt", "b.txt", "c.txt", "f/x.txt"):
        put(flock, name, b"x")
    api = flock.client()
    seen: list[str] = []
    cursor = None
    for _ in range(5):
        page = get(api, f"{DATASETS}/at", **({"cursor": cursor} if cursor else {})).json()
        seen += [d["path"] for d in page["datasets"]]
        seen += [f["path"] for f in page["folders"]]
        seen += [f["key"] for f in page["files"]]
        cursor = page["cursor"]
        if cursor is None:
            break
    assert sorted(seen) == ["a.txt", "b.txt", "c.txt", "d1", "d2", "f"]


# ---------------------------------------------------------------------- the dataset page (D88-D92)


def test_info_rows_and_versions_at_any_version(flock: S3Flock, writer: Writer):
    path = "eval/mmlu"
    writer.write(path, table(0, 10))
    writer.write(path, table(10, 10), mode="append", transaction_properties={"message": "more"})
    ds = writer.open(path)
    ds.delete("id < 5")
    api = flock.client()
    tagged = api.request("PUT", f"{DATASETS}/tags/v1.0", body={"path": path, "version": 1})
    assert tagged.status == 200, tagged.body

    info = get(api, f"{DATASETS}/info", path=path).json()
    assert info["version"] == 3 and info["latest_version"] == 3
    assert info["rows"] == 15 and info["columns"] == 2
    assert info["fragments"] == 2 and info["data_size"] > 0
    assert info["timestamp"] > 0
    assert [f["name"] for f in info["schema"]] == ["id", "text"]
    assert info["schema"][0] == {
        "name": "id",
        "type": "int64",
        "nullable": True,
        "metadata": {},
        "children": [],
    }
    assert info["details"]["data_storage_version"]
    assert info["details"]["stable_row_ids"] is False
    assert info["tags"] == [{"name": "v1.0", "version": 1}]
    assert info["binary"] is False

    older = get(api, f"{DATASETS}/info", path=path, version="1").json()
    assert (older["version"], older["rows"], older["latest_version"]) == (1, 10, 3)
    by_tag = get(api, f"{DATASETS}/info", path=path, version="v1.0").json()
    assert (by_tag["version"], by_tag["rows"]) == (1, 10)
    assert get(api, f"{DATASETS}/info", path=path, version="9").status == 404
    assert get(api, f"{DATASETS}/info", path=path, version="nope").status == 404
    assert get(api, f"{DATASETS}/info", path="eval").status == 404

    rows = get(api, f"{DATASETS}/rows", path=path, version="3", offset=2, limit=3).json()
    assert rows["total"] == 15 and rows["limit"] == 3 and rows["version"] == 3
    assert [c["name"] for c in rows["columns"]] == ["id", "text"]
    assert [r[0]["v"] for r in rows["rows"]] == [7, 8, 9]
    assert rows["rows"][0][1] == {"k": "str", "v": "row 7"}
    first = get(api, f"{DATASETS}/rows", path=path, version="1", limit=2).json()
    assert [r[0]["v"] for r in first["rows"]] == [0, 1]
    assert get(api, f"{DATASETS}/rows", path=path).json()["limit"] == 50
    one = api.get(query(f"{DATASETS}/rows", path=path, columns=["text"], limit=1)).json()
    assert [c["name"] for c in one["columns"]] == ["text"] and len(one["rows"][0]) == 1
    assert api.get(query(f"{DATASETS}/rows", path=path, columns=["nope"])).status == 422
    beyond = get(api, f"{DATASETS}/rows", path=path, offset=100).json()
    assert beyond["rows"] == [] and beyond["total"] == 15

    history = get(api, f"{DATASETS}/versions", path=path).json()
    assert history["latest"] == 3 and history["cursor"] is None
    assert [
        (v["version"], v["operation"], v["rows"], v["rows_delta"], v["tags"])
        for v in history["versions"]
    ] == [
        (3, "Delete", 15, -5, []),
        (2, "Append", 20, 10, []),
        (1, "Overwrite", 10, None, ["v1.0"]),
    ]
    assert history["versions"][1]["properties"] == {"message": "more"}
    assert all(v["timestamp"] > 0 for v in history["versions"])

    # A page keeps the version it resolved: a write after that doesn't show in its requests.
    writer.write(path, table(100, 5), mode="append")
    pinned = get(api, f"{DATASETS}/rows", path=path, version="3").json()
    assert pinned["total"] == 15
    latest = get(api, f"{DATASETS}/info", path=path, version="3").json()
    assert (latest["version"], latest["latest_version"]) == (3, 4)


def test_versions_page(flock: S3Flock, writer: Writer, monkeypatch: pytest.MonkeyPatch):
    tune(monkeypatch, flock, VERSION_PAGE=2)
    writer.write("d", table(0, 1))
    for i in range(4):
        writer.write("d", table(i + 1, 1), mode="append")
    api = flock.client()
    first = get(api, f"{DATASETS}/versions", path="d").json()
    assert [v["version"] for v in first["versions"]] == [5, 4]
    assert first["cursor"] == 4
    assert first["versions"][1]["rows_delta"] == 1  # the version below the page is read too
    second = get(api, f"{DATASETS}/versions", path="d", cursor=4).json()
    assert [v["version"] for v in second["versions"]] == [3, 2]
    third = get(api, f"{DATASETS}/versions", path="d", cursor=second["cursor"]).json()
    assert [v["version"] for v in third["versions"]] == [1] and third["cursor"] is None


def cells_table() -> Any:
    image = pa.struct([("bytes", pa.binary()), ("path", pa.string())])
    features = {
        "info": {
            "features": {
                "label": {"names": ["cat", "dog"], "_type": "ClassLabel"},
                "picture": {"_type": "Image"},
            }
        }
    }
    schema = pa.schema(
        [
            pa.field("n", pa.int64()),
            pa.field("big", pa.int64()),
            pa.field("x", pa.float64()),
            pa.field("ok", pa.bool_()),
            pa.field("text", pa.string()),
            pa.field("at", pa.timestamp("us", tz="Asia/Taipei")),
            pa.field("day", pa.date32()),
            pa.field("png", pa.binary()),
            pa.field("raw", pa.large_binary()),
            pa.field("picture", image),
            pa.field("emb", pa.list_(pa.float32(), 16)),
            pa.field("nums", pa.list_(pa.int32())),
            pa.field("meta", pa.struct([("a", pa.int32()), ("b", pa.string())])),
            pa.field("label", pa.int64()),
            pa.field("blob", pa.large_binary(), metadata={"lance-encoding:blob": "true"}),
        ],
        metadata={"huggingface": json.dumps(features)},
    )
    return pa.table(
        {
            "n": [None, 1],
            "big": [2**60, 3],
            "x": [float("nan"), 1.5],
            "ok": [True, False],
            "text": ["é" * 600, "short"],
            "at": pa.array([1_700_000_000_000_000, 0], pa.timestamp("us", tz="Asia/Taipei")),
            "day": pa.array([19000, 0], pa.date32()),
            "png": [PNG, b"\x00\x01"],
            "raw": [JPEG * 100, b"z"],
            "picture": [{"bytes": JPEG, "path": "a.jpg"}, {"bytes": None, "path": "b.jpg"}],
            "emb": [[float(i) for i in range(16)], [0.0] * 16],
            "nums": [[1, 2, 3], []],
            "meta": [{"a": 1, "b": "x"}, None],
            "label": [1, 5],
            "blob": [b"b" * 3000, b""],
        },
        schema=schema,
    )


def test_cells_render_each_type(flock: S3Flock, writer: Writer, monkeypatch: pytest.MonkeyPatch):
    tune(monkeypatch, flock, IMAGE_LIMIT=1000)
    writer.write("cells", cells_table())
    api = flock.client()
    info = get(api, f"{DATASETS}/info", path="cells").json()
    assert info["binary"] is True
    types = {f["name"]: f["type"] for f in info["schema"]}
    assert types["blob"] == "blob"
    assert types["emb"] == "fixed_size_list<item: float>[16]"
    meta = next(f for f in info["schema"] if f["name"] == "meta")
    assert [c["name"] for c in meta["children"]] == ["a", "b"]
    assert "huggingface" in info["metadata"]

    page = get(api, f"{DATASETS}/rows", path="cells").json()
    assert page["limit"] == 10  # binary columns: smaller pages
    names = [c["name"] for c in page["columns"]]
    first, second = ({n: cell for n, cell in zip(names, row, strict=True)} for row in page["rows"])
    assert first["n"] == {"k": "null"} and second["n"] == {"k": "num", "v": 1}
    assert first["big"] == {"k": "num", "v": str(2**60)}  # beyond what JSON numbers hold
    assert first["x"] == {"k": "num", "v": "nan"} and second["x"] == {"k": "num", "v": 1.5}
    assert first["ok"] == {"k": "bool", "v": True}
    assert first["text"] == {"k": "str", "v": "é" * 500, "len": 600}
    assert second["text"] == {"k": "str", "v": "short"}
    assert first["at"] == {"k": "time", "v": "2023-11-15T06:13:20+08:00"}
    assert first["day"] == {"k": "time", "v": "2022-01-08"}
    assert first["png"] == {
        "k": "image",
        "mime": "image/png",
        "size": len(PNG),
        "data": base64.b64encode(PNG).decode(),
    }
    assert second["png"] == {"k": "bin", "size": 2}
    assert first["raw"] == {"k": "bin", "size": len(JPEG) * 100}  # an image, but too large
    assert first["picture"]["k"] == "image" and first["picture"]["mime"] == "image/jpeg"
    assert second["picture"] == {"k": "str", "v": "b.jpg"}
    assert first["emb"] == {
        "k": "vec",
        "dtype": "float",
        "len": 16,
        "head": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0],
    }
    assert first["nums"] == {"k": "json", "v": "[1,2,3]"}
    assert first["meta"] == {"k": "json", "v": '{"a":1,"b":"x"}'}
    assert second["meta"] == {"k": "null"}
    assert first["label"] == {"k": "num", "v": 1, "label": "dog"}
    assert second["label"] == {"k": "num", "v": 5}  # out of the names' range
    assert first["blob"] == {"k": "blob", "size": 3000}
    assert {c["name"]: c.get("feature") for c in page["columns"]}["label"] == "ClassLabel"

    full = get(api, f"{DATASETS}/cell", path="cells", row=0, column="text").json()
    assert full["cell"] == {"k": "str", "v": "é" * 600}
    vector = get(api, f"{DATASETS}/cell", path="cells", row=0, column="emb").json()
    assert json.loads(vector["cell"]["v"]) == [float(i) for i in range(16)]
    assert get(api, f"{DATASETS}/cell", path="cells", row=9, column="text").status == 404
    assert get(api, f"{DATASETS}/cell", path="cells", row=0, column="nope").status == 404


def test_a_page_sends_at_most_its_byte_budget(
    flock: S3Flock, writer: Writer, monkeypatch: pytest.MonkeyPatch
):
    tune(monkeypatch, flock, PAGE_BYTES=len(PNG) * 2)
    writer.write("pics", pa.table({"img": pa.array([PNG] * 4, pa.binary())}))
    page = get(flock.client(), f"{DATASETS}/rows", path="pics").json()
    assert [row[0]["k"] for row in page["rows"]] == ["image", "image", "bin", "bin"]
    assert page["rows"][3][0] == {"k": "bin", "size": len(PNG)}


def test_readme(flock: S3Flock, writer: Writer):
    writer.write("with", table(0, 1))
    writer.write("without", table(0, 1))
    put(flock, "with/README.md", "# MMLU\n\nQuestions. ✓".encode())
    api = flock.client()
    got = get(api, f"{DATASETS}/readme", path="with").json()
    assert got == {"text": "# MMLU\n\nQuestions. ✓", "truncated": False}
    assert get(api, f"{DATASETS}/readme", path="without").status == 404
    assert get(api, f"{DATASETS}/readme", path="nowhere").status == 404


def test_lance_errors_are_502(flock: S3Flock, writer: Writer):
    writer.write("broken", table(0, 3))
    data = [k for k in keys(flock, upstream_name(flock), "broken/data/")]
    flock.s3.client().delete_object(Bucket=upstream_name(flock), Key=data[0])
    got = get(flock.client(), f"{DATASETS}/rows", path="broken")
    assert got.status == 502
    assert "Lance" in got.json()["error"]


# ---------------------------------------------------------------------- the trash (D93)


def test_delete_moves_to_the_trash_and_restores(
    flock: S3Flock, writer: Writer, monkeypatch: pytest.MonkeyPatch
):
    path = "eval/mmlu"
    writer.write(path, table(0, 10))
    writer.write(path, table(10, 10), mode="append")
    put(flock, f"{path}/README.md", b"# card")
    bucket = upstream_name(flock)
    before = keys(flock, bucket, f"{path}/")
    copies = wrap(monkeypatch, flock, "copy_object", lambda *a: None)
    deletes = wrap(monkeypatch, flock, "delete_keys", lambda *a: None)
    api = flock.client()

    deleted = api.post(f"{DATASETS}/delete", {"path": path})
    assert deleted.status == 200, deleted.body
    entry = deleted.json()
    assert entry["path"] == path and entry["latest_version"] == 2
    assert entry["objects"] == len(before) and entry["bytes"] > 0
    assert entry["deleted_by"]["email"] == flock.ADMIN_EMAIL
    assert entry["expires_at"] == pytest.approx(entry["deleted_at"] + 14 * 86400)
    # _versions/ copied last, and deleted first.
    copied = [args[1] for args in copies]
    first_version = next(i for i, k in enumerate(copied) if "/_versions/" in k)
    assert all("/_versions/" in k for k in copied[first_version:])
    assert all("/_versions/" in k for k in deletes[0][1])
    assert keys(flock, bucket, f"{path}/") == []
    assert get(api, f"{DATASETS}/at", path=path).status == 404

    trash = datasets(flock).trash
    prefix = f"{flock.store.orgs['flock'].pk:05d}/{entry['id']}/"
    assert keys(flock, trash, prefix) == [prefix + k.removeprefix(f"{path}/") for k in before]
    (listed,) = get(api, f"{DATASETS}/trash").json()["entries"]
    assert listed["id"] == entry["id"] and listed["moved_at"] is not None

    restored = api.post(f"{DATASETS}/trash/{entry['id']}/restore", {})
    assert restored.status == 200, restored.body
    assert restored.json() == {"path": path}
    assert keys(flock, bucket, f"{path}/") == before
    assert keys(flock, trash, prefix) == []
    assert get(api, f"{DATASETS}/trash").json()["entries"] == []
    assert get(api, f"{DATASETS}/info", path=path).json()["rows"] == 20
    assert writer.open(path, 1).count_rows() == 10


def test_the_trash_is_out_of_every_key_s_reach(flock: S3Flock, writer: Writer):
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError

    writer.write("d", table(0, 1))
    assert flock.client().post(f"{DATASETS}/delete", {"path": "d"}).status == 200
    creds = flock.credentials()
    s3 = boto3.client(
        "s3",
        endpoint_url=flock.proxy.url,
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 1}),
    )
    names = [b["Name"] for b in s3.list_buckets()["Buckets"]]
    assert names == ["flock-datasets"]
    trash = datasets(flock).trash
    for name in (trash, "flock-trash"):
        with pytest.raises(ClientError) as raised:
            s3.list_objects_v2(Bucket=name)
        assert raised.value.response["Error"]["Code"] == "NoSuchBucket"


def test_restore_to_another_path_when_the_old_one_is_taken(flock: S3Flock, writer: Writer):
    writer.write("d", table(0, 3))
    api = flock.client()
    pk = api.post(f"{DATASETS}/delete", {"path": "d"}).json()["id"]
    writer.write("d", table(0, 1))  # a new dataset at the old path
    taken = api.post(f"{DATASETS}/trash/{pk}/restore", {})
    assert taken.status == 409
    assert taken.json() == {
        "error": "Something already exists at d.",
        "errors": [{"message": "Something already exists at d."}],
        "taken": True,
    }
    assert api.post(f"{DATASETS}/trash/{pk}/restore", {"to": "bad path"}).status == 422
    moved = api.post(f"{DATASETS}/trash/{pk}/restore", {"to": "restored/d"})
    assert moved.status == 200, moved.body
    assert get(api, f"{DATASETS}/info", path="restored/d").json()["rows"] == 3
    assert get(api, f"{DATASETS}/info", path="d").json()["rows"] == 1


def test_delete_for_good_and_who_may(flock: S3Flock, writer: Writer):
    alice = flock.add_user("alice@flock.test", "member")
    api = flock.client()
    assert api.post("/api/v1/orgs", {"name": "lab"}).status == 201
    olga = flock.add_user("olga@flock.test", "member", org="lab")
    writer.write("d", table(0, 3))

    # Members delete datasets, but only admins see the trash.
    deleted = alice.api.post(f"{DATASETS}/delete", {"path": "d"})
    assert deleted.status == 200, deleted.body
    pk = deleted.json()["id"]
    assert get(alice.api, f"{DATASETS}/trash").status == 403
    assert alice.api.post(f"{DATASETS}/trash/{pk}/restore", {}).status == 403
    assert alice.api.delete(f"{DATASETS}/trash/{pk}").status == 403

    # Another org's admin sees nothing of it.
    assert api.get("/api/v1/orgs/lab/datasets/trash").json()["entries"] == []
    assert api.post(f"/api/v1/orgs/lab/datasets/trash/{pk}/restore", {}).status == 404

    assert api.delete(f"{DATASETS}/trash/{pk}").status == 204
    prefix = f"{flock.store.orgs['flock'].pk:05d}/{pk}/"
    assert keys(flock, datasets(flock).trash, prefix) == []
    assert get(api, f"{DATASETS}/trash").json()["entries"] == []
    assert api.delete(f"{DATASETS}/trash/{pk}").status == 404

    # Someone outside the org gets 404 everywhere.
    writer.write("e", table(0, 1))
    for method, path, body in [
        ("GET", f"{DATASETS}/at", None),
        ("GET", query(f"{DATASETS}/info", path="e"), None),
        ("GET", query(f"{DATASETS}/rows", path="e"), None),
        ("GET", query(f"{DATASETS}/cell", path="e", row=0, column="id"), None),
        ("GET", query(f"{DATASETS}/versions", path="e"), None),
        ("GET", query(f"{DATASETS}/readme", path="e"), None),
        ("POST", f"{DATASETS}/delete", {"path": "e"}),
        ("GET", f"{DATASETS}/trash", None),
        ("POST", f"{DATASETS}/trash/{pk}/restore", {}),
        ("DELETE", f"{DATASETS}/trash/{pk}", None),
        ("POST", f"{DATASETS}/rename", {"path": "e", "to": "f"}),
        ("PUT", f"{DATASETS}/tags/t", {"path": "e", "version": 1}),
        ("DELETE", query(f"{DATASETS}/tags/t", path="e"), None),
    ]:
        assert olga.api.request(method, path, body=body).status == 404, (method, path)
    assert get(api, f"{DATASETS}/info", path="e").status == 200


def test_delete_limits(flock: S3Flock, writer: Writer, monkeypatch: pytest.MonkeyPatch):
    writer.write("d", table(0, 3))
    writer.write("d", table(3, 3), mode="append")
    before = keys(flock, upstream_name(flock), "d/")
    tune(monkeypatch, flock, MOVE_OBJECTS=2)
    refused = flock.client().post(f"{DATASETS}/delete", {"path": "d"})
    assert refused.status == 409
    command = "aws s3 rm --recursive s3://flock-datasets/d/ --profile flock"
    assert refused.json()["command"] == command
    assert command in refused.json()["error"]
    tune(monkeypatch, flock, MOVE_OBJECTS=10_000, MOVE_BYTES=10)
    assert flock.client().post(f"{DATASETS}/delete", {"path": "d"}).status == 409
    assert keys(flock, upstream_name(flock), "d/") == before
    assert get(flock.client(), f"{DATASETS}/trash").json()["entries"] == []


def test_sweep_removes_entries_after_14_days(flock: S3Flock, writer: Writer):
    writer.write("old", table(0, 1))
    writer.write("new", table(0, 1))
    api = flock.client()
    old = api.post(f"{DATASETS}/delete", {"path": "old"}).json()["id"]
    new = api.post(f"{DATASETS}/delete", {"path": "new"}).json()["id"]
    sql(
        flock, "UPDATE dataset_trash SET deleted_at = now() - interval '15 days' WHERE pk = $1", old
    )
    done = flock.call(datasets(flock).sweep())
    assert done["expired"] == 1
    org = flock.store.orgs["flock"].pk
    trash = datasets(flock).trash
    assert keys(flock, trash, f"{org:05d}/{old}/") == []
    assert keys(flock, trash, f"{org:05d}/{new}/") != []
    assert [e["id"] for e in get(api, f"{DATASETS}/trash").json()["entries"]] == [new]


def test_sweep_settles_stuck_moves_and_orphans(
    flock: S3Flock, writer: Writer, monkeypatch: pytest.MonkeyPatch
):
    org = flock.store.orgs["flock"].pk
    bucket = upstream_name(flock)
    trash = datasets(flock).trash
    flock.call(datasets(flock)._ensure_trash())
    client = flock.s3.client()

    def stuck(path: str) -> int:
        """A move whose request died after copying: the row and the trash copy."""
        (row,) = sql(
            flock,
            "INSERT INTO dataset_trash (org_pk, path, deleted_at, objects, bytes) "
            "VALUES ($1, $2, now(), 0, 0) RETURNING pk",
            org,
            path,
        )
        for key in keys(flock, bucket, f"{path}/"):
            client.copy_object(
                Bucket=trash,
                Key=f"{org:05d}/{row['pk']}/{key.removeprefix(path + '/')}",
                CopySource={"Bucket": bucket, "Key": key},
            )
        return row["pk"]

    # Still at its path: the move is undone.
    writer.write("kept", table(0, 3))
    kept = stuck("kept")
    # Its _versions/ already deleted: the move is finished.
    writer.write("gone", table(0, 3))
    gone = stuck("gone")
    for key in keys(flock, bucket, "gone/_versions/"):
        client.delete_object(Bucket=bucket, Key=key)
    # A trash prefix with no row at all.
    client.put_object(Bucket=trash, Key=f"{org:05d}/99999/x", Body=b"x")
    # As if an hour had passed (and a day, for the orphan).
    tune(monkeypatch, flock, STUCK_AFTER=0, ORPHAN_AFTER=0)

    done = flock.call(datasets(flock).sweep())
    assert done == {"expired": 0, "settled": 2, "orphans": 1}
    assert keys(flock, trash, f"{org:05d}/{kept}/") == []
    assert get(flock.client(), f"{DATASETS}/info", path="kept").json()["rows"] == 3
    assert keys(flock, bucket, "gone/") == []
    assert keys(flock, trash, f"{org:05d}/{gone}/") != []
    assert keys(flock, trash, f"{org:05d}/99999/") == []
    entries = get(flock.client(), f"{DATASETS}/trash").json()["entries"]
    assert [(e["id"], e["moved_at"] is not None) for e in entries] == [(gone, True)]


def test_deleting_an_org_empties_its_trash(flock: S3Flock, wait_for):
    api = flock.client()
    assert api.post("/api/v1/orgs", {"name": "lab"}).status == 201
    Writer(flock, "lab").write("d", table(0, 1))
    pk = api.post("/api/v1/orgs/lab/datasets/delete", {"path": "d"}).json()["id"]
    org = flock.store.orgs["lab"].pk
    trash = datasets(flock).trash
    assert keys(flock, trash, f"{org:05d}/") != []
    assert api.delete("/api/v1/orgs/lab").status == 204
    wait_for(lambda: keys(flock, trash, f"{org:05d}/") == [])
    assert sql(flock, "SELECT pk FROM dataset_trash WHERE pk = $1", pk) == []


# ---------------------------------------------------------------------- renaming (D95)


def test_rename_keeps_everything(flock: S3Flock, writer: Writer):
    writer.write("scratch/mmlu", table(0, 10))
    writer.write("scratch/mmlu", table(10, 10), mode="append")
    ds = writer.open("scratch/mmlu")
    ds.delete("id = 3")
    ds = writer.open("scratch/mmlu")
    ds.create_scalar_index("id", "BTREE")
    ds.tags.create("v1", 1)
    put(flock, "scratch/mmlu/README.md", b"# MMLU")
    api = flock.client()
    before = get(api, f"{DATASETS}/info", path="scratch/mmlu").json()

    renamed = api.post(f"{DATASETS}/rename", {"path": "scratch/mmlu", "to": "eval/new/mmlu"})
    assert renamed.status == 200, renamed.body
    assert renamed.json() == {"path": "eval/new/mmlu"}
    assert keys(flock, upstream_name(flock), "scratch/") == []
    assert get(api, f"{DATASETS}/at", path="scratch/mmlu").status == 404

    after = get(api, f"{DATASETS}/info", path="eval/new/mmlu").json()
    # The time of each version is Lance's, not the copy's.
    names = ("version", "timestamp", "rows", "columns", "fragments", "data_size", "tags", "schema")
    for name in names:
        assert after[name] == before[name], name
    assert after["rows"] == 19
    assert [i["columns"] for i in after["details"]["indices"]] == [["id"]]
    versions = get(api, f"{DATASETS}/versions", path="eval/new/mmlu").json()["versions"]
    assert [v["version"] for v in versions] == [4, 3, 2, 1]
    assert get(api, f"{DATASETS}/info", path="eval/new/mmlu", version="v1").json()["rows"] == 10
    assert get(api, f"{DATASETS}/readme", path="eval/new/mmlu").json()["text"] == "# MMLU"
    moved = writer.open("eval/new/mmlu")
    assert moved.to_table(filter="id = 15").num_rows == 1


def test_rename_refusals(flock: S3Flock, writer: Writer, monkeypatch: pytest.MonkeyPatch):
    writer.write("a", table(0, 3))
    writer.write("b", table(0, 3))
    put(flock, "files/x.txt", b"x")
    api = flock.client()

    def rename(to: str) -> Any:
        return api.post(f"{DATASETS}/rename", {"path": "a", "to": to})

    taken = rename("b")
    assert taken.status == 409 and taken.json()["error"] == "Something already exists at b."
    assert rename("files").status == 409
    assert rename("a/inner").status == 422
    assert rename("a").status == 422
    assert rename("b/inner").status == 422  # inside another dataset
    for bad in ("x//y", "/x", "x/", "spa ce", "é", "x/../y", "x/_versions", "x" * 513):
        assert rename(bad).status == 422, bad
    assert api.post(f"{DATASETS}/rename", {"path": "nope", "to": "c"}).status == 404
    tune(monkeypatch, flock, MOVE_OBJECTS=2)
    limited = rename("c")
    assert limited.status == 409
    assert limited.json()["command"] == (
        "aws s3 mv --recursive s3://flock-datasets/a/ s3://flock-datasets/c/ --profile flock"
    )
    assert keys(flock, upstream_name(flock), "c/") == []
    assert get(api, f"{DATASETS}/info", path="a").status == 200


def test_rename_refused_when_written_during_the_copy(
    flock: S3Flock, writer: Writer, monkeypatch: pytest.MonkeyPatch
):
    writer.write("a", table(0, 3))
    wrote = []

    async def append(*args: Any) -> None:
        if not wrote:
            wrote.append(True)
            await asyncio.to_thread(writer.write, "a", table(3, 3), "append")

    wrap(monkeypatch, flock, "copy_object", append)
    refused = flock.client().post(f"{DATASETS}/rename", {"path": "a", "to": "b"})
    assert refused.status == 409
    assert refused.json()["error"] == "The dataset changed while it was being renamed. Try again."
    assert keys(flock, upstream_name(flock), "b/") == []
    assert get(flock.client(), f"{DATASETS}/info", path="a").json()["rows"] == 6


def test_rename_copies_large_objects_in_parts(
    flock: S3Flock, writer: Writer, monkeypatch: pytest.MonkeyPatch
):
    writer.write("a", table(0, 3))
    big = bytes(range(256)) * (12 * 4096)  # 12 MiB
    put(flock, "a/extra/big.bin", big)
    tune(monkeypatch, flock, COPY_LIMIT=8 * 1024 * 1024, COPY_PART=5 * 1024 * 1024)
    parts = wrap(monkeypatch, flock, "copy_large", lambda *a: None)
    renamed = flock.client().post(f"{DATASETS}/rename", {"path": "a", "to": "b"})
    assert renamed.status == 200, renamed.body
    assert [args[1] for args in parts] == ["a/extra/big.bin"]
    got = flock.s3.client().get_object(Bucket=upstream_name(flock), Key="b/extra/big.bin")
    assert hashlib.sha256(got["Body"].read()).digest() == hashlib.sha256(big).digest()
    assert get(flock.client(), f"{DATASETS}/info", path="b").json()["rows"] == 3


# ---------------------------------------------------------------------- tags (D96)


def test_tags(flock: S3Flock, writer: Writer):
    writer.write("d", table(0, 1))
    writer.write("d", table(1, 1), mode="append")
    writer.write("d", table(2, 1), mode="append")
    api = flock.client()
    tags = f"{DATASETS}/tags"

    def put_tag(tag: str, version: int, **body: Any) -> Any:
        return api.request(
            "PUT", f"{tags}/{quote(tag, safe='')}", body={"path": "d", "version": version, **body}
        )

    assert put_tag("v1.0", 1).json() == {"tag": "v1.0", "version": 1}
    assert put_tag("v1.0", 1).status == 200  # already there
    moving = put_tag("v1.0", 3)
    assert moving.status == 409
    assert moving.json()["error"] == "v1.0 is on version 1." and moving.json()["version"] == 1
    assert put_tag("v1.0", 3, move=True).status == 200
    assert get(api, f"{DATASETS}/info", path="d", version="v1.0").json()["version"] == 3
    history = get(api, f"{DATASETS}/versions", path="d").json()["versions"]
    assert [v["tags"] for v in history] == [["v1.0"], [], []]

    # Created elsewhere meanwhile: the second gets 409, offering the move.
    writer.open("d").tags.create("best", 2)
    raced = put_tag("best", 1)
    assert raced.status == 409 and raced.json()["version"] == 2

    for bad in ("ü", ".hidden", "-x", "a b", "x" * 101, "123"):
        assert put_tag(bad, 1).status in (404, 422), bad
    assert put_tag("x" * 100, 1).status == 200
    assert put_tag("t", 9).status == 404

    assert delete(api, f"{tags}/v1.0", path="d").status == 204
    assert get(api, f"{DATASETS}/info", path="d", version="v1.0").status == 404
    assert delete(api, f"{tags}/v1.0", path="d").status == 404
    assert sorted(t["name"] for t in get(api, f"{DATASETS}/info", path="d").json()["tags"]) == [
        "best",
        "x" * 100,
    ]
