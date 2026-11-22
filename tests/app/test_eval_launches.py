"""Evals launched on Modal from the web app (026): who may launch, what may be launched, the
caps, the per-launch wandb key, cancelling, failures and taking a launch over after a restart.
Modal is a fake with the deployed worker's calls; the Hub is an httpx mock."""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import asyncpg
import httpx
import pytest

from app.api.src.core.eval_launches import EvalLaunches, Refused

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.app.conftest import Flocks, FlockServer


LAUNCHES = "/api/v1/orgs/flock/evals/launches"
SHA = "d" * 40
HUB = "https://hub.test"
PUBLIC_URL = "http://api.flock.test"
CATALOGUE = {"suites": ["base_easy", "paloma"], "tasks": ["arc_easy", "drcd"]}


@dataclass
class FakeModal:
    """The deployed worker: each spawn is a call that runs until ``outcomes`` names its result,
    a flock commit, or an exception it raised."""

    spawned: list[dict[str, Any]] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    outcomes: dict[str, str | Exception] = field(default_factory=dict)
    refuse: bool = False
    lose: bool = False  # the spawn's answer is lost: the call may have started
    down: bool = False  # cancelling fails, as when Modal can't be reached
    end_on_cancel: bool = False  # a cancel ends the call at once, as Modal reports it
    # A spawn waits for this, when set.
    gate: threading.Event | None = None

    async def catalogue(self) -> dict[str, list[str]]:
        return CATALOGUE

    async def spawn(self, timeout: int, env: dict[str, str | None], **kwargs: Any) -> str:
        if self.refuse:
            raise Refused("no such app")
        if self.lose:
            raise RuntimeError("connection reset")
        while self.gate is not None and not self.gate.is_set():
            await asyncio.sleep(0.02)
        call_id = f"fc-{len(self.spawned)}"
        self.spawned.append({"id": call_id, "timeout": timeout, "env": env, **kwargs})
        return call_id

    async def wait(self, call_id: str, timeout: float) -> str | None:
        outcome = self.outcomes.get(call_id)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is None:
            await asyncio.sleep(min(timeout, 0.05))
        return outcome

    async def cancel(self, call_id: str) -> None:
        if self.down:
            raise RuntimeError("Modal is down")
        self.cancelled.append(call_id)
        if self.end_on_cancel:
            self.outcomes[call_id] = RuntimeError("the call was cancelled")
            await asyncio.sleep(0.2)  # the watcher sees it end first


def hub_transport() -> httpx.MockTransport:
    """The Hub's model API: a public, a gated and a private model."""
    flags = {"acme/tiny": {}, "acme/gated": {"gated": "manual"}, "acme/secret": None}

    async def handle(request: httpx.Request) -> httpx.Response:
        repo = request.url.path.removeprefix("/api/models/").partition("/revision/")[0]
        found = flags.get(repo, None)
        if found is None:  # missing, or private to an anonymous caller
            return httpx.Response(401, headers={"x-error-code": "RepoNotFound"})
        body = {"id": repo, "sha": SHA, "siblings": [], **found}
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handle)


@pytest.fixture(autouse=True)
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(EvalLaunches, "HEARTBEAT", 0.05)
    monkeypatch.setattr(EvalLaunches, "STALE_AFTER", 0.5)


@pytest.fixture
def make_server(make_flock: Flocks) -> Callable[..., tuple[FlockServer, FakeModal]]:
    def make(reuse: FlockServer | None = None, **settings: Any) -> tuple[FlockServer, FakeModal]:
        settings.setdefault("hf_endpoint", HUB)
        settings.setdefault("api_public_url", PUBLIC_URL)
        settings.setdefault("modal_token_id", "ak-test")
        settings.setdefault("modal_token_secret", "as-test")
        settings.setdefault("eval_launch_usd_per_hour", 2.0)
        server = make_flock(reuse=reuse, **settings)
        modal = FakeModal()
        launches = server.store.eval_launches
        if launches.modal is not None:
            launches.modal = modal
        launches.transport = hub_transport()
        return server, modal

    return make


def sql(server: FlockServer, query: str, *args: Any) -> list[asyncpg.Record]:
    async def run() -> list[asyncpg.Record]:
        conn = await asyncpg.connect(server.database_url)
        try:
            return await conn.fetch(query, *args)
        finally:
            await conn.close()

    return asyncio.run(run())


