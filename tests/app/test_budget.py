import json
import time

import pytest

H200x8 = json.dumps({"gpu": "NVIDIA H200", "gpu_count": 8, "cudaVersion": "12.6"}).encode()
BILLING = "/api/v1/orgs/flock/billing"
BUDGET = "/api/v1/orgs/flock/budget"


def run_for(api, project: str, name: str, seconds: float, metadata: bytes | None = H200x8):
    """A run that sends heartbeats for about ``seconds`` and then finishes."""
    api.create_run(project, name)
    if metadata is not None:
        api.upload(project, name, "wandb-metadata.json", metadata)
    api.stream(project, name, [])
    time.sleep(seconds)
    api.stream(project, name, [], complete=0)


def detail(api, project: str, name: str) -> dict:
    resp = api.get(f"/api/v1/projects/flock/{project}/runs/{name}")
    assert resp.status == 200, resp.body
    return resp.json()


def test_gpus_are_detected_and_can_be_overridden(flock, api):
    api.create_run("p", "r1")
    api.create_run("p", "r2")
    assert detail(api, "p", "r1")["gpu_count"] is None  # nothing known yet
    api.upload("p", "r1", "wandb-metadata.json", H200x8)
    api.upload("p", "r2", "wandb-metadata.json", json.dumps({"cpu_count": 4}).encode())
    run = detail(api, "p", "r1")
    assert (run["gpu_type"], run["gpu_count"]) == ("H200", 8)
    assert run["gpu_detected"] == {"type": "H200", "count": 8}
    assert detail(api, "p", "r2")["gpu_count"] == 0  # no GPU
    assert api.runs("p", filter={"gpu_type": "H200"})["ids"] == ["r1"]

    path = "/api/v1/projects/flock/p/runs/r1"
    assert api.patch(path, {"gpu_count": -1}).status == 422
    patched = api.patch(path, {"gpu_type": " B200 ", "gpu_count": 4}).json()
    assert (patched["gpu_type"], patched["gpu_count"]) == ("B200", 4)
    assert patched["gpu_detected"] == {"type": "H200", "count": 8}
    # A new upload changes what was detected, not the override.
    api.upload("p", "r1", "wandb-metadata.json", json.dumps({"gpu": "NVIDIA A100"}).encode())
    assert detail(api, "p", "r1")["gpu_type"] == "B200"
    cleared = api.patch(path, {"gpu_type": None, "gpu_count": None}).json()
    assert (cleared["gpu_type"], cleared["gpu_count"]) == ("A100", 1)

    bulk = {"all": True, "set": {"gpu_type": "H100"}}
    assert api.post("/api/v1/projects/flock/p/runs/update", bulk).json()["count"] == 2
    data = api.runs("p", columns=["gpu_type", "gpu_count"], sort=["name"])["data"]
    assert data == {"gpu_type": ["H100", "H100"], "gpu_count": [1, 0]}


def test_spend_accrues_from_heartbeats_and_is_priced(flock, api):
    alice = flock.add_user("alice@lab.test")
    run_for(alice.api, "p", "r1", 1.0)
    run = detail(api, "p", "r1")
    assert 1.0 <= run["runtime"] < 3.0
    assert run["gpu_hours"] == pytest.approx(run["runtime"] * 8 / 3600, rel=1e-3)
    assert run["cost"] == 0  # no price yet

    budget = api.get(BUDGET).json()
    assert (budget["budget"], budget["remaining"], budget["spent"]) == (None, None, 0)
    assert budget["unpriced"] == [{"gpu_type": "H200", "gpu_hours": round(run["gpu_hours"], 3)}]
    assert budget["currency"] == "USD"

    settings = {"monthly_budget": 100, "currency": "EUR", "rates": {"h200": 3600, "A100": 2}}
    assert alice.api.patch(BILLING, settings).status == 403  # admins only
    assert api.patch(BILLING, {"currency": "eur"}).status == 422
    assert api.patch(BILLING, {"rates": {"H200": -1}}).status == 422
    assert api.patch(BILLING, {"currency": None}).status == 422
    billing = api.patch(BILLING, settings).json()
    assert billing["monthly_budget"] == 100
    assert billing["rates"] == [
        {"gpu_type": "A100", "hourly_cost": 2},
        {"gpu_type": "h200", "hourly_cost": 3600},
    ]
    assert billing["gpu_types"][0] | {"gpu_hours": 0} == {
        "gpu_type": "H200",
        "gpu_hours": 0,
        "runs": 1,
        "hourly_cost": 3600,
    }
    assert alice.api.get(BILLING).json() == billing  # members read it

    budget = alice.api.get(BUDGET).json()
    spent = round(run["runtime"] * 8, 2)  # 8 GPUs at 3600 an hour: 8 per second
    assert budget["spent"] == pytest.approx(spent, abs=0.02)
    assert budget["remaining"] == pytest.approx(100 - budget["spent"], abs=0.01)
    assert (budget["currency"], budget["budget"], budget["unpriced"]) == ("EUR", 100, [])
    assert budget["projects"] == [
        {"project": "p", "cost": budget["spent"], "gpu_hours": budget["gpu_hours"], "runs": 1}
    ]
    (top,) = budget["top_runs"]
    assert (top["id"], top["owner"]["email"], top["gpu_count"]) == ("r1", alice.email, 8)
    assert detail(api, "p", "r1")["cost"] == pytest.approx(spent, abs=0.02)

    assert api.patch(BILLING, {"rates": {"H200": None}}).json()["rates"] == [
        {"gpu_type": "A100", "hourly_cost": 2}
    ]
    assert api.get(BUDGET).json()["spent"] == 0


