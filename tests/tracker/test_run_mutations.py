import asyncio

from src.api.dashboard import Snapshot
from tests.tracker.utils import history_rows, system_samples

RUNS = "/api/v1/projects/flock/p/runs"


def table(api, project: str = "p", columns: str = "tags,group,notes,display_name") -> dict:
    data = api.runs(project, columns=columns, sort="name")
    return {
        name: dict(zip(data["columns"], row))
        for name, *row in zip(data["ids"], *data["data"].values())
    }


# ---------------------------------------------------------------------- edits


def test_patch_sets_and_clears_attributes(make_tracker, events):
    server = make_tracker()
    api = server.client()
    api.create_run("p", "r1", tags=["a"])
    live = events("p", "r1", client=api)
    live.next_of("hello")

    edit = {"display_name": "nice", "notes": "hello", "tags": ["x", "y"], "group": "g"}
    detail = api.patch(f"{RUNS}/r1", edit).json()
    assert {key: detail[key] for key in edit} == edit
    assert live.next_of("run")["attributes"] == edit
    assert api.runs("p", filter={"group": "g", "tags": "y"})["ids"] == ["r1"]  # filterable now

    detail = api.patch(f"{RUNS}/r1", {"notes": None}).json()
    assert (detail["notes"], detail["group"]) == (None, "g")  # cleared; others unchanged
    assert api.runs("p", filter={"notes": {"$exists": True}})["total"] == 0
    assert api.patch(f"{RUNS}/r1", {"name": "renamed"}).status == 422  # run ids never change
    assert api.patch(f"{RUNS}/ghost", {"notes": "x"}).status == 404

    server.stop()
    restarted = make_tracker(reuse=server).client()
    assert table(restarted)["r1"] == {
        "tags": ["x", "y"],
        "group": "g",
        "notes": None,
        "display_name": "nice",
    }


def test_bulk_update_by_filter(api):
    for i in range(4):
        api.create_run("p", f"r{i}", config={"i": i}, tags=["base", "keep"])
    body = {
        "filter": {"config.i": {"$gte": 2}},
        "set": {"group": "big"},
        "add_tags": ["large"],
        "remove_tags": ["base"],
    }
    resp = api.post(f"{RUNS}/update", body).json()
    assert sorted(resp["ids"]) == ["r2", "r3"]
    rows = table(api, columns="tags,group")
    assert rows["r0"] == {"tags": ["base", "keep"], "group": None}
    assert rows["r3"] == {"tags": ["keep", "large"], "group": "big"}

    dry = api.post(f"{RUNS}/update", {"all": True, "add_tags": ["z"], "dry_run": True}).json()
    assert (dry["count"], dry["dry_run"]) == (4, True)
    assert "z" not in table(api, columns="tags")["r0"]["tags"]
    assert api.post(f"{RUNS}/update", {"add_tags": ["z"]}).status == 400  # selects nothing
    missing = api.post(f"{RUNS}/update", {"runs": ["r0", "nope"], "add_tags": ["z"]})
    assert missing.status == 404
    assert missing.json()["ids"] == ["nope"]
    assert api.post(f"{RUNS}/update", {"all": True, "set": {"state": "x"}}).status == 422


# ---------------------------------------------------------------------- deletes


def test_delete_runs(make_tracker, events):
    server = make_tracker()
    store = server.store
    api = server.client()
    for i in range(5):
        api.create_run("p", f"r{i}", config={"i": i})
        api.stream(
            "p",
            f"r{i}",
            history_rows(5, only0=float) if i == 0 else history_rows(5, loss=float),
            events=system_samples(2, {"system.cpu": float}),
            complete=None if i == 4 else 0,  # r4 is still running
        )
    pk0 = store.get_run("flock", "p", "r0").pk
    server.call(store.save_file(store.get_run("flock", "p", "r0"), "config.yaml", b"x"))
    server.call(store.flush())
    assert api.workspace()["metrics"] == ["loss", "only0"]
    live = events("p", "r1", client=api)
    live.next_of("hello")

    assert api.delete(f"{RUNS}/r0").status == 204
    assert live.next_of("deleted") == {"runs": ["r0"]}
    assert not (server.files_dir / str(pk0)).exists()
    assert api.workspace()["metrics"] == ["loss"]  # no run logs only0 any more

    refused = api.post(f"{RUNS}/delete", {"filter": {"config.i": {"$gte": 3}}})
    assert refused.status == 409
    assert refused.json()["ids"] == ["r4"]
    dry = api.post(f"{RUNS}/delete", {"filter": {"config.i": {"$gte": 3}}, "dry_run": True})
    assert sorted(dry.json()["ids"]) == ["r3", "r4"]
    deleted = api.post(f"{RUNS}/delete", {"filter": {"config.i": {"$gte": 3}}, "force": True})
    assert sorted(deleted.json()["ids"]) == ["r3", "r4"]
    assert api.post(f"{RUNS}/delete", {}).status == 400

    def check(client) -> None:
        assert client.runs("p", sort="config.i")["ids"] == ["r1", "r2"]
        assert client.runs("p")["ids"] == ["r2", "r1"]  # newest first
        assert client.runs("p", filter={"config.i": {"$gt": 1}})["ids"] == ["r2"]
        snapshot = client.workspace(system="true")
        assert [r["id"] for r in snapshot["runs"]] == ["r2", "r1"]
        assert snapshot["curves"][("r1", "loss")] == ([0, 1, 2, 3, 4], [0.0, 1.0, 2.0, 3.0, 4.0])
        assert snapshot["system_metrics"] == ["system.cpu"]
        project = client.get("/api/v1/projects/flock/p").json()
        assert (project["run_count"], project["active_run_count"]) == (2, 0)

    check(api)
    assert set(store.runs_by_pk) == {r.pk for r in store.project("flock", "p").runs}
    server.stop()
    check(make_tracker(reuse=server).client())


