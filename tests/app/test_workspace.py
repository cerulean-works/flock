import pytest

from app.api.src.api.dashboard import Snapshot
from app.api.src.core.live import Subscriber
from tests.app.utils import history_rows

# ---------------------------------------------------------------------- snapshot


def test_snapshot_has_table_cells_and_curves(api):
    api.create_run("p", "old", config={"lr": 0.1, "opt": "adam", "model": {"depth": 4}})
    api.stream("p", "old", history_rows(10, loss=lambda s: 10 - s), summary={"loss": 1, "tag": "x"})
    api.stream("p", "old", complete=0)
    api.create_run("p", "new", config={"lr": 0.2})
    # val_loss is logged every 3rd step, acc at irregular steps.
    history = history_rows(7, loss=lambda s: s * 0.5)
    for row in history:
        if row["_step"] % 3 == 0:
            row["val_loss"] = row["_step"] + 0.25
        if row["_step"] in (0, 1, 5):
            row["acc"] = row["_step"] / 10
    api.stream("p", "new", history)

    snap = api.workspace()
    assert [r["id"] for r in snap["runs"]] == ["new", "old"]  # newest first
    assert [r["state"] for r in snap["runs"]] == ["running", "finished"]
    assert [r["lines"] for r in snap["runs"]] == [7, 10]
    assert snap["total"] == 2 and snap["active"] == 1
    assert snap["metrics"] == ["acc", "loss", "val_loss"]

    kinds = {c["key"]: c["kind"] for c in snap["columns"]}
    keys = list(kinds)
    assert keys[:3] == ["state", "name", "created_at"]
    assert keys.index("config.lr") < keys.index("summary.loss")
    assert kinds["state"] == "state" and kinds["created_at"] == "time"
    assert kinds["config.lr"] == "num" and kinds["config.opt"] == "str"

    cells = snap["cells"]
    assert cells["config.lr"] == [0.2, 0.1]
    assert cells["config.model.depth"] == [None, 4.0]
    assert cells["config.opt"] == [None, "adam"]
    assert cells["summary.tag"] == [None, "x"]

    curves = snap["curves"]
    assert curves[("old", "loss")] == (list(range(10)), [10.0 - s for s in range(10)])
    assert curves[("new", "val_loss")] == ([0, 3, 6], [0.25, 3.25, 6.25])  # regular, stride 3
    assert curves[("new", "acc")][0] == [0, 1, 5]  # irregular: x sent explicitly
    assert curves[("new", "acc")][1] == pytest.approx([0.0, 0.1, 0.5])
    assert ("old", "acc") not in curves


def test_snapshot_downsampling_sorting_and_paging(api):
    for i in range(5):
        api.create_run("p", f"r{i}", config={"order": (i * 3) % 5})
        api.stream("p", f"r{i}", history_rows(1000, loss=lambda s, i=i: s + i))
    snap = api.workspace(sort="config.order", offset=1, limit=2, max_points=50)
    assert snap["total"] == 5
    assert [r["id"] for r in snap["runs"]] == ["r2", "r4"]  # order 1, 2
    x, y = snap["curves"][("r2", "loss")]
    assert len(x) == 50 and x[0] == 0 and x[-1] == 999
    assert y == [s + 2 for s in x]  # sampled points are real points


def test_snapshot_filters_runs(api):
    for i in range(4):
        api.create_run("p", f"r{i}", config={"lr": i / 10, "opt": "adam" if i % 2 else "sgd"})
        api.stream("p", f"r{i}", history_rows(3, loss=float))
        if i < 3:
            api.stream("p", f"r{i}", complete=0)
    snap = api.workspace(filter='{"config.opt": "adam"}', sort="config.lr")
    assert snap["total"] == 2 and snap["runs_total"] == 4
    assert [r["id"] for r in snap["runs"]] == ["r1", "r3"]
    assert snap["active"] == 1  # the project's live runs, filtered or not
    assert snap["cells"]["config.lr"] == [0.1, 0.3]
    snap = api.workspace(filter='{"config.lr": {"$lt": 0.15}}', running="true")
    assert snap["total"] == 0 and snap["runs"] == []
    assert [r["id"] for r in api.workspace(running="true")["runs"]] == ["r3"]
    bad = api.get("/api/v1/projects/flock/p/workspace", filter='{"config.lr": {"$bogus": 1}}')
    assert bad.status == 400
    assert api.get("/api/v1/projects/flock/p/workspace", filter="{").status == 400


def test_snapshot_point_budget(api, monkeypatch):
    monkeypatch.setattr(Snapshot, "POINT_BUDGET", 200)
    api.create_run("p", "r")
    api.stream("p", "r", history_rows(1000, a=float, b=float))
    snap = api.workspace()
    assert snap["points"] == 100
    assert len(snap["curves"][("r", "a")][0]) == 100


def test_unknown_project_and_metric_keys_survive_restart(make_flock):
    server = make_flock()
    api = server.client()
    assert api.get("/api/v1/projects/flock/nope/workspace").status == 404
    api.create_run("p", "done")
    api.stream("p", "done", history_rows(5, loss=float, **{"eval/acc": float}))
    api.stream("p", "done", complete=0)
    server.call(server.store.flush())  # compaction: keys now live in history_columns only
    api.close()
    server.stop()

    restarted = make_flock(reuse=server)
    api = restarted.client()
    snap = api.workspace()
    assert snap["metrics"] == ["eval/acc", "loss"]
    assert snap["curves"][("done", "eval/acc")] == ([0, 1, 2, 3, 4], [0, 1, 2, 3, 4])
    api.close()


