"""Forks and blends (023): made by the API, read through the proxy as users read datasets, and
kept from breaking: pins on the sources (D173) and sources that can't move (D177)."""

from collections.abc import Callable
from typing import Any

import pytest
from botocore.exceptions import ClientError

from tests.app.conftest import Client, S3Flock
from tests.app.test_datasets import (
    DATASETS,
    Writer,
    delete,
    flock,  # noqa: F401 - fixture
    get,
    keys,
    options,
    table,
    upstream_name,
    writer,  # noqa: F401 - fixture
)

lance = pytest.importorskip("lance")
pa = pytest.importorskip("pyarrow")


def tags(w: Writer, path: str) -> dict[str, Any]:
    return w.open(path).tags.list()


def pins(w: Writer, path: str) -> dict[str, int]:
    return {
        name: info["version"]
        for name, info in tags(w, path).items()
        if name.startswith("flock-ref-")
    }


def wait_job(api: Client, job: dict[str, Any], wait_for: Callable[..., Any]) -> dict[str, Any]:
    def done() -> dict[str, Any] | None:
        got = get(api, f"{DATASETS}/jobs/{job['id']}").json()
        return got if got["state"] != "running" else None

    return wait_for(done, timeout=60)


def blend(api: Client, body: dict[str, Any], wait_for: Callable[..., Any]) -> dict[str, Any]:
    started = api.post(f"{DATASETS}/blend", body)
    assert started.status == 202, started.body
    finished = wait_job(api, started.json(), wait_for)
    assert finished["state"] == "finished", finished
    return finished


def code_table(start: int, count: int) -> Any:
    ids = list(range(start, start + count))
    return pa.table(
        {
            "doc_id": pa.array(ids, pa.int64()),
            "content": [f"code {i}" for i in ids],
            "score": pa.array([i % 5 for i in ids], pa.int32()),
        }
    )


# ---------------------------------------------------------------------- forks (D171)


def test_a_fork_reads_its_source_in_place_and_edits_stay_in_it(
    flock: S3Flock,  # noqa: F811
    writer: Writer,  # noqa: F811
) -> None:
    writer.write("web/edu", table(0, 100), max_rows_per_file=30)
    writer.write("web/edu", table(100, 50), mode="append")
    api = flock.client()

    made = api.post(f"{DATASETS}/fork", {"path": "web/edu", "version": 1, "to": "exp/edu"})
    assert made.status == 201, made.body
    assert made.json()["source"] == {"path": "web/edu", "version": 1}
    # Only metadata was written: no data files of its own.
    bucket = upstream_name(flock)
    assert not [k for k in keys(flock, bucket, "exp/edu/") if "/data/" in k]

    # Read and edited through the proxy, as users do: the manifest names the source by its
    # upstream bucket, which the proxy takes (D172).
    fork = writer.open("exp/edu")
    assert fork.count_rows() == 100
    fork.delete("id < 10")
    assert writer.open("exp/edu").count_rows() == 90
    assert writer.open("web/edu").count_rows() == 150
    assert writer.open("web/edu", 1).count_rows() == 100

    info = get(api, f"{DATASETS}/info", path="exp/edu").json()
    assert info["origin"]["kind"] == "fork"
    assert info["origin"]["source"] == {"path": "web/edu", "version": 1}
    assert info["rows"] == 90
    source = get(api, f"{DATASETS}/info", path="web/edu").json()
    assert source["dependents"] == {"count": 1, "trashed": 0, "paths": ["exp/edu"]}
    # The pin is a tag on the version read, which Flock hides (D179) and owns (D173).
    assert list(pins(writer, "web/edu").values()) == [1]
    assert all(not t["name"].startswith("flock-ref-") for t in source["tags"])
    versions = get(api, f"{DATASETS}/versions", path="web/edu").json()["versions"]
    assert {v["version"]: v["kept_for"] for v in versions} == {2: 0, 1: 1}
    assert all(not t.startswith("flock-ref-") for v in versions for t in v["tags"])
    (pin,) = pins(writer, "web/edu")
    refused = api.request("DELETE", f"{DATASETS}/tags/{pin}", params={"path": "web/edu"})
    assert refused.status == 422
    taken = api.request(
        "PUT", f"{DATASETS}/tags/flock-ref-mine", body={"path": "web/edu", "version": 1}
    )
    assert taken.status == 422

    listing = get(api, f"{DATASETS}/at", path="exp").json()
    assert [(d["path"], d["origin"]) for d in listing["datasets"]] == [("exp/edu", "fork")]

    again = api.post(f"{DATASETS}/fork", {"path": "web/edu", "to": "exp/edu"})
    assert again.status == 409 and again.json()["taken"]


