import contextlib
import json
import os
import signal
import subprocess

import pytest

pytest.importorskip("wandb")


def test_login_cli_stores_credentials_for_the_server(tracker, wandb_client, api_key):
    proc = wandb_client.cli("login", "--host", tracker.url, api_key, WANDB_SILENT="false")
    combined = proc.stdout + proc.stderr
    assert "flock" in combined  # "Currently logged in as: flock"
    netrc = (wandb_client.workdir / "home" / ".netrc").read_text()
    assert f"127.0.0.1:{tracker.port}" in netrc
    assert api_key in netrc


def test_login_cli_rejects_unknown_key(make_tracker, make_wandb_client, api_key):
    server = make_tracker(api_keys={api_key: "alice"})
    client = make_wandb_client(server)
    proc = client.cli("login", "--host", server.url, "z" * 40, check=False)
    assert proc.returncode != 0
    ok = client.cli("login", "--relogin", "--host", server.url, api_key, WANDB_SILENT="false")
    assert "alice" in ok.stdout + ok.stderr


def test_init_log_summary_and_config(tracker, api, wandb_client):
    wandb_client.run(
        """
        import math
        run = wandb.init(
            project="core",
            name="baseline-run",
            config={"lr": 0.001, "optimizer": "adam", "model": {"layers": 12, "act": "gelu"}},
            tags=["baseline", "v1"],
            notes="first try",
            group="sweep-a",
            job_type="train",
            settings=SETTINGS,
        )
        run.define_metric("val_loss", summary="min")
        print("hello from training")
        val_losses = {0: 1.5, 100: 1.2, 200: 1.4, 300: 1.1, 400: 1.3}
        for step in range(0, 500, 10):
            weird = float("nan") if step == 10 else float("inf") if step == 20 else step
            run.log(
                {
                    "loss": 1.0 / (step + 1),
                    "train": {"acc": step / 1000},
                    "flag": step % 20 == 0,
                    "phase": "warmup" if step < 100 else "main",
                    "weird": weird,
                },
                step=step,
            )
            if step in val_losses:
                run.log({"val_loss": val_losses[step], "epoch": step // 100}, step=step)
            if step == 250:
                run.log({"hist": wandb.Histogram([1, 2, 2, 3, 3, 3])}, step=step)
        run.summary["best_acc"] = 0.93
        run.summary.update({"eval": {"bleu": 31.5}})
        run.config.update({"extra": "x"})
        run.tags = run.tags + ("added",)
        run.finish()
        print("RUN_ID", run.id)
        """
    )
    row = api.only_run("core")
    run_id = row["name"]
    assert row["display_name"] == "baseline-run"
    assert row["state"] == "finished"
    assert row["exitcode"] == 0
    assert set(row["tags"]) == {"baseline", "v1", "added"}
    assert row["notes"] == "first try"
    assert row["group"] == "sweep-a"
    assert row["job_type"] == "train"
    assert row["config.lr"] == 0.001
    assert row["config.optimizer"] == "adam"
    assert row["config.model.layers"] == 12
    assert row["config.model.act"] == "gelu"
    assert row["config.extra"] == "x"
    assert row["summary.loss"] == pytest.approx(1 / 491)
    assert row["summary.train.acc"] == pytest.approx(0.49)
    # define_metric(summary="min") stores {"val_loss": {"min": ...}}, flattened like wandb's UI.
    assert row["summary.val_loss.min"] == pytest.approx(1.1)
    assert row["summary.best_acc"] == 0.93
    assert row["summary.eval.bleu"] == 31.5
    assert row["summary._step"] == 490
    assert row["summary.phase"] == "main"

    series = api.run_history("core", run_id)
    steps = list(range(0, 500, 10))
    assert series["loss"]["x"] == steps
    assert series["loss"]["y"] == pytest.approx([1 / (s + 1) for s in steps])
    assert series["train.acc"]["y"] == pytest.approx([s / 1000 for s in steps])
    assert series["flag"]["y"] == [1.0 if s % 20 == 0 else 0.0 for s in steps]
    assert series["val_loss"]["x"] == [0, 100, 200, 300, 400]
    assert series["val_loss"]["y"] == [1.5, 1.2, 1.4, 1.1, 1.3]
    # Non-finite values are kept in the curve as JSON nulls (gaps).
    assert series["weird"]["y"][:4] == [0.0, None, None, 30.0]
    assert "phase" not in series and "hist" not in series  # non-numeric values

    by_epoch = api.run_history("core", run_id, keys="val_loss", x="epoch")
    assert by_epoch["val_loss"] == {"x": [0, 1, 2, 3, 4], "y": [1.5, 1.2, 1.4, 1.1, 1.3]}

    detail = api.get(f"/api/v1/projects/flock/core/runs/{run_id}").json()
    assert detail["config"]["model"] == {"layers": 12, "act": "gelu"}
    assert detail["summary"]["eval"] == {"bleu": 31.5}
    assert detail["summary"]["hist"]["_type"] == "histogram"
    assert {"loss", "val_loss", "_step", "train.acc"} <= set(detail["history_keys"])

    logs = api.get(f"/api/v1/projects/flock/core/runs/{run_id}/logs").json()
    assert any("hello from training" in line["text"] for line in logs["lines"])

    files = {
        f["name"]
        for f in api.get(f"/api/v1/projects/flock/core/runs/{run_id}/files").json()["files"]
    }
    assert {"config.yaml", "wandb-metadata.json", "wandb-summary.json"} <= files
    metadata = api.get(f"/api/v1/projects/flock/core/runs/{run_id}/files/wandb-metadata.json")
    assert metadata.status == 200
    assert json.loads(metadata.body)["program"].endswith(".py")

    match = api.runs(
        "core", filter={"config.optimizer": "adam", "summary.val_loss.min": {"$lt": 1.2}}
    )
    assert match["ids"] == [run_id]
    miss = api.runs("core", filter={"tags": "nope"})
    assert miss["total"] == 0