# ---------------------------------------------------------------------- live stream


def test_stream_replays_missed_points_then_streams_new_ones(api, events):
    api.create_run("p", "r1")
    api.stream("p", "r1", history_rows(3, loss=float))
    lines = api.workspace()["runs"][0]["lines"]
    assert lines == 3
    api.stream(
        "p", "r1", history_rows(2, start=3, loss=float), offset=3
    )  # logged after the snapshot

    live = events("p", f"r1:{lines}")
    hello = live.next_of("hello")
    assert hello["runs"] == {"r1": {"state": "running", "lines": 5}}
    replay = live.next_of("history")
    assert replay["run"] == "r1" and replay["lines"] == 5
    assert replay["series"]["loss"] == {"rows": [3, 4], "x": [3, 4], "y": [3.0, 4.0]}
    assert "_step" not in replay["series"]

    api.stream("p", "r1", history_rows(1, start=5, loss=float, acc=lambda s: 0.5), offset=5)
    new = live.next_of("history")
    assert new["lines"] == 6
    assert new["series"] == {
        "loss": {"rows": [5], "x": [5], "y": [5.0]},
        "acc": {"rows": [5], "x": [5], "y": [0.5]},
    }


def test_stream_reports_summary_state_and_new_runs(api, events):
    api.create_run("p", "r1")
    api.create_run("p", "other")
    live = events("p", "r1")
    live.next_of("hello")

    api.stream("p", "other", history_rows(2, loss=float))  # not watched: no history event
    api.stream("p", "r1", summary={"loss": 0.5, "best": {"acc": 0.9}})
    update = live.next_of("run")
    assert update["run"] == "r1" and update["state"] == "running"
    assert update["summary"] == {"summary.loss": 0.5, "summary.best.acc": 0.9}

    api.stream("p", "r1", summary={"loss": 0.4, "best": {"acc": 0.9}})
    assert live.next_of("run")["summary"] == {"summary.loss": 0.4}

    api.stream("p", "r1", complete=1)
    done = live.next_of("run")
    assert done["state"] == "failed"

    api.create_run("p", "fresh")
    created = live.next_of("created")
    assert created["run"] == "fresh" and created["state"] == "running"


def test_crashed_runs_are_announced(make_flock, events):
    server = make_flock(crash_timeout=0.3)
    api = server.client()
    api.create_run("p", "r1")
    live = events("p", "r1", client=api)
    live.next_of("hello")
    assert live.next_of("run")["state"] == "crashed"
    live.close()
    api.close()


def test_slow_subscribers_are_disconnected(api, flock, monkeypatch):
    monkeypatch.setattr(Subscriber, "QUEUE_SIZE", 2)
    api.create_run("p", "r1")

    async def subscribe():
        return flock.store.live.subscribe(flock.store.project("flock", "p").pk, ["r1"])

    sub = flock.call(subscribe())
    for i in range(5):
        api.stream("p", "r1", history_rows(1, start=i, loss=float), offset=i)
    assert sub.closed
    items = []
    while not sub.queue.empty():
        items.append(sub.queue.get_nowait())
    assert items[-1] is None  # end-of-stream marker made it into the full queue

    async def unsubscribe():
        flock.store.live.unsubscribe(flock.store.project("flock", "p").pk, sub)

    flock.call(unsubscribe())


def test_wandb_sdk_points_reach_a_dashboard_stream(flock, api, wandb_client, events, wait_for):
    """End to end: a run logging through the real SDK, watched from a snapshot's cursor."""
    proc = wandb_client.popen(
        """
        import sys
        run = wandb.init(project="live", settings=SETTINGS)
        for step in range(10):
            run.log({"loss": 1.0 / (step + 1)})
        print("LOGGED", run.id, flush=True)
        sys.stdin.readline()
        for step in range(10, 15):
            run.log({"loss": 1.0 / (step + 1), "eval/acc": step / 100})
        run.finish()
        """
    )
    try:
        line = proc.stdout.readline()
        assert line.startswith("LOGGED"), line + proc.stderr.read()
        run_id = line.split()[1]
        snap = wait_for(lambda: (s := api.workspace("live")) and s["runs"][0]["lines"] >= 10 and s)
        lines = snap["runs"][0]["lines"]
        live = events("live", f"{run_id}:{lines}")
        assert live.next_of("hello")["runs"][run_id]["state"] == "running"

        proc.stdin.write("\n")
        proc.stdin.flush()
        steps: list[int] = []
        while len(steps) < 5:
            data = live.next_of("history")
            assert data["run"] == run_id
            steps += data["series"]["loss"]["x"]
        assert steps == list(range(10, 15))
        assert live.next_of("run")  # summary / final state follow
        assert proc.wait(timeout=120) == 0, proc.stderr.read()
    finally:
        if proc.poll() is None:
            proc.kill()
    assert api.workspace("live")["runs"][0]["state"] == "finished"
