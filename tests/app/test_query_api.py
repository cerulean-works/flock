import json
import math

import orjson
import pytest

from tests.app.utils import history_rows

# ---------------------------------------------------------------------- protocol


def test_upsert_creates_then_updates(api):
    first = api.create_run("p", "r1", config={"lr": 0.1}, tags=["a"])
    assert first["inserted"] is True
    assert first["bucket"]["name"] == "r1"
    assert first["bucket"]["project"]["name"] == "p"
    assert first["bucket"]["project"]["entity"]["name"] == "flock"
    assert first["bucket"]["displayName"].endswith("-1")  # generated adjective-noun-N
    second = api.create_run("p", "r1", config={"lr": 0.2, "bs": 8}, display_name="renamed")
    assert second["inserted"] is False
    assert second["bucket"]["displayName"] == "renamed"
    assert json.loads(second["bucket"]["config"]) == {"lr": {"value": 0.2}, "bs": {"value": 8}}
    row = api.runs("p", columns=["config.*", "tags", "display_name"])["data"]
    assert row == {
        "config.bs": [8],
        "config.lr": [0.2],
        "tags": [["a"]],
        "display_name": ["renamed"],
    }


def test_file_stream_retries_do_not_duplicate_history(api):
    api.create_run("p", "r1")
    batch = history_rows(10, loss=lambda s: 1 / (s + 1))
    api.stream("p", "r1", batch[:6])
    api.stream("p", "r1", batch[4:], offset=4)  # overlapping retry
    api.stream("p", "r1", batch[4:], offset=4)
    curve = api.get("/api/v1/projects/flock/p/runs/r1/history", keys="loss").json()["series"]
    assert curve["loss"]["x"] == list(range(10))


def test_non_finite_values_and_bad_lines(api):
    api.create_run("p", "r1")
    history = [
        '{"_step": 0, "loss": NaN}',
        '{"_step": 1, "loss": Infinity}',
        "{not json",
        '{"_step": 3, "loss": 0.5, "nested": {"a": {"b": 2}}, "img": {"_type": "image-file"}}',
    ]
    api.stream("p", "r1", history)
    series = api.get("/api/v1/projects/flock/p/runs/r1/history").json()["series"]
    assert series["loss"] == {"x": [0, 1, 3], "y": [None, None, 0.5]}
    assert series["nested.a.b"] == {"x": [3], "y": [2.0]}
    assert "img" not in series


def test_summary_replaces_previous_summary(api):
    api.create_run("p", "r1")
    api.stream("p", "r1", summary={"loss": 1.0, "gone": 5, "nested": {"x": 1}})
    api.stream("p", "r1", summary={"loss": 0.5, "nested": {"y": 2}})
    data = api.runs("p", columns=["summary.*"])["data"]
    assert data == {"summary.loss": [0.5], "summary.nested.y": [2]}


def test_run_states(api):
    for name in ("ok", "bad", "pre"):
        api.create_run("p", name)
    api.stream("p", "ok", complete=0)
    api.stream("p", "bad", complete=137)
    api.stream("p", "pre", preempting=True)
    data = api.runs("p", columns=["state", "exitcode"], sort=["name"])
    assert data["ids"] == ["bad", "ok", "pre"]
    assert data["data"] == {
        "state": ["failed", "finished", "preempting"],
        "exitcode": [137, 0, None],
    }


def test_resume_status_reports_tail(api):
    api.create_run("p", "r1", config={"lr": 0.1})
    api.stream("p", "r1", history_rows(3, loss=float), summary={"loss": 2.0})
    query = """
    query RunResumeStatus($project: String, $entity: String, $name: String!) {
      model(name: $project, entityName: $entity) {
        bucket(name: $name, missingOk: true) {
          historyLineCount historyTail summaryMetrics config wandbConfig(keys: ["t"])
        }
      }
    }"""
    bucket = api.graphql(query, {"project": "p", "name": "r1"}).json()["data"]["model"]["bucket"]
    assert bucket["historyLineCount"] == 3
    (last,) = json.loads(bucket["historyTail"])
    assert json.loads(last)["_step"] == 2
    assert json.loads(bucket["summaryMetrics"]) == {"loss": 2.0}
    missing = api.graphql(query, {"project": "p", "name": "nope"}).json()["data"]["model"]
    assert missing["bucket"] is None
    assert api.graphql(query, {"project": "zz", "name": "r1"}).json()["data"]["model"] is None