def test_a_pinned_source_survives_cleanup_and_forks_of_forks_pin_both(
    flock: S3Flock,  # noqa: F811
    writer: Writer,  # noqa: F811
) -> None:
    from datetime import timedelta

    writer.write("a", table(0, 40), max_rows_per_file=10)
    api = flock.client()
    assert api.post(f"{DATASETS}/fork", {"path": "a", "to": "f"}).status == 201
    writer.open("f").delete("id >= 30")
    assert api.post(f"{DATASETS}/fork", {"path": "f", "to": "g"}).status == 201
    # g reads f's version 2 and, through it, a's version 1.
    assert list(pins(writer, "f").values()) == [2]
    assert list(pins(writer, "a").values()) == [1, 1]

    writer.write("a", table(100, 5), mode="overwrite")
    writer.open("a").cleanup_old_versions(
        older_than=timedelta(0), error_if_tagged_old_versions=False
    )
    assert writer.open("g").count_rows() == 30
    assert sorted(writer.open("g").to_table()["id"].to_pylist()) == list(range(30))


# ---------------------------------------------------------------------- sources stay put (D177)


def test_sources_cant_move_until_their_dependents_are_gone(
    flock: S3Flock,  # noqa: F811
    writer: Writer,  # noqa: F811
) -> None:
    writer.write("data/src", table(0, 20))
    api = flock.client()
    assert api.post(f"{DATASETS}/fork", {"path": "data/src", "to": "exp/f"}).status == 201

    renamed = api.post(f"{DATASETS}/rename", {"path": "data/src", "to": "data/other"})
    assert renamed.status == 409
    assert "exp/f" in renamed.json()["error"]
    folder = api.post(f"{DATASETS}/rename", {"path": "data", "to": "archive"})
    assert folder.status == 409
    (moved,) = api.post(f"{DATASETS}/move", {"paths": ["data/src"], "to": "x"}).json()["results"]
    assert not moved["moved"] and "exp/f" in moved["error"]
    assert api.post(f"{DATASETS}/delete", {"path": "data/src"}).status == 409
    assert api.post(f"{DATASETS}/delete", {"path": "data"}).status == 409

    # The fork moves freely, and its links follow.
    assert api.post(f"{DATASETS}/rename", {"path": "exp/f", "to": "exp/g"}).status == 200
    assert writer.open("exp/g").count_rows() == 20
    deps = get(api, f"{DATASETS}/info", path="data/src").json()["dependents"]
    assert deps["paths"] == ["exp/g"]
    assert get(api, f"{DATASETS}/info", path="exp/g").json()["origin"]["kind"] == "fork"

    # In the trash it still reads the source, so the source stays put until it's gone for good.
    entry = api.post(f"{DATASETS}/delete", {"path": "exp/g"}).json()
    refused = api.post(f"{DATASETS}/delete", {"path": "data/src"})
    assert refused.status == 409 and "trash" in refused.json()["error"]
    assert api.post(f"{DATASETS}/trash/{entry['id']}/restore", {}).status == 200
    assert writer.open("exp/g").count_rows() == 20
    assert get(api, f"{DATASETS}/info", path="exp/g").json()["origin"]["kind"] == "fork"

    entry = api.post(f"{DATASETS}/delete", {"path": "exp/g"}).json()
    assert delete(api, f"{DATASETS}/trash/{entry['id']}").status == 204
    assert pins(writer, "data/src") == {}
    assert api.post(f"{DATASETS}/rename", {"path": "data/src", "to": "data/other"}).status == 200


# ---------------------------------------------------------------------- blends (D174, D175)


