import numpy as np
import pytest

from app.api.src.api.dashboard import Snapshot
from app.api.src.core.live import Subscriber
from app.api.src.core.metric_defs import parse_metric_defs
from tests.app.utils import history_rows, system_samples

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


def test_snapshot_has_and_filters_on_run_owners(flock, api):
    alice = flock.add_user("alice@lab.test")
    me = api.get("/api/v1/me").json()["id"]
    api.create_run("p", "mine")
    alice.api.create_run("p", "hers")
    snap = api.workspace()
    kinds = {c["key"]: c["kind"] for c in snap["columns"]}
    assert kinds["owner"] == "user"
    assert [r["id"] for r in snap["runs"]] == ["hers", "mine"]
    assert snap["cells"]["owner"] == [alice.id, me]
    snap = api.workspace(filter=f'{{"owner": {alice.id}}}')
    assert [r["id"] for r in snap["runs"]] == ["hers"]


def test_snapshot_lists_only_the_metrics_of_its_runs(api):
    # Old runs logged metrics newer ones don't: their pages would chart them empty.
    api.create_run("p", "old", config={"kind": "old"})
    api.stream("p", "old", history_rows(3, loss=float, **{"lr/group0": float, "perf/mfu": float}))
    api.stream("p", "old", complete=0)
    api.create_run("p", "new", config={"kind": "new"})
    api.stream("p", "new", history_rows(3, loss=float, **{"eval/acc": float}))
    api.stream("p", "new", complete=0)
    assert api.workspace()["metrics"] == ["eval/acc", "loss", "lr/group0", "perf/mfu"]
    assert api.workspace(limit=1)["metrics"] == ["eval/acc", "loss"]  # "new" only
    assert api.workspace(offset=1, limit=1)["metrics"] == ["loss", "lr/group0", "perf/mfu"]
    assert api.workspace(filter='{"config.kind": "new"}')["metrics"] == ["eval/acc", "loss"]
    assert api.workspace(filter='{"config.kind": "none"}')["metrics"] == []


def test_snapshot_point_budget(api, monkeypatch):
    monkeypatch.setattr(Snapshot, "POINT_BUDGET", 200)
    api.create_run("p", "r")
    api.stream("p", "r", history_rows(1000, a=float, b=float))
    snap = api.workspace()
    assert snap["points"] == 66  # a, b and the _runtime mapping share the budget
    assert len(snap["curves"][("r", "a")][0]) == 66


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


# ---------------------------------------------------------------------- x axes

# What wandb-core records for Hugging Face's WandbCallback (``define_metric("train/global_step")``,
# ``define_metric("*", step_metric="train/global_step", step_sync=True)``) and for
# ``define_metric("debug", hidden=True)``.
HF_METRICS = [
    {"1": "train/global_step", "6": [3], "7": []},
    {"2": "*", "5": 1, "6": [1], "7": []},
    {"1": "debug", "6": [3, 2], "7": []},
]


def test_metric_definitions_are_parsed_from_the_config():
    config = {"lr": {"value": 0.1}, "_wandb": {"value": {"m": HF_METRICS}}}
    assert parse_metric_defs(config) == [
        {"key": "train/global_step"},
        {"glob": "*", "step": "train/global_step"},
        {"key": "debug", "hidden": True},
    ]
    # Older SDKs name the step metric; broken and self-referencing records are skipped.
    legacy = [
        {"1": "loss", "4": "epoch"},
        {"1": "epoch", "5": 2},
        {"2": "eval/", "5": 1},
        {"1": "acc", "5": 99},
        "junk",
        {"6": [2]},
    ]
    assert parse_metric_defs({"_wandb": {"value": {"m": legacy}}}) == [
        {"key": "loss", "step": "epoch"},
        {"key": "epoch"},
        {"key": "acc"},
    ]
    assert parse_metric_defs({}) == parse_metric_defs({"_wandb": {"value": {"m": 3}}}) == []