def test_unknown_operations_and_bad_requests(api, flock):
    body = api.graphql("query Mystery { mystery { id } }").json()
    assert body["data"] is None and "Mystery" in body["errors"][0]["message"]
    # Operations are recognised by their root field when no name is sent.
    viewer = api.graphql("{ viewer { entity } }").json()
    assert viewer["data"]["viewer"]["entity"] == "flock"
    assert api.post("/graphql", b"not json").status == 400
    assert api.post("/sdk/otel/v1/metrics", b"\x00\x01").status == 200
    api.create_run("p", "r1")
    bad_upload = api.request(
        "PUT", "/storage/flock/p/r1/config.yaml", params={"sig": "x"}, body=b"a"
    )
    assert bad_upload.status == 403


def test_uploads_use_signed_urls(api):
    api.create_run("p", "r1")
    query = """
    mutation CreateRunFiles($entity: String!, $project: String!, $run: String!, $files: [String!]!) {
      createRunFiles(input: {entityName: $entity, projectName: $project, runName: $run, files: $files}) {
        runID uploadHeaders files { name uploadUrl }
      }
    }"""
    files = ["config.yaml", "media/images/a.png", "../escape.txt"]
    resp = api.graphql(query, {"entity": "flock", "project": "p", "run": "r1", "files": files})
    assert "invalid file name" in resp.json()["errors"][0]["message"]
    resp = api.graphql(query, {"entity": "flock", "project": "p", "run": "r1", "files": files[:2]})
    urls = {f["name"]: f["uploadUrl"] for f in resp.json()["data"]["createRunFiles"]["files"]}
    path = urls["media/images/a.png"].split(str(api.port), 1)[1]
    assert api.request("PUT", path, body=b"\x89PNG").status == 200
    listing = api.get("/api/v1/projects/flock/p/runs/r1/files").json()["files"]
    assert [f["name"] for f in listing] == ["media/images/a.png"]
    assert api.get("/api/v1/projects/flock/p/runs/r1/files/media/images/a.png").body == b"\x89PNG"


def test_stale_runs_are_marked_crashed_and_revive(make_flock, wait_for):
    server = make_flock(crash_timeout=0.3)
    api = server.client()
    api.create_run("p", "r1")
    api.stream("p", "r1", history_rows(2, loss=float))
    wait_for(lambda: api.runs("p", columns=["state"])["data"]["state"] == ["crashed"])
    api.stream("p", "r1", history_rows(1, start=2, loss=float), offset=2)  # client came back
    assert api.runs("p", columns=["state"])["data"]["state"] == ["running"]


def test_history_cache_eviction_and_compaction(make_flock):
    server = make_flock(history_cache_points=100)
    api = server.client()
    expected = {}
    for i in range(5):
        name = f"r{i}"
        api.create_run("p", name)
        history = history_rows(40, loss=lambda s, i=i: i + s / 100, acc=lambda s: s / 40)
        api.stream("p", name, history[:20])
        api.stream("p", name, history[20:], offset=20, complete=0)
        expected[name] = [i + s / 100 for s in range(40)]
    server.call(server.store.flush())
    store = server.store
    assert all(r.compacted["history"] == 40 for r in store.runs_by_pk.values())
    for _ in range(2):  # second pass reads evicted runs back from compacted blobs
        data = api.get("/api/v1/projects/flock/p/history", keys="loss,acc").json()["runs"]
        assert {r["id"]: r["series"]["loss"]["y"] for r in data} == pytest.approx(expected)
        assert sum(h.points for h in store._history.values()) <= 100 + 80

    server.stop()
    restarted = make_flock(reuse=server)
    data = restarted.client().get("/api/v1/projects/flock/p/history", keys="loss").json()["runs"]
    assert {r["id"]: r["series"]["loss"]["y"] for r in data} == pytest.approx(expected)