def test_blend_samples_each_source_and_maps_its_columns(
    flock: S3Flock,  # noqa: F811
    writer: Writer,  # noqa: F811
    wait_for: Callable[..., Any],
) -> None:
    writer.write("web", table(0, 300), max_rows_per_file=70)
    writer.write("code", code_table(0, 200), max_rows_per_file=60)
    writer.open("code").delete("doc_id < 20")
    api = flock.client()
    sources = [{"path": "web", "rows": 100}, {"path": "code", "rows": 50}]

    preview = api.post(f"{DATASETS}/blend/preview", {"sources": sources}).json()
    assert preview["automatic"] and preview["problems"] == [
        "code gives the blend only nulls. Take at least one of its columns, or a value."
    ]
    assert [s["live"] for s in preview["sources"]] == [300, 180]
    auto = {c["name"]: c["from"]["code"] for c in preview["columns"]}
    assert auto == {"id": {"null": True}, "text": {"null": True}}
    assert [c["name"] for c in preview["unused"]["code"]] == ["doc_id", "content", "score"]

    columns = [
        {"name": "id", "type": "int64", "nullable": True,
         "from": {"web": {"column": "id"}, "code": {"column": "doc_id"}}},
        {"name": "text", "type": "string", "nullable": True,
         "from": {"web": {"column": "text"}, "code": {"column": "content"}}},
        {"name": "score", "type": "int64", "nullable": True,
         "from": {"web": {"null": True}, "code": {"column": "score", "cast": True}}},
        {"name": "source", "type": "string", "nullable": False,
         "from": {"web": {"value": "web"}, "code": {"value": "code"}}},
    ]  # fmt: skip
    body = {"sources": sources, "columns": columns, "seed": 42, "to": "mix/a"}
    checked = api.post(f"{DATASETS}/blend/preview", body).json()
    assert checked["problems"] == []
    assert {(w["column"], w["source"], w["kind"]) for w in checked["writes"]} == {
        ("score", "code", "cast"),
        ("source", "web", "value"),
        ("source", "code", "value"),
    }
    done = blend(api, body, wait_for)
    assert done["result"]["rows"] == 150

    rows = writer.open("mix/a").to_table().to_pylist()
    by = {
        "web": [r for r in rows if r["source"] == "web"],
        "code": [r for r in rows if r["source"] == "code"],
    }
    assert len(by["web"]) == 100 and len(by["code"]) == 50
    assert all(r["text"] == f"row {r['id']}" and r["score"] is None for r in by["web"])
    assert all(r["text"] == f"code {r['id']}" and r["score"] == r["id"] % 5 for r in by["code"])
    assert all(r["id"] >= 20 for r in by["code"])  # the source's deletions stay deleted
    assert len({r["id"] for r in by["web"]}) == 100

    # The same seed takes the same rows; a smaller count of the same seed takes some of them.
    blend(api, {**body, "to": "mix/b"}, wait_for)
    assert sorted(map(str, writer.open("mix/b").to_table().to_pylist())) == sorted(map(str, rows))
    smaller = [{"path": "web", "rows": 40}, {"path": "code", "rows": 50}]
    blend(api, {**body, "sources": smaller, "to": "mix/c"}, wait_for)
    web_c = {r["id"] for r in writer.open("mix/c").to_table().to_pylist() if r["source"] == "web"}
    assert len(web_c) == 40 and web_c <= {r["id"] for r in by["web"]}

    info = get(api, f"{DATASETS}/info", path="mix/a").json()
    assert info["origin"]["kind"] == "blend" and info["origin"]["seed"] == 42
    assert [(s["path"], s["rows"]) for s in info["origin"]["sources"]] == [
        ("web", 100),
        ("code", 50),
    ]
    assert get(api, f"{DATASETS}/info", path="web").json()["dependents"]["count"] == 3

    # Fractions of a total, split by largest remainder.
    fractions = [{"path": "web", "fraction": 0.66}, {"path": "code", "fraction": 0.34}]
    split = api.post(f"{DATASETS}/blend/preview", {"sources": fractions, "total": 101}).json()
    assert [s["rows"] for s in split["sources"]] == [67, 34]
    # Within 0.1% of 100% is scaled to it; further off is refused, and isn't a column problem.
    # 66.6 / 100 in floating point is 0.6659999999999999, so the sum is just past 0.1% off.
    thirds = [{"path": "web", "fraction": 66.6 / 100}, {"path": "code", "fraction": 33.3 / 100}]
    split = api.post(
        f"{DATASETS}/blend/preview", {"sources": thirds, "total": 300, "columns": columns}
    ).json()
    assert [s["rows"] for s in split["sources"]] == [200, 100] and split["problems"] == []
    short = [{"path": "web", "fraction": 0.6}, {"path": "code", "fraction": 0.3}]
    split = api.post(
        f"{DATASETS}/blend/preview", {"sources": short, "total": 300, "columns": columns}
    ).json()
    assert split["problems"] == ["The percentages add up to 90%, not 100%."]
    assert split["column_problems"] == []