def test_delete_while_reading_leaves_nothing_cached(make_tracker):
    server = make_tracker()
    store = server.store
    api = server.client()
    for i in range(10):
        api.create_run("p", f"r{i}")
        api.stream("p", f"r{i}", history_rows(50, loss=float), complete=0)
    server.call(store.flush())
    for key in list(store._history):
        store._history.pop(key).detach()
    project = store.project("flock", "p")
    for run in project.runs[:3]:
        run.compacted["history"] = 0  # read these back from raw lines instead

    async def race() -> None:
        runs = list(project.runs)
        reads = [
            asyncio.ensure_future(store.histories(runs, ["loss"])),
            asyncio.ensure_future(Snapshot.build(store, project, None, 0, 100, 500)),
        ]
        await asyncio.sleep(0)  # both are now waiting on the database
        await store.delete_runs(project, runs[::2])
        await asyncio.gather(*reads)

    server.call(race())
    alive = {run.pk for run in project.runs}
    assert len(alive) == 5
    assert {pk for pk, _ in store._history} <= alive
    assert store._history_points.total == sum(h.points for h in store._history.values())
    assert [r["id"] for r in api.workspace()["runs"]] == ["r9", "r7", "r5", "r3", "r1"]


def test_files_of_deleted_runs_are_swept_on_startup(make_tracker):
    server = make_tracker()
    api = server.client()
    api.create_run("p", "r1")
    orphan = server.files_dir / "987654"  # a run whose delete committed before a crash
    orphan.mkdir()
    (orphan / "output.log").write_text("x")
    server.stop()
    make_tracker(reuse=server)
    assert not orphan.exists()


# ---------------------------------------------------------------------- moves


def test_move_runs(make_tracker, events):
    server = make_tracker()
    store = server.store
    api = server.client()
    api.create_run("src", "a", config={"k": 1})
    api.stream("src", "a", history_rows(3, a_only=float), summary={"acc": 0.5}, complete=0)
    api.create_run("dst", "x")
    api.stream("dst", "x", history_rows(2, loss=float), complete=0)
    api.create_run("src", "b")
    api.stream("src", "b", history_rows(3, loss=float), events=system_samples(2, {"cpu": float}))
    api.stream("src", "b", complete=0)
    api.create_run("src", "live")  # still running
    server.call(store.save_file(store.get_run("flock", "src", "a"), "config.yaml", b"k: 1\n"))
    server.call(store.flush())
    source_events = events("src", "a", client=api)
    target_events = events("dst", "x", client=api)
    source_events.next_of("hello")
    target_events.next_of("hello")
    base = "/api/v1/projects/flock/src/runs"

    refused = api.post(f"{base}/move", {"runs": ["a", "live"], "to": "dst"})
    assert refused.status == 409
    assert refused.json()["ids"] == ["live"]
    missing = api.post(f"{base}/move", {"runs": ["a"], "to": "nowhere"})
    assert missing.status == 404
    moved = api.post(f"{base}/move", {"runs": ["a", "b"], "to": "dst"}).json()
    assert moved["ids"] == ["a", "b"]
    assert source_events.next_of("deleted") == {"runs": ["a", "b"], "moved_to": "dst"}
    assert {target_events.next_of("created")["run"] for _ in range(2)} == {"a", "b"}

    def check(client) -> None:
        assert client.runs("src")["ids"] == ["live"]
        assert client.runs("dst")["ids"] == ["b", "x", "a"]  # by creation time, newest first
        row = client.runs("dst", filter={"config.k": 1}, columns="summary.acc,state")
        assert (row["ids"], row["data"]) == (["a"], {"summary.acc": [0.5], "state": ["finished"]})
        assert client.run_history("dst", "a", keys="a_only")["a_only"]["y"] == [0.0, 1.0, 2.0]
        assert client.run_history("dst", "b", stream="system", keys="cpu")["cpu"]["y"] == [0, 1]
        files = client.get("/api/v1/projects/flock/dst/runs/a/files").json()["files"]
        assert [f["name"] for f in files] == ["config.yaml"]
        assert client.workspace("src")["metrics"] == []
        assert client.workspace("dst")["metrics"] == ["a_only", "loss"]
        assert client.get("/api/v1/projects/flock/src/runs/a").status == 404

    check(api)
    clash = api.post(f"{base}/move", {"runs": ["live"], "to": "dst"})  # active, so refused
    assert clash.status == 409
    api.create_run("src", "x")
    api.stream("src", "x", complete=0)
    collision = api.post(f"{base}/move", {"runs": ["x"], "to": "dst"})
    assert collision.status == 409
    assert collision.json()["ids"] == ["x"]
    created = api.post(f"{base}/move", {"runs": ["x"], "to": "fresh", "create": True})
    assert created.json()["ids"] == ["x"]
    assert api.delete("/api/v1/projects/flock/fresh/runs/x").status == 204

    server.stop()
    check(make_tracker(reuse=server).client())