def test_metrics_are_visible_while_the_run_is_still_logging(tracker, api, wandb_client, wait_for):
    proc = wandb_client.popen(
        """
        import sys
        run = wandb.init(project="live", config={"lr": 0.1}, settings=SETTINGS)
        for step in range(20):
            run.log({"loss": 1.0 / (step + 1)})
        run.summary["phase"] = "waiting"
        print("LOGGED", run.id, flush=True)
        sys.stdin.readline()  # block until the test has observed the live run
        for step in range(20, 25):
            run.log({"loss": 1.0 / (step + 1)})
        run.finish()
        """
    )
    try:
        line = proc.stdout.readline()
        assert line.startswith("LOGGED"), line + proc.stderr.read()
        run_id = line.split()[1]

        def live_curve():
            data = api.get("/api/v1/projects/flock/live/history", keys="loss", running="1").json()
            runs = data["runs"]
            if runs and len(runs[0]["series"].get("loss", {}).get("x", [])) == 20:
                return runs[0]
            return None

        live = wait_for(live_curve)
        assert live["id"] == run_id
        assert live["state"] == "running"
        assert live["series"]["loss"]["x"] == list(range(20))
        row = wait_for(lambda: (r := api.only_run("live")).get("summary.phase") == "waiting" and r)
        assert row["state"] == "running"
        assert row["summary.loss"] == pytest.approx(1 / 20)

        proc.stdin.write("\n")
        proc.stdin.flush()
        assert proc.wait(timeout=120) == 0, proc.stderr.read()
    finally:
        if proc.poll() is None:
            proc.kill()
    row = api.only_run("live")
    assert row["state"] == "finished"
    assert api.run_history("live", run_id, keys="loss")["loss"]["x"] == list(range(25))
    running = api.get("/api/v1/projects/flock/live/history", keys="loss", running="1").json()
    assert running["runs"] == []


def test_failed_runs_record_exit_code(tracker, api, wandb_client):
    proc = wandb_client.run(
        """
        run = wandb.init(project="failures", name="crashy", settings=SETTINGS)
        run.log({"loss": 1.0})
        raise RuntimeError("boom")
        """,
        check=False,
    )
    assert proc.returncode != 0
    wandb_client.run(
        """
        run = wandb.init(project="failures", name="exit-two", settings=SETTINGS)
        run.log({"loss": 1.0})
        run.finish(exit_code=2)
        """
    )
    data = api.runs("failures", columns="display_name,state,exitcode")
    rows = dict(
        zip(data["data"]["display_name"], zip(data["data"]["state"], data["data"]["exitcode"]))
    )
    assert rows == {"crashy": ("failed", 1), "exit-two": ("failed", 2)}