def test_blend_refusals_name_every_problem(
    flock: S3Flock,  # noqa: F811
    writer: Writer,  # noqa: F811
) -> None:
    writer.write("web", table(0, 30))
    writer.write("code", code_table(0, 10))
    api = flock.client()
    columns = [
        {"name": "id", "type": "int16", "nullable": False,
         "from": {"web": {"column": "id"}, "code": {"null": True}}},
    ]  # fmt: skip
    sources = [{"path": "web", "rows": 5}, {"path": "code", "rows": 20}]
    preview = api.post(f"{DATASETS}/blend/preview", {"sources": sources, "columns": columns})
    problems = preview.json()["problems"]
    assert any("code has 10 rows, fewer than the 20" in p for p in problems)
    assert any("int64, which can't become int16" in p for p in problems)
    assert any("id can't be null" in p for p in problems)
    refused = api.post(
        f"{DATASETS}/blend", {"sources": sources, "columns": columns, "seed": 1, "to": "m"}
    )
    assert refused.status == 422
    assert keys(flock, upstream_name(flock), "m/") == []
    no_seed = api.post(f"{DATASETS}/blend", {"sources": sources, "to": "m"})
    assert no_seed.status == 422

    ids = [
        {"name": "id", "type": "int64", "nullable": True,
         "from": {"web": {"column": "id"}, "code": {"column": "doc_id"}}},
    ]  # fmt: skip
    upsampled = {"sources": sources, "columns": ids, "replace": True, "seed": 3, "to": "up"}
    assert api.post(f"{DATASETS}/blend/preview", upsampled).json()["problems"] == []


def test_a_blend_made_independent_releases_its_sources(
    flock: S3Flock,  # noqa: F811
    writer: Writer,  # noqa: F811
    wait_for: Callable[..., Any],
) -> None:
    writer.write("web", table(0, 50))
    writer.write("more", table(1000, 50))
    api = flock.client()
    body = {
        "sources": [{"path": "web", "rows": 10}, {"path": "more", "rows": 10}],
        "seed": 5,
        "to": "mix",
    }
    blend(api, body, wait_for)
    assert pins(writer, "web") and pins(writer, "more")
    assert api.post(f"{DATASETS}/delete", {"path": "web"}).status == 409

    started = api.post(f"{DATASETS}/independent", {"path": "mix"})
    assert started.status == 202, started.body
    assert wait_job(api, started.json(), wait_for)["state"] == "finished"
    assert pins(writer, "web") == {} and pins(writer, "more") == {}
    assert api.post(f"{DATASETS}/delete", {"path": "web"}).status == 200
    assert api.post(f"{DATASETS}/delete", {"path": "more"}).status == 200
    assert writer.open("mix").count_rows() == 20
    assert get(api, f"{DATASETS}/info", path="mix").json()["origin"]["independent"] is True
    again = api.post(f"{DATASETS}/independent", {"path": "mix"})
    assert again.status == 422


def test_the_proxy_takes_only_its_own_org_s_upstream_name(
    flock: S3Flock,  # noqa: F811
    writer: Writer,  # noqa: F811
) -> None:
    writer.write("a", table(0, 3))
    api = flock.client()
    assert api.post("/api/v1/orgs", {"name": "other"}).status == 201
    import boto3

    def s3(org: str) -> Any:
        opts = options(flock, org=org)
        return boto3.client(
            "s3",
            endpoint_url=opts["aws_endpoint"],
            aws_access_key_id=opts["aws_access_key_id"],
            aws_secret_access_key=opts["aws_secret_access_key"],
            region_name="us-east-1",
        )

    name = upstream_name(flock)
    listed = s3("flock").list_objects_v2(Bucket=name, Prefix="a/_versions/")
    assert listed["KeyCount"] >= 1
    with pytest.raises(ClientError) as refused:
        s3("other").list_objects_v2(Bucket=name, Prefix="a/")
    assert refused.value.response["Error"]["Code"] == "NoSuchBucket"


