import asyncio

from app.api.scripts.seed_demo import ADMIN_EMAIL, PASSWORD, simulate


def test_a_simulated_month_loads_into_the_flock(postgres, make_flock, tmp_path):
    """A small simulated month: Flock loads it as written, with nothing to rebuild."""
    url = postgres.create()
    files = tmp_path / "flock-files-0"  # where the first flock of ``make_flock`` looks
    stats = asyncio.run(simulate(url, scale=0.03, seed=7, files_dir=files, log=lambda _: None))
    assert stats["runs"] > 100 and stats["points"] > 0 and stats["gpu_hours"] > 0

    server = make_flock(database_url=url, api_key=stats["api_key"])
    store = server.store
    assert len(store.runs_by_pk) == stats["runs"]
    stale = [r for r in store.runs_by_pk.values() if r.compacted["history"] < r.history_line_count]
    assert stale == []  # stored compacted, as Flock keeps finished runs
    api = server.client()
    login = server.anonymous().post(
        "/api/v1/auth/login", {"email": ADMIN_EMAIL, "password": PASSWORD}
    )
    assert login.status == 200

    orgs = api.get("/api/v1/orgs").json()["orgs"]
    assert [(o["name"], o["display_name"]) for o in orgs] == [
        ("flock", "Flock Research"),
        ("speech", "Speech Team"),
        ("vision", "Vision Lab"),
    ]
    assert all(o["avatar_url"] for o in orgs)

    feed = api.get("/api/v1/feed", limit=100).json()["items"]
    assert len(feed) == 100
    assert {"run_started", "run_finished"} <= {item["kind"] for item in feed}
    times = [item["at"] for item in feed]
    assert times == sorted(times, reverse=True)

    budget = api.get("/api/v1/orgs/flock/budget").json()
    assert budget["spent"] > 0 and budget["budget"] is not None
    assert budget["top_runs"] and budget["projects"]

    # A finished training run reads back with its curves, GPUs and metadata file.
    project = store.project("flock", "lr-sweep")
    run = next(r for r in project.runs if r.state == "finished" and r.gpu_count)
    path = f"/api/v1/projects/flock/lr-sweep/runs/{run.name}"
    detail = api.get(path).json()
    assert detail["gpu_detected"]["count"] == run.gpu_count_detected
    assert detail["runtime"] > 0 and "loss" in detail["history_keys"]
    curve = api.run_history("lr-sweep", run.name, keys="loss")["loss"]
    assert len(curve["x"]) == run.history_line_count
    system = api.run_history("lr-sweep", run.name, stream="system", keys="system.gpu.0.gpu")
    assert system["system.gpu.0.gpu"]["y"]
    assert api.get(f"{path}/files/wandb-metadata.json").status == 200