def test_snapshot_carries_metric_definitions_and_axis_mappings(api):
    api.create_run("p", "hf", config={"lr": 0.1, "_wandb": {"m": HF_METRICS}})
    rows = []
    for s in range(40):
        # Every 10th line is an evaluation at the same global step as the line before it.
        gs = 16 * (s - s // 10)
        row = {"_step": s, "_runtime": s * 0.5, "_timestamp": 1.79e9 + s * 0.5}
        row |= {"eval/loss": 1 / gs} if s % 10 == 9 else {"train/loss": 1 / (gs + 16)}
        rows.append(row | {"train/global_step": gs + (0 if s % 10 == 9 else 16)})
    api.stream("p", "hf", rows, events=system_samples(3, {"system.cpu": float}))
    api.create_run("p", "plain")
    api.stream("p", "plain", history_rows(5, loss=float))

    snap = api.workspace(system="true", sort="created_at")
    hf, plain = snap["runs"]
    assert hf["metric_defs"] == [
        {"key": "train/global_step"},
        {"glob": "*", "step": "train/global_step"},
        {"key": "debug", "hidden": True},
    ]
    assert plain["metric_defs"] == []
    assert snap["axes"] == ["_runtime", "_timestamp", "train/global_step"]
    assert snap["system_axes"] == ["_timestamp"]

    maps = snap["mappings"]
    # A mapping keeps only the knots interpolation needs: the ends and where the stride
    # changes. Wall times keep full float64 precision.
    steps, values = maps[("hf", "train/global_step")]
    assert len(steps) < 20
    interpolated = np.interp(np.arange(40), steps, values)
    assert interpolated.tolist() == [r["train/global_step"] for r in rows]
    assert maps[("hf", "_timestamp")] == ([0, 39], [1.79e9, 1.79e9 + 19.5])
    assert maps[("hf", "_runtime")] == ([0, 39], [0, 19.5])
    assert maps[("hf", "system:_timestamp")] == ([0.25, 3.25], [1.79e9, 1.79e9 + 3])
    steps, runtimes = maps[("plain", "_runtime")]
    assert steps[0] == 0 and steps[-1] == 4 and runtimes[-1] == pytest.approx(0.4)
    assert ("plain", "train/global_step") not in maps
    # Metrics are still sent against _step.
    assert snap["curves"][("hf", "train/loss")][0][:3] == [0, 1, 2]

    # A page whose runs define no step metrics maps wandb's times only.
    bare = api.workspace("p", filter='{"name": "plain"}')
    assert bare["axes"] == ["_runtime", "_timestamp"] and bare["system_axes"] == []


def test_stream_announces_new_metric_definitions(api, events):
    api.create_run("p", "r1", config={"lr": 0.1})
    live = events("p", "r1:0")
    live.next_of("hello")
    api.create_run("p", "r1", config={"lr": 0.1, "_wandb": {"m": HF_METRICS}})
    update = live.next_of("run")
    assert update["metric_defs"][1] == {"glob": "*", "step": "train/global_step"}
    api.create_run("p", "r1", config={"lr": 0.2, "_wandb": {"m": HF_METRICS}})  # unchanged
    assert "metric_defs" not in live.next_of("run")
    rows = [{"_step": 0, "_runtime": 0.5, "_timestamp": 1.79e9, "loss": 1.0}]
    api.stream("p", "r1", rows)
    series = live.next_of("history")["series"]
    assert series["_timestamp"] == {"rows": [0], "x": [0], "y": [1.79e9]}
    assert "_step" not in series


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
        # Streamed too, so dashboards can plot against time.
        "_runtime": {"rows": [5], "x": [5], "y": [0.5]},
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


def test_slow_subscribers_are_disconnected(api, flock, monkeypatch, wait_for):
    monkeypatch.setattr(Subscriber, "QUEUE_SIZE", 2)
    api.create_run("p", "r1")

    async def subscribe():
        project = await flock.store.project("flock", "p")
        return flock.store.live.subscribe(project.pk, ["r1"])

    sub = flock.call(subscribe())
    for i in range(5):
        api.stream("p", "r1", history_rows(1, start=i, loss=float), offset=i)
    wait_for(lambda: sub.closed)  # events reach the hub through notifications (D79)
    items = []
    while not sub.queue.empty():
        items.append(sub.queue.get_nowait())
    assert items[-1] is None  # end-of-stream marker made it into the full queue

    async def unsubscribe():
        project = await flock.store.project("flock", "p")
        flock.store.live.unsubscribe(project.pk, sub)

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