# ---------------------------------------------------------------------- Python (D180)


def test_python_forks_and_blends_through_the_api(
    flock: S3Flock,  # noqa: F811
    writer: Writer,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    from flock.hub import datasets

    monkeypatch.setenv("FLOCK_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("FLOCK_HOST", flock.url)
    assert flock.token
    monkeypatch.setenv("FLOCK_TOKEN", flock.token)
    writer.write("web", table(0, 60))
    writer.write("code", code_table(0, 40))

    made = datasets.fork("web", "exp/web", org="flock")
    assert made["source"] == {"path": "web", "version": 1}
    assert writer.open("exp/web").count_rows() == 60

    # Overrides change the outputs they name and add new ones; the rest stay automatic. A new
    # output takes a same-named column from sources it doesn't name (code's score), and an
    # automatic output renamed away entirely (web's id, now key) is dropped.
    columns = {
        "text": {"code": "content"},
        "source": {"web": datasets.value("web"), "code": datasets.value("code")},
        "key": {"web": "id", "code": "doc_id"},
        "score": {"web": datasets.null()},
    }
    sources = {"web@1": 0.75, "code": 0.25}
    preview = datasets.blend_preview(sources, total=40, columns=columns, org="flock")
    assert preview["problems"] == []
    by_name = {c["name"]: c for c in preview["columns"]}
    assert list(by_name) == ["text", "source", "key", "score"]
    assert by_name["text"]["from"] == {"web": {"column": "text"}, "code": {"column": "content"}}
    assert by_name["source"]["type"] == "string"
    assert by_name["score"]["from"] == {"web": {"null": True}, "code": {"column": "score"}}
    assert by_name["score"]["type"] == "int32"

    done = datasets.blend(sources, "mix", total=40, columns=columns, org="flock")
    assert done["rows"] == 40
    rows = writer.open("mix").to_table().to_pylist()
    assert sum(r["source"] == "code" for r in rows) == 10
    assert all(r["text"].startswith("code ") for r in rows if r["source"] == "code")

    with pytest.raises(datasets.DatasetError, match="exists"):
        datasets.blend(sources, "mix", total=40, columns=columns, org="flock")
    assert datasets.make_independent("mix", org="flock")["rows"] == 40


# ---------------------------------------------------------------------- across scopes (022)


def test_forks_cross_into_a_project_and_keep_it_from_being_deleted(
    flock: S3Flock,  # noqa: F811
    writer: Writer,  # noqa: F811
) -> None:
    api = flock.client()
    made = api.post("/api/v1/projects", {"entity": "flock", "name": "pretrain"})
    assert made.status == 201, made.body
    writer.write("web/edu", table(0, 40))

    # Into the project: named project:path both ways, and listed in the project's scope.
    fork = api.post(f"{DATASETS}/fork", {"path": "web/edu", "to": "pretrain:exp/edu"})
    assert fork.status == 201, fork.body
    assert fork.json()["path"] == "pretrain:exp/edu"
    at = get(api, f"{DATASETS}/info", path="exp/edu", project="pretrain").json()
    assert at["origin"]["source"]["path"] == "web/edu"
    assert get(api, f"{DATASETS}/info", path="web/edu").json()["dependents"]["paths"] == [
        "pretrain:exp/edu"
    ]

    # Out of the project: the global fork reads the project's dataset, so the project stays.
    back = api.post(f"{DATASETS}/fork", {"path": "pretrain:exp/edu", "to": "kept/edu"})
    assert back.status == 201, back.body
    refused = api.delete("/api/v1/projects/flock/pretrain")
    assert refused.status == 409 and "kept/edu" in refused.json()["error"], refused.body
    entry = api.post(f"{DATASETS}/delete", {"path": "kept/edu"}).json()
    assert delete(api, f"{DATASETS}/trash/{entry['id']}").status == 204

    # Its own fork goes with it: the project's pin on web/edu is released once it's purged.
    assert api.delete("/api/v1/projects/flock/pretrain").status == 204
    flock.call(flock.store.flush())
    assert pins(writer, "web/edu") == {}
    assert api.post(f"{DATASETS}/rename", {"path": "web/edu", "to": "web/moved"}).status == 200