def body(**fields: Any) -> dict[str, Any]:
    return {"model": "acme/tiny", "suites": ["base_easy"], "project": "evals", **fields}


def test_a_launch_scores_the_pinned_model_and_its_key_ends_with_it(
    make_server: Callable[..., tuple[FlockServer, FakeModal]], wait_for: Callable[..., Any]
) -> None:
    server, modal = make_server()
    api = server.client()
    started = api.post(LAUNCHES, body(tasks=["drcd"], add_bos=True))
    assert started.status == 202, started.body
    job = started.json()
    assert job["state"] == "running" and job["sha"] == SHA
    assert job["run"] == f"eval-launch-{job['id']}"
    assert job["cap_usd"] == 25.0

    # The worker gets the commit, never a moving revision, and the cap as its timeout.
    [call] = modal.spawned
    assert call["model"] == f"acme/tiny@{SHA}"
    assert (call["suites"], call["tasks"], call["add_bos"]) == (["base_easy"], ["drcd"], True)
    assert call["project"] == "evals"
    # $25 at $2 an hour, less ten minutes for the container to start and stop.
    assert call["timeout"] == 25 * 3600 // 2 - 600
    env = call["env"]
    assert env["WANDB_BASE_URL"] == PUBLIC_URL and env["WANDB_ENTITY"] == "flock"
    assert env["WANDB_RUN_ID"] == job["run"]
    assert env["HF_ENDPOINT"] == HUB  # the Hub the model was checked on
    key = env["WANDB_API_KEY"]
    assert key and server.call(server.store.accounts.api_key(key)) is not None

    modal.outcomes[call["id"]] = "abc1234"
    done = wait_for(
        lambda: (j := api.get(f"{LAUNCHES}/{job['id']}").json())["state"] != "running" and j
    )
    assert done["state"] == "finished" and done["flock_commit"] == "abc1234"
    assert done["finished_at"] is not None
    # Done with it, so the key is gone: it can't log or read anything any more.
    assert server.call(server.store.accounts.api_key(key)) is None
    assert sql(server, "SELECT api_key_pk FROM eval_launches")[0][0] is None
    assert [j["id"] for j in api.get(LAUNCHES).json()["launches"]] == [job["id"]]


def test_only_public_suites_tasks_and_models_are_launched(
    make_server: Callable[..., tuple[FlockServer, FakeModal]],
) -> None:
    server, modal = make_server()
    api = server.client()
    catalogue = api.get(f"{LAUNCHES}/catalogue").json()
    assert catalogue == {**CATALOGUE, "max_usd": 25.0, "monthly_usd": 300.0}

    refused = api.post(LAUNCHES, body(suites=["zh_tw"], tasks=["tc_edu_math", "drcd"]))
    assert refused.status == 422
    assert "zh_tw, tc_edu_math" in refused.json()["error"]
    assert api.post(LAUNCHES, body(suites=[], tasks=[])).status == 422
    assert api.post(LAUNCHES, body(suites=["base_easy", "base_easy"])).status == 422
    assert api.post(LAUNCHES, body(model="acme/gated")).status == 403
    assert api.post(LAUNCHES, body(model="acme/secret")).status == 404
    assert api.post(LAUNCHES, body(project="a/b")).status == 422

    # Nothing started and no key was made for any of them.
    assert modal.spawned == []
    assert sql(server, "SELECT count(*) FROM eval_launches")[0][0] == 0
    assert sql(server, "SELECT count(*) FROM api_keys WHERE name LIKE 'eval launch%'")[0][0] == 0


def test_launches_are_off_without_the_modal_token_price_and_public_url(
    make_server: Callable[..., tuple[FlockServer, FakeModal]],
) -> None:
    for missing in ("modal_token_secret", "eval_launch_usd_per_hour", "api_public_url"):
        server, modal = make_server(
            **{missing: None if missing != "eval_launch_usd_per_hour" else 0.0}
        )
        api = server.client()
        assert api.get(f"{LAUNCHES}/catalogue").status == 404, missing
        assert api.post(LAUNCHES, body()).status == 404, missing
        assert modal.spawned == []