def test_resumed_compacted_run_appends(api, flock):
    api.create_run("p", "r1")
    api.stream("p", "r1", history_rows(5, loss=float), complete=0)
    flock.call(flock.store.flush())
    flock.store._history.clear()  # force the lazily loaded, compacted representation
    api.stream("p", "r1", history_rows(3, start=5, loss=float), offset=5)
    series = api.get("/api/v1/projects/flock/p/runs/r1/history", keys="loss").json()["series"]
    assert series["loss"]["x"] == list(range(8))
    assert api.runs("p", columns=["state"])["data"]["state"] == ["running"]


def test_resumed_uncompacted_run_does_not_duplicate_history(api, flock):
    """A finished run whose compaction never landed (e.g. server crash) is rebuilt from raw
    lines on resume; the lines of the resuming request must not be applied twice."""
    api.create_run("p", "r1")
    api.stream("p", "r1", history_rows(5, loss=float), complete=0)

    async def forget_compaction():
        store = flock.store
        await store.flush()
        run = store.get_run("flock", "p", "r1")
        run.compacted["history"] = 0
        store._history.pop((run.pk, "history")).detach()

    flock.call(forget_compaction())
    api.stream("p", "r1", history_rows(3, start=5, loss=float), offset=5)
    series = api.get("/api/v1/projects/flock/p/runs/r1/history", keys="loss").json()["series"]
    assert series["loss"]["x"] == list(range(8))
    assert series["loss"]["y"] == [float(s) for s in range(8)]


# ---------------------------------------------------------------------- run table queries


@pytest.fixture
def table_api(api):
    """Six runs with mixed config/summary types, created oldest (r0) to newest (r5)."""
    specs = [
        ({"lr": 0.1, "opt": "adam", "amp": True}, {"loss": 0.9, "acc": 0.1}, ["base"]),
        ({"lr": 0.01, "opt": "sgd", "amp": False}, {"loss": 0.5, "acc": 0.5}, ["base", "v2"]),
        ({"lr": 0.001, "opt": "adamw"}, {"loss": 0.2, "acc": 0.7, "extra": "yes"}, ["v2"]),
        ({"lr": 0.001, "opt": "adam"}, {"loss": 0.4}, []),
        ({"opt": "lion", "layers": [1, 2]}, {"loss": float("nan")}, ["exp"]),
        ({"lr": 0.05, "opt": "sgd"}, {"acc": 0.9, "loss": "diverged"}, ["exp", "base"]),
    ]
    for i, (config, summary, tags) in enumerate(specs):
        api.create_run("t", f"r{i}", config=config, tags=tags)
        api.stream("t", f"r{i}", summary=summary, complete=0 if i % 2 == 0 else None)
    return api


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ({"config.lr": {"$lt": 0.01}}, ["r3", "r2"]),
        ({"config.lr": {"$gte": 0.01, "$lte": 0.05}}, ["r5", "r1"]),
        ({"config.opt": "adam"}, ["r3", "r0"]),
        ({"config.opt": {"$in": ["adam", "sgd"]}}, ["r5", "r3", "r1", "r0"]),
        ({"config.opt": {"$nin": ["adam", "sgd"]}}, ["r4", "r2"]),
        ({"config.opt": {"$regex": "^adam"}}, ["r3", "r2", "r0"]),
        ({"config.lr": {"$ne": 0.001}}, ["r5", "r4", "r1", "r0"]),  # missing counts as "not equal"
        ({"config.lr": {"$exists": False}}, ["r4"]),
        ({"config.amp": True}, ["r0"]),
        ({"config.amp": False}, ["r1"]),
        ({"config.layers": 2}, ["r4"]),  # list membership
        ({"tags": "base"}, ["r5", "r1", "r0"]),
        ({"tags": {"$in": ["v2", "exp"]}}, ["r5", "r4", "r2", "r1"]),
        ({"tags": []}, ["r3"]),
        ({"summary.loss": {"$lt": 0.5}}, ["r3", "r2"]),  # NaN and strings never compare
        ({"summary.loss": "diverged"}, ["r5"]),
        ({"summary_metrics.acc": {"$gt": 0.4}}, ["r5", "r2", "r1"]),
        ({"summary.extra": {"$exists": True}}, ["r2"]),
        ({"state": "running"}, ["r5", "r3", "r1"]),
        ({"$or": [{"summary.acc": {"$gt": 0.8}}, {"config.opt": "adamw"}]}, ["r5", "r2"]),
        ({"$and": [{"tags": "base"}, {"$not": {"config.opt": "sgd"}}]}, ["r0"]),
        ({"$nor": [{"tags": "base"}, {"tags": "exp"}]}, ["r3", "r2"]),
        ({"config.opt": {"$not": {"$in": ["adam", "sgd"]}}}, ["r4", "r2"]),
        ({"no.such.column": {"$gt": 1}}, []),
        ({"no.such.column": None}, ["r5", "r4", "r3", "r2", "r1", "r0"]),
    ],
)
def test_filters(table_api, expr, expected):
    data = table_api.runs("t", filter=expr, all_ids=True)
    assert data["all_ids"] == expected
    assert data["total"] == len(expected)


