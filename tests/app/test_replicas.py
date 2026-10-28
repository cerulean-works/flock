"""Several replicas on one database (012): nothing one replica does depends on memory another
lacks, so clients may reach any of them, in any order."""

import asyncio
import contextlib
import threading
from typing import Any

from app.api.src.core.imports import Imports
from tests.app.conftest import Client, Flocks
from tests.app.test_device import CLIENT, START, TOKEN
from tests.app.test_wandb_import import FakeWandb, source, wandb, wandb_run
from tests.app.utils import history_rows

SESSIONS = "/api/v1/me/cli-sessions"

__all__ = ["wandb"]  # the fake W&B fixture, used here too


def test_requests_spread_over_replicas_see_one_flock(make_flock: Flocks):
    replicas = make_flock.replicas(2)
    api = replicas.client()  # each request to the next replica
    api.create_run("p", "r1", config={"lr": 0.1})
    for start in range(0, 10, 2):
        api.stream("p", "r1", history_rows(2, start=start, loss=float), offset=start)
    api.stream("p", "r1", complete=0)
    for replica in replicas:
        client = replica.client()
        assert client.only_run("p")["state"] == "finished"
        assert client.run_history("p", "r1", keys="loss")["loss"]["x"] == list(range(10))
        assert client.runs("p", filter={"config.lr": 0.1})["ids"] == ["r1"]


def test_a_dashboard_streams_a_run_logged_through_the_other_replica(
    make_flock: Flocks, make_wandb_client, events
):
    first, second = make_flock.replicas(2)
    proc = make_wandb_client(first).popen(
        """
        import sys
        run = wandb.init(project="live", settings=SETTINGS)
        for step in range(20):
            run.log({"loss": 1.0 / (step + 1)})
        print("LOGGED", run.id, flush=True)
        sys.stdin.readline()  # block until the dashboard watches
        for step in range(20, 25):
            run.log({"loss": 1.0 / (step + 1)})
        run.finish()
        """
    )
    try:
        line = proc.stdout.readline()
        assert line.startswith("LOGGED"), line + proc.stderr.read()
        run_id = line.split()[1]
        dashboard = events("live", run_id, client=second.client())
        dashboard.next_of("hello")
        proc.stdin.write("\n")
        proc.stdin.flush()
        steps: set[int] = set()
        while max(steps, default=0) < 24:
            event, data = dashboard.next()
            if event == "history":
                assert data["run"] == run_id
                steps.update(data["series"]["loss"]["x"])
        assert set(range(20, 25)) <= steps
        assert dashboard.next_of("run")["state"] in ("running", "finished")
        assert proc.wait(timeout=120) == 0, proc.stderr.read()
    finally:
        if proc.poll() is None:
            proc.kill()
    for replica in (first, second):
        assert replica.client().only_run("live")["state"] == "finished"