def test_silences_longer_than_the_crash_timeout_are_not_billed(make_flock):
    api = make_flock(crash_timeout=0.5).client()
    api.create_run("p", "r1")
    api.stream("p", "r1", [])
    time.sleep(1.0)  # e.g. preempted, then resumed
    api.stream("p", "r1", [])
    assert detail(api, "p", "r1")["runtime"] < 0.5


def test_runs_without_gpu_info_are_reported(api):
    run_for(api, "p", "r1", 0.2, metadata=None)
    budget = api.get(BUDGET).json()
    assert budget["unknown_gpu"] == {"runs": 1, "hours": 0.0}  # a fraction of a second
    assert budget["top_runs"] == []  # no GPU hours, nothing to rank


def test_top_runs_and_the_project_breakdown(api):
    api.patch(BILLING, {"rates": {"H200": 60}})
    for i in range(6):
        run_for(api, "p" if i % 2 else "q", f"r{i}", 0.05 * (i + 1))
    budget = api.get(BUDGET).json()
    assert [run["id"] for run in budget["top_runs"]] == ["r5", "r4", "r3", "r2", "r1"]
    assert [p["project"] for p in budget["projects"]] == ["p", "q"]  # p ran longer
    assert [p["runs"] for p in budget["projects"]] == [3, 3]
    assert sum(p["cost"] for p in budget["projects"]) == pytest.approx(budget["spent"], abs=0.02)


def test_spend_survives_moves_deletes_and_restarts(make_flock):
    server = make_flock()
    api = server.client()
    api.patch(BILLING, {"rates": {"H200": 3600}, "monthly_budget": 1000})
    run_for(api, "p", "r1", 0.3)
    run_for(api, "q", "r2", 0.2)
    before = api.get(BUDGET).json()

    moved = {"runs": ["r1"], "to": "p2", "create": True}
    assert api.post("/api/v1/projects/flock/p/runs/move", moved).status == 200
    after_move = api.get(BUDGET).json()
    assert after_move["spent"] == before["spent"]
    assert {p["project"] for p in after_move["projects"]} == {"p2", "q"}

    assert api.delete("/api/v1/projects/flock/p2/runs/r1").status == 204
    assert api.delete("/api/v1/projects/flock/q", force="true").status == 204
    after_delete = api.get(BUDGET).json()
    assert after_delete["spent"] == before["spent"]  # spend doesn't come back
    assert [p["project"] for p in after_delete["projects"]] == ["p2", None]
    assert after_delete["top_runs"] == []  # neither run exists any more
    assert api.get(BILLING).json()["gpu_types"][0]["runs"] == 2

    server.stop()
    restarted = make_flock(reuse=server).client()
    assert restarted.get(BUDGET).json() == after_delete


def test_budgets_are_monthly(flock, api):
    alice = flock.add_user("alice@lab.test")
    assert api.patch(BILLING, {"monthly_budget": 500}).status == 200
    this_month = api.get(BUDGET).json()["month"]
    assert api.get(BUDGET, month="2000-01").json()["budget"] is None  # before it was set
    assert api.get(BUDGET, month="2999-12").json()["budget"] == 500
    assert api.get(BUDGET, month="2026-13").status == 422
    budgets = api.patch(BILLING, {"monthly_budget": None}).json()["budgets"]
    assert budgets == [{"month": this_month, "amount": None}]  # replaced, this month only
    assert alice.api.get(BUDGET).status == 200
    assert api.post("/api/v1/orgs", {"name": "lab"}).status == 201
    assert alice.api.get("/api/v1/orgs/lab/budget").status == 404
    assert alice.api.get("/api/v1/orgs/lab/billing").status == 404