def test_filter_via_query_string(table_api):
    resp = table_api.get(
        "/api/v1/projects/flock/t/runs", filter=json.dumps({"config.opt": "sgd"}), columns="name"
    )
    assert resp.json()["data"]["name"] == ["r5", "r1"]


@pytest.mark.parametrize(
    "bad",
    [{"$bogus": []}, {"config.lr": {"$near": 1}}, {"config.opt": {"$regex": "("}}, [1, 2]],
)
def test_invalid_filters_are_rejected(table_api, bad):
    resp = table_api.post("/api/v1/projects/flock/t/runs/query", {"filter": bad})
    assert resp.status == 400
    assert "error" in resp.json()


def test_sorting(table_api):
    by_lr = table_api.runs("t", sort="config.lr", columns=["config.lr"])
    assert by_lr["ids"] == ["r2", "r3", "r1", "r5", "r0", "r4"]  # missing last
    by_lr_desc = table_api.runs("t", sort="-config.lr")
    assert by_lr_desc["ids"] == ["r0", "r5", "r1", "r2", "r3", "r4"]
    by_opt = table_api.runs("t", sort=[{"key": "config.opt"}, {"key": "name", "desc": True}])
    assert by_opt["ids"] == ["r3", "r0", "r2", "r4", "r5", "r1"]
    mixed = table_api.runs("t", sort="summary.loss")
    assert mixed["ids"][:3] == ["r2", "r3", "r1"]
    assert table_api.runs("t")["ids"] == ["r5", "r4", "r3", "r2", "r1", "r0"]


def test_columns_and_paging(table_api):
    page = table_api.runs("t", columns=["config.l*", "summary.loss", "nope"], limit=2, offset=1)
    assert page["total"] == 6
    assert page["ids"] == ["r4", "r3"]
    assert page["columns"] == ["config.layers", "config.lr", "summary.loss", "nope"]
    assert page["data"]["config.lr"] == [None, 0.001]
    assert page["data"]["config.layers"] == [[1, 2], None]
    assert page["data"]["summary.loss"][1] == 0.4 and page["data"]["summary.loss"][0] is None
    assert page["data"]["nope"] == [None, None]

    cols = table_api.get("/api/v1/projects/flock/t/columns", prefix="summary.").json()["columns"]
    kinds = {c["name"]: (c["type"], c["count"]) for c in cols}
    assert kinds["summary.loss"] == ("mixed", 6)
    assert kinds["summary.acc"] == ("number", 4)
    assert kinds["summary.extra"] == ("string", 1)
    found = table_api.get("/api/v1/projects/flock/t/columns", q="AMP").json()["columns"]
    assert [c["name"] for c in found] == ["config.amp"]