def test_only_members_launch_and_only_the_launcher_or_an_admin_cancels(
    make_server: Callable[..., tuple[FlockServer, FakeModal]], wait_for: Callable[..., Any]
) -> None:
    server, modal = make_server()
    member = server.add_user("member@flock.test")
    other = server.add_user("other@flock.test")
    server.call(server.store.create_org("rival"))
    outsider = server.add_user("outsider@flock.test", org="rival")

    assert server.anonymous().post(LAUNCHES, body()).status == 401
    assert server.anonymous().get(LAUNCHES).status == 401
    assert outsider.api.post(LAUNCHES, body()).status == 404
    assert outsider.api.get(LAUNCHES).status == 404
    assert outsider.api.get(f"{LAUNCHES}/catalogue").status == 404
    # A wandb key reaches tracking only, so it can't launch (a launch's own key included).
    assert server.client(api_key=member.api_key).post(LAUNCHES, body()).status == 401

    started = member.api.post(LAUNCHES, body())
    assert started.status == 202, started.body
    pk = started.json()["id"]
    assert started.json()["started_by"]["id"] == member.id
    assert outsider.api.get(f"{LAUNCHES}/{pk}").status == 404
    assert outsider.api.delete(f"{LAUNCHES}/{pk}").status == 404
    assert other.api.delete(f"{LAUNCHES}/{pk}").status == 403
    assert modal.cancelled == []

    cancelled = server.client().delete(f"{LAUNCHES}/{pk}")  # the admin
    assert cancelled.status == 200 and cancelled.json()["state"] == "cancelled"
    assert modal.cancelled == [modal.spawned[0]["id"]]
    key = modal.spawned[0]["env"]["WANDB_API_KEY"]
    assert server.call(server.store.accounts.api_key(key)) is None
    # The watcher sees it ended elsewhere and stops; a late result changes nothing.
    modal.outcomes[modal.spawned[0]["id"]] = "abc1234"
    wait_for(lambda: not server.store.eval_launches.jobs)
    assert member.api.get(f"{LAUNCHES}/{pk}").json()["state"] == "cancelled"


def test_the_monthly_cap_counts_running_launches_at_their_cap(
    make_server: Callable[..., tuple[FlockServer, FakeModal]],
) -> None:
    server, modal = make_server(eval_launch_monthly_usd=60.0)
    api = server.client()
    first = api.post(LAUNCHES, body()).json()
    assert api.post(LAUNCHES, body()).status == 202
    refused = api.post(LAUNCHES, body())
    assert refused.status == 409 and "$50.00 of $60.00" in refused.json()["error"]
    assert len(modal.spawned) == 2

    # Once one ends it counts at its time, here an hour at $2, which leaves room again.
    api.delete(f"{LAUNCHES}/{first['id']}")
    sql(
        server,
        "UPDATE eval_launches SET started_at = finished_at - interval '1 hour' WHERE pk = $1",
        first["id"],
    )
    assert api.post(LAUNCHES, body()).status == 202

    # A running launch counts at its cap whenever it started, and an ended one that ran into
    # this month at its whole time; launches that ended before the month don't count.
    sql(server, "UPDATE eval_launches SET started_at = now() - interval '40 days'")
    assert api.post(LAUNCHES, body()).status == 409
    for job in api.get(LAUNCHES).json()["launches"]:
        api.delete(f"{LAUNCHES}/{job['id']}")
    sql(
        server,
        "UPDATE eval_launches SET started_at = now() - interval '40 days', "
        "finished_at = now() - interval '35 days'",
    )
    assert api.post(LAUNCHES, body()).status == 202


def test_a_failed_launch_says_why_and_its_key_is_deleted(
    make_server: Callable[..., tuple[FlockServer, FakeModal]], wait_for: Callable[..., Any]
) -> None:
    server, modal = make_server()
    api = server.client()
    pk = api.post(LAUNCHES, body()).json()["id"]
    call = modal.spawned[0]
    modal.outcomes[call["id"]] = RuntimeError("not public: tw_text")
    done = wait_for(lambda: (j := api.get(f"{LAUNCHES}/{pk}").json())["state"] != "running" and j)
    assert done["state"] == "failed" and done["error"] == "not public: tw_text"
    assert server.call(server.store.accounts.api_key(call["env"]["WANDB_API_KEY"])) is None

    # Modal refusing the call fails the launch at once, with its key.
    modal.refuse = True
    refused = api.post(LAUNCHES, body())
    assert refused.status == 502 and "no such app" in refused.json()["error"]
    # A lost answer may hide a call that started: that launch keeps its whole cap.
    modal.refuse, modal.lose = False, True
    lost = api.post(LAUNCHES, body())
    assert lost.status == 502 and "answer to the launch was lost" in lost.json()["error"]
    rows = sql(server, "SELECT state, api_key_pk, unconfirmed FROM eval_launches ORDER BY pk")
    assert [tuple(r) for r in rows[1:]] == [("failed", None, False), ("failed", None, True)]
    assert sql(server, "SELECT count(*) FROM api_keys WHERE name LIKE 'eval launch%'")[0][0] == 0
    # The refused one costs nothing; the lost one keeps its cap; the first ran for minutes.
    spent = server.call(
        server.store.db.fetchval(EvalLaunches.SPENT, EvalLaunches.OVERHEAD_SECONDS, 10**6)
    )
    assert 25 < spent < 25 + 2 * (600 + 60) / 3600 + 0.01