def test_resume_continues_history_and_summary(tracker, api, wandb_client):
    script = """
        import sys
        run = wandb.init(project="resume", id="resumable1", resume=sys.argv[1], settings=SETTINGS)
        base = 0 if sys.argv[2] == "first" else 5
        for i in range(5):
            run.log({"loss": 10.0 - (base + i), "only_" + sys.argv[2]: i})
        if sys.argv[2] == "second":
            assert run.summary["only_first"] == 4, dict(run.summary)
            assert run.config["lr"] == 0.5
        else:
            run.config["lr"] = 0.5
        run.finish()
    """
    wandb_client.run(script, "allow", "first")
    wandb_client.run(script, "must", "second")

    row = api.only_run("resume")
    assert row["name"] == "resumable1"
    assert row["state"] == "finished"
    assert row["summary._step"] == 9
    assert row["summary.only_first"] == 4  # kept from the first session
    assert row["summary.only_second"] == 4
    assert row["config.lr"] == 0.5
    series = api.run_history("resume", "resumable1")
    assert series["loss"]["x"] == list(range(10))
    assert series["loss"]["y"] == [10.0 - i for i in range(10)]
    assert series["only_first"]["x"] == [0, 1, 2, 3, 4]
    assert series["only_second"]["x"] == [5, 6, 7, 8, 9]


def test_resume_must_fails_for_unknown_run(tracker, wandb_client):
    proc = wandb_client.run(
        """
        wandb.init(project="resume", id="doesnotexist", resume="must", settings=SETTINGS)
        """,
        check=False,
    )
    assert proc.returncode != 0


def test_data_survives_server_restart(make_tracker, make_wandb_client):
    first = make_tracker()
    make_wandb_client(first).run(
        """
        run = wandb.init(project="persist", name="durable", config={"bs": 64}, settings=SETTINGS)
        for i in range(30):
            run.log({"loss": 1.0 / (i + 1)})
        run.summary["note"] = "kept"
        run.finish()
        """
    )
    first.stop()

    second = make_tracker(reuse=first)
    api = second.client()
    row = api.only_run("persist")
    assert row["display_name"] == "durable"
    assert row["config.bs"] == 64
    assert row["summary.note"] == "kept"
    assert row["state"] == "finished"
    curves = api.get("/api/v1/projects/flock/persist/history", keys="loss").json()["runs"]
    assert curves[0]["series"]["loss"]["y"] == pytest.approx([1 / (i + 1) for i in range(30)])

    # And the restarted server still accepts new runs into the same project.
    make_wandb_client(second).run(
        """
        run = wandb.init(project="persist", name="after-restart", settings=SETTINGS)
        run.log({"loss": 0.5})
        run.finish()
        """
    )
    assert api.runs("persist")["total"] == 2


def test_offline_run_synced_with_cli(tracker, api, wandb_client):
    wandb_client.run(
        """
        run = wandb.init(project="offline", name="synced-later", config={"seed": 7})
        for i in range(10):
            run.log({"loss": float(10 - i)})
        run.finish()
        """,
        WANDB_MODE="offline",
    )
    assert api.get("/api/v1/projects/flock/offline/runs").status == 404  # nothing synced yet
    (run_dir,) = (wandb_client.workdir / "wandb").glob("offline-run-*")
    wandb_client.cli("sync", str(run_dir))

    row = api.only_run("offline")
    assert row["display_name"] == "synced-later"
    assert row["config.seed"] == 7
    assert row["summary.loss"] == 1.0
    assert row["state"] == "finished"
    series = api.run_history("offline", row["name"], keys="loss")
    assert series["loss"]["y"] == [float(10 - i) for i in range(10)]


def test_concurrent_runs(tracker, api, wandb_client):
    procs = [
        wandb_client.popen(
            """
            import sys
            worker = int(sys.argv[1])
            run = wandb.init(project="parallel", group="ddp", config={"worker": worker}, settings=SETTINGS)
            for step in range(100):
                run.log({"loss": worker + step / 100})
            run.finish()
            """,
            str(worker),
        )
        for worker in range(4)
    ]
    for proc in procs:
        assert proc.wait(timeout=180) == 0, proc.stderr.read()

    data = api.runs("parallel", columns="config.worker,state,group", sort="config.worker")
    assert data["data"]["config.worker"] == [0, 1, 2, 3]
    assert set(data["data"]["state"]) == {"finished"}
    curves = api.get("/api/v1/projects/flock/parallel/history", keys="loss").json()["runs"]
    assert len(curves) == 4
    for curve in curves:
        assert curve["series"]["loss"]["x"] == list(range(100))


def test_api_keys_map_to_entities(make_tracker, make_wandb_client, api_key):
    server = make_tracker(api_keys={api_key: "alice"})
    make_wandb_client(server).run(
        """
        run = wandb.init(project="secured", settings=SETTINGS)
        assert run.entity == "alice", run.entity
        run.log({"loss": 1.0})
        run.finish()
        """
    )
    api = server.client()
    assert api.only_run("secured", entity="alice")["state"] == "finished"
    assert server.client(api_key="z" * 40).get("/api/v1/projects").status == 401

    proc = make_wandb_client(server, api_key="z" * 40).run(
        """
        wandb.init(project="secured", settings=SETTINGS)
        """,
        check=False,
        timeout=120,
    )
    assert proc.returncode != 0