def test_not_found(api):
    assert api.get("/api/v1/projects/flock/nope/runs").status == 404
    api.create_run("p", "r1")
    assert api.get("/api/v1/projects/flock/p/runs/nope").status == 404
    assert api.get("/api/v1/projects/flock/p/runs/r1/files/x.txt").status == 404


# ---------------------------------------------------------------------- curves


@pytest.fixture
def curves_api(api):
    for i in range(60):
        name = f"c{i:02d}"
        api.create_run("curves", name, config={"idx": i})
        history = history_rows(30, loss=lambda s, i=i: i + s / 100, epoch=lambda s: s // 10)
        api.stream("curves", name, history, complete=None if i >= 57 else 0)
    return api


def test_history_defaults_to_newest_50_runs(curves_api):
    data = curves_api.get("/api/v1/projects/flock/curves/history", keys="loss").json()
    assert [r["id"] for r in data["runs"]] == [f"c{i:02d}" for i in range(59, 9, -1)]
    first = data["runs"][0]
    assert first["state"] == "running"
    assert first["series"]["loss"]["x"] == list(range(30))
    assert first["series"]["loss"]["y"] == pytest.approx([59 + s / 100 for s in range(30)])


def test_history_selection(curves_api):
    base = "/api/v1/projects/flock/curves/history"
    running = curves_api.get(base, keys="loss", running="1").json()["runs"]
    assert [r["id"] for r in running] == ["c59", "c58", "c57"]
    explicit = curves_api.get(base, keys="loss,missing", runs="c03,c01,zzz").json()["runs"]
    assert [r["id"] for r in explicit] == ["c03", "c01"]
    assert set(explicit[0]["series"]) == {"loss"}
    filtered = curves_api.post(
        f"{base}/query",
        {"keys": ["loss"], "filter": {"config.idx": {"$lt": 5}}, "sort": "config.idx", "limit": 3},
    ).json()["runs"]
    assert [r["id"] for r in filtered] == ["c00", "c01", "c02"]
    assert curves_api.get(base).status == 422  # keys is required


def test_history_x_axis_and_downsampling(curves_api):
    base = "/api/v1/projects/flock/curves/runs/c05/history"
    by_time = curves_api.get(base, keys="loss", x="_runtime").json()["series"]["loss"]
    assert by_time["x"] == pytest.approx([s * 0.1 for s in range(30)])
    sampled = curves_api.get(base, keys="loss", max_points=7).json()["series"]["loss"]
    assert len(sampled["x"]) == 7
    assert sampled["x"][0] == 0 and sampled["x"][-1] == 29
    assert sampled["y"][-1] == pytest.approx(5.29)
    by_epoch = curves_api.get(base, keys="loss", x="epoch").json()["series"]["loss"]
    assert by_epoch["x"][:11] == [0.0] * 10 + [1.0]
    assert all(not math.isnan(v) for v in by_epoch["y"])


def test_run_detail_and_logs(api):
    api.create_run("p", "r1", config={"lr": 0.1, "_wandb": {"t": 1}})
    api.stream(
        "p",
        "r1",
        history_rows(2, loss=float),
        summary={"loss": 1.0, "_wandb": {"runtime": 3}},
    )
    api.file_stream(
        "flock",
        "p",
        "r1",
        {"files": {"output.log": {"offset": 0, "content": ["line a", "line b"]}}},
    )
    api.file_stream(
        "flock", "p", "r1", {"files": {"output.log": {"offset": 1, "content": ["line b!"]}}}
    )
    detail = api.get("/api/v1/projects/flock/p/runs/r1").json()
    assert detail["config"] == {"lr": 0.1}
    assert detail["summary"] == {"loss": 1.0}
    assert detail["history_keys"] == ["_runtime", "_step", "loss"]
    logs = api.get("/api/v1/projects/flock/p/runs/r1/logs").json()
    assert logs == {
        "total": 2,
        "lines": [{"line": 0, "text": "line a"}, {"line": 1, "text": "line b!"}],
    }
    assert orjson.loads(api.get("/api/v1/projects").body)["projects"][0]["run_count"] == 1