def test_a_run_deleted_on_one_replica_stays_deleted_while_its_client_logs_through_the_other(
    make_flock: Flocks, make_wandb_client
):
    first, second = make_flock.replicas(2)
    proc = make_wandb_client(first).popen(
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
        assert second.client().delete(path, force="true").status == 204
        proc.stdin.write("\n")
        proc.stdin.flush()
        assert proc.wait(timeout=120) == 0, proc.stderr.read()
        assert "FINISHED" in proc.stdout.read()
    finally:
        if proc.poll() is None:
            proc.kill()
    for replica in (first, second):
        api = replica.client()
        assert api.get(path).status == 404
        assert api.get("/api/v1/projects/flock/doomed").json()["run_count"] == 0


def test_a_session_revoked_on_one_replica_is_refused_by_the_other_at_once(
    make_flock: Flocks, notification
):
    first, second = make_flock.replicas(2)
    alice = first.add_user("alice@lab.test")
    on_second = second.client(token=alice.token)
    assert on_second.get("/api/v1/me").status == 200  # now cached on the second replica
    browser = first.client(token=alice.signup["access_token"])
    session = next(s for s in browser.get(SESSIONS).json()["sessions"] if s["name"] == "tests")
    with notification(second.store.bus, second.loop, "flock_auth", "sessions"):
        assert browser.delete(f"{SESSIONS}/{session['id']}").status == 204
    assert on_second.get("/api/v1/me").status == 401

    # A role change too: alice stops being an admin of flock everywhere.
    admin = first.client()
    members = f"/api/v1/orgs/flock/members/{alice.id}"
    with notification(second.store.bus, second.loop, "flock_auth", "memberships"):
        assert admin.patch(members, {"role": "admin"}).status == 200
    as_alice = second.client(token=alice.signup["access_token"])
    assert as_alice.get("/api/v1/orgs/flock").json()["role"] == "admin"
    with notification(second.store.bus, second.loop, "flock_auth", "memberships"):
        assert admin.patch(members, {"role": "member"}).status == 200
    assert as_alice.get("/api/v1/orgs/flock").json()["role"] == "member"


def test_a_sign_in_approved_on_one_replica_is_polled_on_the_other(make_flock: Flocks):
    first, second = make_flock.replicas(2)
    alice = first.add_user("alice@lab.test")
    started = second.anonymous().post(START, {"client": CLIENT}).json()
    pending = second.anonymous().post(TOKEN, {"device_code": started["device_code"]})
    assert pending.json()["error"] == "authorization_pending"
    browser = first.client(token=alice.signup["access_token"])
    assert browser.post(f"{START}/{started['user_code']}", {"approve": True}).status == 204
    granted = second.anonymous().post(TOKEN, {"device_code": started["device_code"]})
    assert granted.status == 200, granted.body
    token = granted.json()["access_token"]
    assert first.client(token=token).get("/api/v1/me").json()["email"] == "alice@lab.test"


def test_an_import_of_a_killed_replica_is_marked_interrupted_by_the_other(
    make_flock: Flocks, wandb: FakeWandb, wait_for, monkeypatch
):
    monkeypatch.setattr(Imports, "HEARTBEAT", 0.2)
    monkeypatch.setattr(Imports, "STALE_AFTER", 1.0)
    first, second = make_flock.replicas(2)
    wandb.add("p", wandb_run("r1"))
    wandb.gate.clear()  # history requests hang: the import is under way on the first replica
    api = first.client()
    assert api.post("/api/v1/orgs", {"name": "lab", "wandb": source(wandb)}).status == 201
    wait_for(lambda: "FlockHistory" in wandb.queries)
    on_second = second.client()
    assert on_second.get("/api/v1/orgs/lab/import").json()["state"] == "running"
    refused = on_second.post("/api/v1/orgs/lab/import", source(wandb))
    assert refused.status == 409  # claimed by the first replica, which still heartbeats

    first.kill()
    second.call(
        second.store.db.execute(
            "UPDATE org_imports SET heartbeat_at = now() - interval '2 seconds' "
            "WHERE state = 'running'"
        )
    )
    second.call(second.store.flush())  # background work, as every replica does every few seconds
    job = on_second.get("/api/v1/orgs/lab/import").json()
    assert job["state"] == "interrupted"
    assert job["finished_at"] is not None

    wandb.gate.set()  # resumed on the replica still standing
    assert on_second.post("/api/v1/orgs/lab/import", source(wandb)).status == 202
    wait_for(lambda: on_second.get("/api/v1/orgs/lab/import").json()["state"] == "finished")
    assert on_second.runs("p", entity="lab")["ids"] == ["r1"]


def test_a_replica_killed_mid_request_loses_nothing_it_acknowledged(
    make_flock: Flocks, monkeypatch
):
    """What a replica acknowledged is kept, and what it was writing when it died isn't half
    there: the other replica sees the run as it was, and can write to it at once."""
    first, second = make_flock.replicas(2)
    api = first.client()
    api.create_run("p", "r1")
    api.stream("p", "r1", history_rows(5, loss=float), summary={"loss": 4.0})  # acknowledged

    # The next request stops inside its transaction, after locking the run.
    inside = threading.Event()

    async def stuck(conn: Any, pk: int, prefix: str) -> dict[str, Any]:
        inside.set()
        await asyncio.Event().wait()
        return {}

    # Read under the run's lock, before the request's writes.
    monkeypatch.setattr(first.store, "_prefixed", stuck)
    request = threading.Thread(
        target=lambda: _quietly(
            Client(first.port, first.api_key, first.token).stream,
            "p",
            "r1",
            history_rows(5, start=5, loss=float),
            offset=5,
            summary={"loss": 9.0},
        ),
        daemon=True,
    )
    request.start()
    assert inside.wait(30)
    first.kill()

    other = second.client()
    run = other.only_run("p")
    assert (run["summary.loss"], run["state"]) == (4.0, "running")
    assert other.run_history("p", "r1", keys="loss")["loss"]["x"] == list(range(5))
    detail = other.get("/api/v1/projects/flock/p/runs/r1").json()
    assert detail["history_line_count"] == 5
    # The client resends what wasn't acknowledged, to the replica still standing.
    other.stream("p", "r1", history_rows(5, start=5, loss=float), offset=5, summary={"loss": 9.0})
    assert other.run_history("p", "r1", keys="loss")["loss"]["x"] == list(range(10))
    assert other.only_run("p")["summary.loss"] == 9.0


def _quietly(call, *args, **kwargs) -> None:
    # The replica dies under this request: no answer comes.
    with contextlib.suppress(Exception):
        call(*args, **kwargs)