def test_a_cap_that_buys_less_than_a_minute_is_refused(
    make_server: Callable[..., tuple[FlockServer, FakeModal]],
) -> None:
    server, modal = make_server(eval_launch_max_usd=0.3)  # ten minutes at $2 is $0.33
    refused = server.client().post(LAUNCHES, body())
    assert refused.status == 422 and "FLOCK_EVAL_LAUNCH_MAX_USD" in refused.json()["error"]
    assert modal.spawned == []
    assert sql(server, "SELECT count(*) FROM eval_launches")[0][0] == 0


def test_a_cancelled_launch_reads_cancelled_whoever_sees_its_end(
    make_server: Callable[..., tuple[FlockServer, FakeModal]], wait_for: Callable[..., Any]
) -> None:
    server, modal = make_server()
    api = server.client()
    pk = api.post(LAUNCHES, body()).json()["id"]
    # Modal ends the call at once, so the watcher may see it fail before the cancel records.
    modal.end_on_cancel = True
    assert api.delete(f"{LAUNCHES}/{pk}").json()["state"] == "cancelled"
    wait_for(lambda: not server.store.eval_launches.jobs)
    assert api.get(f"{LAUNCHES}/{pk}").json()["state"] == "cancelled"


def test_ended_launches_follow_their_project_when_it_is_renamed(
    make_server: Callable[..., tuple[FlockServer, FakeModal]], wait_for: Callable[..., Any]
) -> None:
    server, modal = make_server()
    api = server.client()
    assert api.post("/api/v1/projects", {"entity": "flock", "name": "evals"}).status == 201
    pk = api.post(LAUNCHES, body()).json()["id"]
    modal.outcomes["fc-0"] = "abc1234"
    wait_for(lambda: api.get(f"{LAUNCHES}/{pk}").json()["state"] == "finished")
    assert api.patch("/api/v1/projects/flock/evals", {"name": "renamed"}).status == 200
    listed = api.get(LAUNCHES, project="renamed").json()["launches"]
    assert [j["id"] for j in listed] == [pk]


def test_a_launch_goes_on_through_a_restart_and_another_replica_takes_it_over(
    make_server: Callable[..., tuple[FlockServer, FakeModal]], wait_for: Callable[..., Any]
) -> None:
    server, _ = make_server()
    pk = server.client().post(LAUNCHES, body()).json()["id"]
    server.stop()

    # The call runs on Modal whatever happens to the replica: the launch stays running.
    restarted, modal = make_server(reuse=server)
    api = restarted.client()
    assert api.get(f"{LAUNCHES}/{pk}").json()["state"] == "running"
    wait_for(lambda: pk in restarted.store.eval_launches.jobs)
    modal.outcomes["fc-0"] = "abc1234"
    done = wait_for(lambda: (j := api.get(f"{LAUNCHES}/{pk}").json())["state"] != "running" and j)
    assert done["state"] == "finished"

    # One cut short between its row and its call ends failed, and its key goes.
    sql(
        restarted,
        "INSERT INTO eval_launches (org_pk, project, model, revision, sha, run, state, cap_usd, "
        "usd_per_hour, started_at, heartbeat_at) SELECT pk, 'evals', 'acme/tiny', 'main', $1, "
        "'eval-launch-x', 'running', 25, 2, now(), now() - interval '1 hour' FROM orgs",
        SHA,
    )
    rows = wait_for(
        lambda: (
            (r := sql(restarted, "SELECT state, error FROM eval_launches ORDER BY pk"))[1][0]
            != "running"
            and r
        )
    )
    assert rows[1]["state"] == "failed" and "cut short" in rows[1]["error"]