def test_killed_client_is_marked_crashed(make_tracker, make_wandb_client, wait_for):
    server = make_tracker(crash_timeout=3.0)
    client = make_wandb_client(server)
    proc = client.popen(
        """
        import time
        run = wandb.init(project="crash", settings=SETTINGS)
        run.log({"loss": 1.0})
        print("STARTED", flush=True)
        time.sleep(600)
        """
    )
    try:
        assert proc.stdout.readline().startswith("STARTED")
        api = server.client()
        wait_for(lambda: api.only_run("crash")["state"] == "running")
        # SIGKILL the script, its wandb-core service and wandb-core's system monitor so nothing
        # can report an exit code; the server must notice the missing heartbeats on its own.
        pids = [proc.pid]
        for parent in pids:  # walk the process tree breadth first
            children = subprocess.run(
                ["pgrep", "-P", str(parent)], check=False, capture_output=True, text=True
            ).stdout.split()
            pids.extend(int(child) for child in children)
        for pid in pids:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        proc.wait(timeout=30)
        row = wait_for(lambda: (r := api.only_run("crash"))["state"] == "crashed" and r, timeout=60)
        assert row.get("exitcode") is None
    finally:
        if proc.poll() is None:
            proc.kill()


def test_force_deleted_run_stays_deleted_while_its_client_finishes(tracker, api, wandb_client):
    """A run deleted under a live client: the client finishes normally and the run doesn't
    come back from the client's later requests."""
    proc = wandb_client.popen(
        """
        import sys
        run = wandb.init(project="doomed", settings=SETTINGS)
        run.log({"loss": 1.0})
        print("LOGGED", run.id, flush=True)
        sys.stdin.readline()  # block until the test has deleted the run
        for step in range(1, 20):
            run.log({"loss": 1.0 / (step + 1)})
        run.summary["after"] = True
        run.finish()
        print("FINISHED", flush=True)
        """
    )
    try:
        line = proc.stdout.readline()
        assert line.startswith("LOGGED"), line + proc.stderr.read()
        run_id = line.split()[1]
        path = f"/api/v1/projects/flock/doomed/runs/{run_id}"
        refused = api.delete(path)
        assert refused.status == 409, refused.body
        assert refused.json()["ids"] == [run_id]
        assert api.delete(path, force="true").status == 204
        proc.stdin.write("\n")
        proc.stdin.flush()
        assert proc.wait(timeout=120) == 0, proc.stderr.read()
        assert "FINISHED" in proc.stdout.read()
    finally:
        if proc.poll() is None:
            proc.kill()
    assert api.get(path).status == 404
    assert api.get("/api/v1/projects/flock/doomed").json()["run_count"] == 0


def test_moved_run_is_not_resumed_at_its_old_path(tracker, api, wandb_client):
    script = """
        import sys
        run = wandb.init(project="before", id="mover", resume="allow", settings=SETTINGS)
        run.log({"loss": float(sys.argv[1])})
        run.finish()
    """
    wandb_client.run(script, "1")
    moved = api.post("/api/v1/projects/flock/before/runs/move", {"runs": ["mover"], "to": "after"})
    assert moved.status == 404  # "after" doesn't exist yet
    body = {"runs": ["mover"], "to": "after", "create": True}
    assert api.post("/api/v1/projects/flock/before/runs/move", body).json()["ids"] == ["mover"]
    wandb_client.run(script, "2")  # resume="allow" at the old path starts a new run there

    assert api.run_history("after", "mover", keys="loss")["loss"]["y"] == [1.0]
    assert api.run_history("before", "mover", keys="loss")["loss"]["y"] == [2.0]


def test_offline_run_synced_into_a_deleted_project(tracker, api, wandb_client):
    wandb_client.run(
        """
        run = wandb.init(project="reborn", name="first")
        run.log({"loss": 1.0})
        run.finish()
        """,
    )
    assert api.delete("/api/v1/projects/flock/reborn").status == 204
    wandb_client.run(
        """
        run = wandb.init(project="reborn", name="offline")
        run.log({"loss": 2.0})
        run.finish()
        """,
        WANDB_MODE="offline",
    )
    (run_dir,) = (wandb_client.workdir / "wandb").glob("offline-run-*")
    wandb_client.cli("sync", str(run_dir))
    row = api.only_run("reborn")
    assert (row["display_name"], row["summary.loss"]) == ("offline", 2.0)
