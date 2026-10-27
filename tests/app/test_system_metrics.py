import pytest

from tests.app.utils import history_rows, sample_runtimes, system_samples


def test_system_metrics_are_their_own_stream(api):
    api.create_run("p", "r1")
    samples = system_samples(4, {"system.cpu": lambda i: 10.0 * i})
    samples[1]["system.gpu.0.gpu"] = 55.0  # sparse, like keys that appear after warmup
    api.stream("p", "r1", history_rows(3, loss=float), events=samples)

    series = api.run_history("p", "r1", stream="system", keys="system.cpu,system.gpu.0.gpu")
    assert series["system.cpu"] == {"x": sample_runtimes(range(4)), "y": [0.0, 10.0, 20.0, 30.0]}
    assert series["system.gpu.0.gpu"] == {"x": sample_runtimes([1]), "y": [55.0]}
    by_time = api.run_history("p", "r1", stream="system", keys="system.cpu", x="_timestamp")
    assert by_time["system.cpu"]["x"] == [1.79e9 + 1.5 * i for i in range(4)]
    assert api.run_history("p", "r1", keys="loss")["loss"]["x"] == [0, 1, 2]

    detail = api.get("/api/v1/projects/flock/p/runs/r1").json()
    assert "system.cpu" not in detail["history_keys"]
    assert {"system.cpu", "system.gpu.0.gpu"} <= set(detail["system_keys"])
    assert detail["system_line_count"] == 4

    curves = api.get(
        "/api/v1/projects/flock/p/history", keys="system.cpu", stream="system", running="1"
    ).json()
    assert curves["stream"] == "system" and curves["x"] == "_runtime"
    assert curves["runs"][0]["series"]["system.cpu"]["y"] == [0.0, 10.0, 20.0, 30.0]
    assert api.get("/api/v1/projects/flock/p/history", keys="x", stream="gpu").status == 422


def test_resent_system_samples_are_not_duplicated(api):
    api.create_run("p", "r1")
    samples = system_samples(6, {"system.cpu": float})
    api.stream("p", "r1", events=samples[:4])
    api.stream("p", "r1", events=samples[2:], events_offset=2)  # overlapping retry
    series = api.run_history("p", "r1", stream="system", keys="system.cpu")
    assert series["system.cpu"]["y"] == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]


def test_system_metrics_compact_and_survive_restart(make_flock):
    server = make_flock()
    api = server.client()
    api.create_run("p", "r1")
    samples = system_samples(8, {"system.cpu": float})
    api.stream("p", "r1", history_rows(5, loss=float), events=samples, complete=0)
    server.call(server.store.flush())
    chunks = server.call(
        server.store.db.fetch("SELECT stream, key, first_line, n FROM series_chunks")
    )
    assert ("system", "system.cpu", 0, 8) in {tuple(record) for record in chunks}

    expected = [float(i) for i in range(8)]
    cold = api.run_history("p", "r1", stream="system", keys="system.cpu")
    assert cold["system.cpu"] == {"x": sample_runtimes(range(8)), "y": expected}

    server.stop()
    restarted = make_flock(reuse=server).client()
    after = restarted.run_history("p", "r1", stream="system", keys="system.cpu")
    assert after["system.cpu"]["y"] == expected
    assert restarted.workspace(system="true")["system_metrics"] == ["system.cpu"]


def test_snapshot_includes_system_curves_on_request(api):
    api.create_run("p", "r1")
    samples = system_samples(4, {"system.cpu": lambda i: 10.0 * i})
    api.stream("p", "r1", history_rows(3, loss=float), events=samples)

    plain = api.workspace()
    assert plain["system_metrics"] == []
    assert ("r1", "system.cpu") not in plain["curves"]
    assert plain["runs"][0]["system_lines"] == 4

    full = api.workspace(system="true")
    assert full["system_metrics"] == ["system.cpu"]
    assert full["curves"][("r1", "system.cpu")] == (
        sample_runtimes(range(4)),
        [0.0, 10.0, 20.0, 30.0],
    )
    assert full["curves"][("r1", "loss")] == plain["curves"][("r1", "loss")]


def test_stream_replays_then_pushes_system_samples(api, events):
    api.create_run("p", "r1")
    samples = system_samples(5, {"system.cpu": float})
    api.stream("p", "r1", events=samples[:3])
    run = api.workspace(system="true")["runs"][0]
    assert (run["lines"], run["system_lines"]) == (0, 3)
    api.stream("p", "r1", events=samples[3:4], events_offset=3)  # logged after the snapshot

    live = events("p", f"r1:{run['lines']}:{run['system_lines']}", system=True)
    assert live.next_of("hello")["runs"]["r1"]["system_lines"] == 4
    replay = live.next_of("system")
    assert replay["lines"] == 4
    assert replay["series"]["system.cpu"] == {"rows": [3], "x": sample_runtimes([3]), "y": [3.0]}
    assert "_wandb" not in replay["series"] and "_runtime" not in replay["series"]

    api.stream("p", "r1", history_rows(1, loss=float), events=samples[4:], events_offset=4)
    assert live.next_of("history")["series"]["loss"]["y"] == [0.0]
    pushed = live.next_of("system")
    assert pushed["series"]["system.cpu"] == {"rows": [4], "x": sample_runtimes([4]), "y": [4.0]}


def test_streams_only_carry_system_samples_when_asked(api, events):
    api.create_run("p", "r1")
    live = events("p", "r1")
    live.next_of("hello")
    api.stream("p", "r1", history_rows(1, loss=float), events=system_samples(1, {"x": float}))
    api.stream("p", "r1", history_rows(1, start=1, loss=float), offset=1)
    seen = []
    while len([e for e in seen if e == "history"]) < 2:
        seen.append(live.next()[0])
    assert "system" not in seen


def test_wandb_sdk_system_metrics(flock, api, wandb_client):
    """The SDK's system monitor samples reach the system stream."""
    wandb_client.run(
        """
        import time
        settings = wandb.Settings(
            x_file_stream_transmit_interval=0.1, x_stats_sampling_interval=0.5
        )
        run = wandb.init(project="sysmon", settings=settings)
        run.log({"loss": 1.0})
        time.sleep(3)
        run.finish()
        """
    )
    run_id = api.only_run("sysmon")["name"]
    detail = api.get(f"/api/v1/projects/flock/sysmon/runs/{run_id}").json()
    assert detail["system_line_count"] >= 2
    assert any(key.startswith("system.") for key in detail["system_keys"])
    series = api.run_history("sysmon", run_id, stream="system")
    densest = max(
        (s for key, s in series.items() if key.startswith("system.")), key=lambda s: len(s["x"])
    )
    assert len(densest["x"]) >= 2
    assert densest["x"] == sorted(densest["x"])  # x is the run's clock (_runtime)
    assert not any(key.startswith("system.") for key in detail["history_keys"])


@pytest.mark.parametrize("stream", ["history", "system"])
def test_run_history_defaults_x_by_stream(api, stream):
    api.create_run("p", "r1")
    api.stream("p", "r1", history_rows(2, loss=float), events=system_samples(2, {"cpu": float}))
    resp = api.get("/api/v1/projects/flock/p/runs/r1/history", stream=stream).json()
    assert resp["stream"] == stream
    assert resp["x"] == ("_step" if stream == "history" else "_runtime")