def test_the_key_never_reaches_the_database_responses_or_logs(
    make_server: Callable[..., tuple[FlockServer, FakeModal]],
    wait_for: Callable[..., Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    server, modal = make_server()
    api = server.client()
    started = api.post(LAUNCHES, body())
    key = modal.spawned[0]["env"]["WANDB_API_KEY"]
    assert key not in started.body.decode()
    assert key not in api.get(LAUNCHES).body.decode()
    rows = sql(server, "SELECT row_to_json(t)::text FROM eval_launches t")
    assert rows and all(key not in row[0] for row in rows)
    modal.outcomes["fc-0"] = RuntimeError("boom")
    wait_for(lambda: not server.store.eval_launches.jobs)
    assert key not in caplog.text


def test_deleting_the_org_stops_its_launches_until_modal_confirms(
    make_server: Callable[..., tuple[FlockServer, FakeModal]], wait_for: Callable[..., Any]
) -> None:
    server, modal = make_server()
    api = server.client()
    pk = api.post(LAUNCHES, body()).json()["id"]
    # A refused delete (an active run) leaves its launches running.
    api.create_run("p", "active")
    org = server.call(server.store.find_org("flock"))
    with pytest.raises(Exception, match="active"):
        server.call(server.store.delete_org(org))
    assert modal.cancelled == []

    # Modal down at first: the watcher keeps asking, and ends the launch once it's cancelled.
    modal.down = True
    server.call(server.store.delete_org(org, force=True))
    wait_for(
        lambda: sql(server, "SELECT heartbeat_at > now() - interval '1 second' FROM eval_launches")[
            0
        ][0]
    )
    assert sql(server, "SELECT state FROM eval_launches")[0][0] == "running"
    modal.down = False
    wait_for(lambda: modal.cancelled == ["fc-0"])
    wait_for(lambda: sql(server, "SELECT state FROM eval_launches")[0][0] == "cancelled")
    assert sql(server, "SELECT count(*) FROM api_keys WHERE name LIKE 'eval launch%'")[0][0] == 0
    # The row outlives the org's purge, so the month's spend still counts it.
    server.call(server.store.flush())
    assert sql(server, "SELECT pk FROM eval_launches")[0][0] == pk


def test_a_cancel_waits_for_the_call_and_a_slow_spawn_taken_over_is_stopped(
    make_server: Callable[..., tuple[FlockServer, FakeModal]], wait_for: Callable[..., Any]
) -> None:
    server, modal = make_server()
    api = server.client()
    gate = modal.gate = threading.Event()
    started: list[Any] = []
    thread = threading.Thread(target=lambda: started.append(api.post(LAUNCHES, body())))
    thread.start()
    pk = wait_for(lambda: (r := sql(server, "SELECT pk FROM eval_launches")) and r[0][0])
    refused = server.client().delete(f"{LAUNCHES}/{pk}")
    assert refused.status == 409 and "still starting" in refused.json()["error"]

    # Its replica looks gone: another ends the row, so the call made late is stopped at once.
    sql(server, "UPDATE eval_launches SET heartbeat_at = now() - interval '1 hour'")
    wait_for(lambda: sql(server, "SELECT state FROM eval_launches")[0][0] == "failed")
    gate.set()
    thread.join(10)
    assert started[0].json()["state"] == "failed"
    assert modal.cancelled == ["fc-0"]
    assert sql(server, "SELECT count(*) FROM api_keys WHERE name LIKE 'eval launch%'")[0][0] == 0


def test_a_project_lists_its_own_launches_and_every_running_one(
    make_server: Callable[..., tuple[FlockServer, FakeModal]],
) -> None:
    server, _ = make_server()
    api = server.client()
    old = api.post(LAUNCHES, body(project="evals")).json()["id"]
    api.post(LAUNCHES, body(project="other"))
    sql(server, "UPDATE eval_launches SET started_at = now() - interval '90 days'")
    listed = api.get(LAUNCHES, project="evals").json()["launches"]
    assert [j["id"] for j in listed] == [old]  # running, so listed however old


def test_only_the_launchs_own_key_is_deleted(
    make_server: Callable[..., tuple[FlockServer, FakeModal]], wait_for: Callable[..., Any]
) -> None:
    server, modal = make_server()
    api = server.client()
    # A key of the user's own that happens to bear a launch's name.
    own = api.post("/api/v1/me/wandb-keys", {"name": "eval launch 1"}).json()["key"]
    pk = api.post(LAUNCHES, body()).json()["id"]
    assert pk == 1
    modal.outcomes["fc-0"] = "abc1234"
    wait_for(lambda: api.get(f"{LAUNCHES}/{pk}").json()["state"] == "finished")
    assert server.call(server.store.accounts.api_key(own)) is not None
    launch_key = modal.spawned[0]["env"]["WANDB_API_KEY"]
    assert server.call(server.store.accounts.api_key(launch_key)) is None


def test_a_project_with_a_running_launch_is_not_renamed_or_deleted(
    make_server: Callable[..., tuple[FlockServer, FakeModal]],
) -> None:
    server, _ = make_server()
    api = server.client()
    assert api.post("/api/v1/projects", {"entity": "flock", "name": "evals"}).status == 201
    pk = api.post(LAUNCHES, body()).json()["id"]
    renamed = api.patch("/api/v1/projects/flock/evals", {"name": "elsewhere"})
    assert renamed.status == 409 and "eval launch" in renamed.json()["error"]
    deleted = api.delete("/api/v1/projects/flock/evals", force="true")
    assert deleted.status == 409
    api.delete(f"{LAUNCHES}/{pk}")
    assert api.patch("/api/v1/projects/flock/evals", {"name": "elsewhere"}).status == 200


def test_a_launch_past_its_longest_life_ends_even_where_launches_are_off(
    make_server: Callable[..., tuple[FlockServer, FakeModal]],
) -> None:
    server, modal = make_server()
    pk = server.client().post(LAUNCHES, body()).json()["id"]
    key = modal.spawned[0]["env"]["WANDB_API_KEY"]
    server.stop()

    # Restarted without Modal: no new launch, but the old one's key can't live on.
    restarted, _ = make_server(reuse=server, modal_token_secret=None)
    assert restarted.client().get(f"{LAUNCHES}/{pk}").json()["state"] == "running"
    # Not yet: Modal may have held the call in its queue for up to a day.
    sql(restarted, "UPDATE eval_launches SET started_at = now() - interval '13 hours'")
    restarted.call(restarted.store.eval_launches.sweep())
    assert restarted.client().get(f"{LAUNCHES}/{pk}").json()["state"] == "running"
    sql(restarted, "UPDATE eval_launches SET started_at = now() - interval '40 hours'")
    restarted.call(restarted.store.eval_launches.sweep())
    job = restarted.client().get(f"{LAUNCHES}/{pk}").json()
    assert job["state"] == "failed" and "time limit" in job["error"]
    assert restarted.call(restarted.store.accounts.api_key(key)) is None


def test_the_modal_adapter_tells_a_poll_deadline_from_the_calls_own_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import modal

    from app.api.src.core.eval_launches import Modal

    raised: list[BaseException] = []

    class Call:
        class get:
            @staticmethod
            async def aio(timeout: float) -> str:
                raise raised[0]

    monkeypatch.setattr(modal.FunctionCall, "from_id", lambda call_id, client: Call())
    adapter = Modal("ak", "as")
    adapter._client = object()
    # Still running: the poll's deadline, or Modal itself unavailable for a while.
    for error in (
        TimeoutError(),
        modal.exception.ServiceError("unavailable"),
        modal.exception.ResourceExhaustedError("rate limited"),
    ):
        raised[:] = [error]
        assert asyncio.run(adapter.wait("fc-0", 0.01)) is None
    # The call's own end: its time limit, or what the worker raised.
    for error in (modal.exception.FunctionTimeoutError("limit"), RuntimeError("not public")):
        raised[:] = [error]
        with pytest.raises(type(error)):
            asyncio.run(adapter.wait("fc-0", 0.01))


def test_the_worker_runs_only_suites_and_tasks_on_public_datasets() -> None:
    from app.api import eval_score
    from flock.eval.tasks import SUITES, TASKS

    public = eval_score.public_names()
    assert {"base_easy", "paloma"} <= set(public["suites"])
    assert "drcd_bpb" in public["tasks"]
    # Each of these holds a task on a private dataset: the exams, tc-edu, CMMLU's mirror.
    assert not {"zh_tw", "zh_tw:bpb", "tc_edu", "tw_text", "zh_cn"} & set(public["suites"])
    for name in public["tasks"]:
        assert not TASKS[name].dataset.private
    for name in public["suites"]:
        assert not any(task.dataset.private for task in SUITES[name].tasks)

    with pytest.raises(SystemExit, match="not public, or not a suite or task of this flock: zh_tw"):
        eval_score.score("acme/tiny@" + SHA, ["base_easy", "zh_tw"], [], False, "evals")
