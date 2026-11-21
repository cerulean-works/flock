"""Public projects (015): read-only access for anyone with the link, on projects an org admin
made public."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest

from tests.app.conftest import Client, EventStream, FlockServer

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    from tests.app.conftest import Response


P = "/api/v1/projects/flock"

# The routes outsiders may read on a public project (D112), with a POST body for queries.
READABLE = [
    ("GET", "/p", None),
    ("GET", "/p/owners", None),
    ("GET", "/p/columns", None),
    ("GET", "/p/runs", None),
    ("POST", "/p/runs/query", {}),
    ("GET", "/p/history?keys=loss", None),
    ("POST", "/p/history/query", {"keys": ["loss"]}),
    ("GET", "/p/runs/r1", None),
    ("GET", "/p/runs/r1/history", None),
    ("GET", "/p/runs/r1/logs", None),
    ("GET", "/p/runs/r1/files", None),
    ("GET", "/p/runs/r1/files/config.yaml", None),
    ("GET", "/p/workspace", None),
]
# The routes only members reach, even on a public project.
MEMBERS_ONLY = [
    ("PATCH", "/p", {"description": "x"}),
    ("DELETE", "/p", None),
    ("PATCH", "/p/runs/r1", {"notes": "x"}),
    ("DELETE", "/p/runs/r1", None),
    ("POST", "/p/runs/update", {"runs": ["r1"], "set": {"notes": "x"}}),
    ("POST", "/p/runs/delete", {"runs": ["r1"]}),
    ("POST", "/p/runs/move", {"runs": ["r1"], "to": "q"}),
    ("GET", "/p/runs/r1/models", None),
]


def call(client: Client, method: str, path: str, body: Any = None) -> Response:
    return client.request(method, P + path, body=body)


@pytest.fixture
def setup(flock: FlockServer) -> tuple[Client, Client, Client, Client]:
    """A project ``p`` with a finished run ``r1`` (logged by the super admin), a member of
    ``flock``, an outsider (a member of ``lab`` only), and an anonymous client."""
    api = flock.client()
    api.create_run("p", "r1", config={"lr": 0.1})
    api.stream("p", "r1", history=[{"_step": 0, "loss": 1.0}], complete=0)
    api.upload("p", "r1", "config.yaml", b"lr: 0.1\n")
    assert api.post("/api/v1/orgs", {"name": "lab"}).status == 201
    member = flock.add_user("m@x.io")
    outsider = flock.add_user("o@x.io", org="lab")
    return api, member.api, outsider.api, flock.anonymous()


def publish(api: Client, project: str = "p", visibility: str = "public") -> dict:
    resp = api.patch(f"{P}/{project}", {"visibility": visibility})
    assert resp.status == 200, resp.body
    return resp.json()


def test_outsiders_read_public_projects_and_get_404_on_private_ones(
    setup: tuple[Client, Client, Client, Client],
) -> None:
    api, _, outsider, anon = setup
    # The same answer as for a project that doesn't exist, so its existence isn't revealed.
    unknown = call(anon, "GET", "/nope")
    assert call(anon, "GET", "/p").body == unknown.body.replace(b"nope", b"p")
    for client in (outsider, anon):
        for method, path, body in READABLE:
            resp = call(client, method, path, body)
            assert resp.status == 404, (method, path)
            assert resp.json()["error"] == "project flock/p not found", (method, path)

    publish(api)
    for client in (outsider, anon):
        for method, path, body in READABLE:
            resp = call(client, method, path, body)
            assert resp.status == 200, (method, path, resp.body)
        assert call(client, "GET", "/p").json()["access"] == "public"
        assert call(client, "GET", "/p").json()["visibility"] == "public"
        assert client.runs("p")["ids"] == ["r1"]
        assert client.run_history("p", "r1")["loss"] == {"x": [0], "y": [1.0]}
        assert client.workspace()["runs"][0]["id"] == "r1"
        assert call(client, "GET", "/p/runs/r1/files/config.yaml").body == b"lr: 0.1\n"


def test_members_only_routes_refuse_outsiders_on_public_projects(
    setup: tuple[Client, Client, Client, Client],
) -> None:
    api, _, outsider, anon = setup
    publish(api)
    for method, path, body in MEMBERS_ONLY:
        assert call(anon, method, path, body).status == 401, (method, path)
        assert call(outsider, method, path, body).status == 404, (method, path)
    assert anon.post("/api/v1/projects", {"entity": "flock", "name": "new"}).status == 401
    assert outsider.post("/api/v1/projects", {"entity": "flock", "name": "new"}).status == 404
    assert call(api, "GET", "/p/runs/r1").status == 200  # nothing was changed or deleted


def test_bad_credentials_are_401_not_the_public_view(
    setup: tuple[Client, Client, Client, Client], flock: FlockServer
) -> None:
    api, _, _, _ = setup
    publish(api)
    expired = Client(flock.port, token="a.b.c")
    assert call(expired, "GET", "/p").status == 401
    cli = Client(flock.port, token="flock_cli_nope")
    assert call(cli, "GET", "/p").status == 401
    # A wandb key reaches the wandb protocol only (D72), public project or not.
    wandb_key = Client(flock.port, api_key=flock.api_key)
    resp = call(wandb_key, "GET", "/p")
    assert resp.status == 401
    assert "wandb keys only work with the wandb SDK" in resp.json()["error"]


def test_outsiders_see_no_cost_or_email(setup: tuple[Client, Client, Client, Client]) -> None:
    api, member, outsider, anon = setup
    publish(api)
    for client in (api, member):
        run = call(client, "GET", "/p/runs/r1").json()
        assert run["cost"] == 0
        assert run["owner_user"]["email"] == FlockServer.ADMIN_EMAIL
        owners = call(client, "GET", "/p/owners").json()["owners"]
        assert [o["email"] for o in owners] == [FlockServer.ADMIN_EMAIL]
    for client in (outsider, anon):
        run = call(client, "GET", "/p/runs/r1").json()
        assert run["cost"] is None
        assert run["owner_user"] == {"id": run["owner"], "name": "Admin", "avatar_url": None}
        assert call(client, "GET", "/p/owners").json()["owners"] == [run["owner_user"]]
        # The owner column still filters by the user's id.
        assert client.runs("p", filter={"owner": run["owner"]})["ids"] == ["r1"]


def test_outsiders_get_run_metadata_without_who_ran_it(
    setup: tuple[Client, Client, Client, Client],
) -> None:
    api, member, outsider, anon = setup
    metadata = {"email": "a@x.io", "username": "alice", "program": "train.py", "host": "n1"}
    api.upload("p", "r1", "wandb-metadata.json", json.dumps(metadata).encode())
    publish(api)
    path = "/p/runs/r1/files/wandb-metadata.json"
    for client in (api, member):
        assert json.loads(call(client, "GET", path).body) == metadata
    for client in (outsider, anon):
        assert call(client, "GET", path).json() == {"program": "train.py", "host": "n1"}
    # One that can't be stripped isn't served raw.
    api.upload("p", "r1", "wandb-metadata.json", b'{"email": "a@x.io"')
    assert call(member, "GET", path).status == 200
    for client in (outsider, anon):
        assert call(client, "GET", path).status == 404


@pytest.mark.parametrize("name", ["%2e/wandb-metadata.json", ".//wandb-metadata.json"])
def test_metadata_aliases_do_not_expose_private_fields(
    setup: tuple[Client, Client, Client, Client], name: str
) -> None:
    api, member, outsider, anon = setup
    metadata = {"email": "fixture@example.test", "username": "fixture", "host": "n1"}
    api.upload("p", "r1", "wandb-metadata.json", json.dumps(metadata).encode())
    publish(api)
    for client in (outsider, anon):
        response = call(client, "GET", f"/p/runs/r1/files/{name}")
        assert response.status == 404 or response.json() == {"host": "n1"}
    assert call(member, "GET", "/p/runs/r1/files/wandb-metadata.json").json() == metadata


def test_private_run_files_stay_with_members_whatever_the_projects_visibility(
    setup: tuple[Client, Client, Client, Client],
) -> None:
    api, member, outsider, anon = setup
    rows = b'{"id": "q-1", "gold": 0, "correct": true}\n'
    api.upload("p", "r1", "private/predictions/exam.jsonl", rows)
    api.upload("p", "r1", "predictions/toy.jsonl", rows)
    # Only the first path component counts: a name that merely starts with it is public.
    api.upload("p", "r1", "private-notes.txt", b"public")
    # In any case: a case-insensitive volume would serve it from the same folder.
    api.upload("p", "r1", "Private/x.jsonl", rows)
    publish(api)

    every = {"config.yaml", "private/predictions/exam.jsonl", "predictions/toy.jsonl"}
    every |= {"private-notes.txt", "Private/x.jsonl"}
    for client in (api, member):  # the super admin and a member
        assert {f["name"] for f in call(client, "GET", "/p/runs/r1/files").json()["files"]} == every
        assert call(client, "GET", "/p/runs/r1/files/private/predictions/exam.jsonl").body == rows
        assert call(client, "GET", "/p/runs/r1/files/Private/x.jsonl").body == rows
    for client in (outsider, anon):
        listed = {f["name"] for f in call(client, "GET", "/p/runs/r1/files").json()["files"]}
        assert listed == every - {"private/predictions/exam.jsonl", "Private/x.jsonl"}
        assert call(client, "GET", "/p/runs/r1/files/Private/x.jsonl").status == 404
        assert call(client, "GET", "/p/runs/r1/files/predictions/toy.jsonl").body == rows
        assert call(client, "GET", "/p/runs/r1/files/private-notes.txt").body == b"public"
        # The same answer as a file the run doesn't have, for every spelling of the path.
        missing = call(client, "GET", "/p/runs/r1/files/predictions/nope.jsonl")
        for name in (
            "private/predictions/exam.jsonl",
            "./private/predictions/exam.jsonl",
            "private//predictions/exam.jsonl",
            "%2e/private/predictions/exam.jsonl",
            "%70rivate/predictions/exam.jsonl",
            "PRIVATE/predictions/exam.jsonl",  # the same folder on a case-insensitive volume
        ):
            response = call(client, "GET", f"/p/runs/r1/files/{name}")
            assert (response.status, response.body) == (404, missing.body), name

    # Moved into another public project, the run's private files stay with members.
    api.create_run("q", "r2")
    api.upload("q", "r2", "private/predictions/exam.jsonl", rows)
    api.stream("q", "r2", complete=0)
    assert api.post(f"{P}/q/runs/move", {"runs": ["r2"], "to": "p"}).status == 200
    assert call(anon, "GET", "/p/runs/r2/files/private/predictions/exam.jsonl").status == 404
    assert call(anon, "GET", "/p/runs/r2/files").json()["files"] == []
    assert call(member, "GET", "/p/runs/r2/files/private/predictions/exam.jsonl").body == rows


def test_admins_change_visibility_of_projects_they_didnt_create(
    setup: tuple[Client, Client, Client, Client], flock: FlockServer
) -> None:
    api, member, outsider, _ = setup
    assert member.patch(f"{P}/p", {"visibility": "public"}).status == 403
    assert outsider.patch(f"{P}/p", {"visibility": "public"}).status == 404
    assert api.patch(f"{P}/p", {"visibility": "secret"}).status == 422
    assert api.patch(f"{P}/p", {"visibility": None}).status == 422
    assert publish(api)["visibility"] == "public"
    assert publish(api)["visibility"] == "public"  # no change: no second event
    assert publish(api, visibility="private")["visibility"] == "private"
    events = flock.call(
        flock.store.db.fetch(
            "SELECT kind, from_visibility, to_visibility, user_pk FROM visibility_events "
            "ORDER BY pk"
        )
    )
    admin = call(api, "GET", "/p/runs/r1").json()["owner"]
    assert [tuple(e) for e in events] == [
        ("visibility", "private", "public", admin),
        ("visibility", "public", "private", admin),
    ]
    # A rename keeps the visibility; the old name is gone.
    publish(api)
    assert api.patch(f"{P}/p", {"name": "p2"}).json()["visibility"] == "public"
    assert call(outsider, "GET", "/p2").status == 200
    assert call(outsider, "GET", "/p").status == 404


def test_creators_change_visibility_and_nothing_else(
    setup: tuple[Client, Client, Client, Client], flock: FlockServer
) -> None:
    api, member, outsider, _ = setup
    other = flock.add_user("n@x.io").api
    # A project created in the dashboard, and one created by a run's upload (D118).
    assert member.post("/api/v1/projects", {"entity": "flock", "name": "m"}).status == 201
    member.create_run("q", "r1")
    me = member.get("/api/v1/me").json()["id"]
    assert call(api, "GET", "/m").json()["created_by"] == me
    assert call(api, "GET", "/q").json()["created_by"] == me
    assert call(api, "GET", "/p").json()["created_by"] != me
    for project in ("m", "q"):
        published = publish(member, project)
        assert (published["visibility"], published["access"]) == ("public", "member")
        assert call(outsider, "GET", f"/{project}").status == 200
        assert other.patch(f"{P}/{project}", {"visibility": "private"}).status == 403
    # Renames and descriptions stay with admins, even alongside a visibility change.
    assert member.patch(f"{P}/m", {"description": "x"}).status == 403
    assert member.patch(f"{P}/m", {"visibility": "private", "name": "m2"}).status == 403
    assert call(api, "GET", "/m").json()["visibility"] == "public"
    assert publish(member, "m", visibility="private")["visibility"] == "private"
    assert publish(api, "m")["visibility"] == "public"


def test_going_private_ends_outsiders_streams_only(
    setup: tuple[Client, Client, Client, Client],
    flock: FlockServer,
    events: Callable[..., EventStream],
) -> None:
    api, member, outsider, anon = setup
    api.create_run("p", "live")
    publish(api)
    outside = events("p", "live:0", client=anon)
    inside = events("p", "live:0", client=member)
    for stream in (outside, inside):
        assert stream.next_of("hello")["runs"]["live"]["lines"] == 0
    publish(api, visibility="private")
    assert outside.next_of("project") == {"visibility": "private"}
    with pytest.raises(AssertionError, match="stream ended"):
        outside.next()
    # Members aren't told: their dashboards would take it for a rename and leave.
    api.stream("p", "live", history=[{"_step": 0, "loss": 2.0}])
    while (event := inside.next())[0] != "history":
        assert event[0] != "project", event
    assert event[1]["series"]["loss"]["y"] == [2.0]
    # Outsiders can't reconnect.
    resp = EventStream(outsider, f"{P}/p/stream?runs=live").resp
    assert resp.status == 404


def test_outsiders_see_an_org_only_with_public_things(
    setup: tuple[Client, Client, Client, Client],
) -> None:
    api, member, outsider, anon = setup
    for client in (outsider, anon):
        assert client.get("/api/v1/orgs/flock").status == 404
        assert client.get("/api/v1/orgs/nope").status == 404
        assert client.get("/api/v1/projects", entity="flock").json() == {"projects": []}
    api.post("/api/v1/projects", {"entity": "flock", "name": "hidden"})
    publish(api)
    for client in (outsider, anon):
        org = client.get("/api/v1/orgs/flock").json()
        assert org == {"name": "flock", "display_name": "flock", "avatar_url": None, "role": None}
        listed = client.get("/api/v1/projects", entity="flock").json()["projects"]
        assert [p["name"] for p in listed] == ["p"]
        for route in ("members", "invites", "budget", "billing", "budget/deleted-projects"):
            assert client.get(f"/api/v1/orgs/flock/{route}").status in (401, 404), route
    assert member.get("/api/v1/orgs/flock").json()["role"] == "member"
    assert member.get("/api/v1/orgs/flock").json()["member_count"] == 2
    # Without entity, only the viewer's own orgs' projects: never others' public ones.
    assert outsider.get("/api/v1/projects").json() == {"projects": []}
    assert anon.get("/api/v1/projects").json() == {"projects": []}
    listed = member.get("/api/v1/projects").json()["projects"]
    assert {p["name"] for p in listed} == {"p", "hidden"}


def test_runs_moved_into_a_public_project_become_readable(
    setup: tuple[Client, Client, Client, Client],
) -> None:
    api, _, _, anon = setup
    api.create_run("q", "r2")
    api.stream("q", "r2", complete=0)
    publish(api)
    assert call(anon, "GET", "/p/runs/r2").status == 404
    moved = api.post(f"{P}/q/runs/move", {"runs": ["r2"], "to": "p"})
    assert moved.status == 200, moved.body
    assert call(anon, "GET", "/p/runs/r2").status == 200
    assert anon.runs("p")["total"] == 2


def test_removed_creator_cannot_change_visibility_or_keep_private_stream(
    setup: tuple[Client, Client, Client, Client], events: Callable[..., EventStream]
) -> None:
    api, member, _, _ = setup
    member.create_run("mine", "live")
    stream = events("mine", "live:0", client=member)
    remaining = events("mine", "live:0", client=api)
    assert remaining.next_of("hello")["runs"]["live"]["lines"] == 0
    assert stream.next_of("hello")["runs"]["live"]["lines"] == 0
    user_id = member.get("/api/v1/me").json()["id"]
    assert api.delete(f"/api/v1/orgs/flock/members/{user_id}").status == 204
    assert member.patch(f"{P}/mine", {"visibility": "public"}).status == 404
    api.stream("mine", "live", history=[{"_step": 0, "loss": 2.0}])
    with pytest.raises(AssertionError, match="stream ended"):
        stream.next_of("history")
    with pytest.raises(AssertionError, match="stream ended"):
        remaining.next_of("history")
    reconnected = events("mine", "live:0", client=api)
    assert reconnected.next_of("history")["series"]["loss"]["y"] == [2.0]


def test_member_removal_serializes_new_projects(
    flock: FlockServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.src.core import accounts

    member = flock.add_user("removed@x.io")
    user_id = member.api.get("/api/v1/me").json()["id"]

    async def check() -> None:
        reached = asyncio.Event()
        release = asyncio.Event()
        original = accounts.publish

        async def paused_publish(conn: Any, messages: Any) -> None:
            reached.set()
            await release.wait()
            await original(conn, messages)

        monkeypatch.setattr(accounts, "publish", paused_publish)
        store = flock.store
        org = await store.org("flock")
        user = await store.accounts.user(user_id)
        assert user is not None
        removal = asyncio.create_task(store.accounts.remove_member(user, org))
        await asyncio.wait_for(reached.wait(), 5)
        creation = asyncio.create_task(store.create_project("flock", "concurrent"))
        try:
            # Project creation must wait until the removal's project snapshot is committed.
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(creation), 0.2)
        finally:
            release.set()
            await removal
            await creation
        assert store.accounts.role(await store.accounts.user(user_id), org) is None

    flock.call(check())


def test_project_content_and_refusals_are_not_cached(
    setup: tuple[Client, Client, Client, Client],
) -> None:
    api, member, outsider, anon = setup
    for visibility in ("private", "public", "private"):
        publish(api, visibility=visibility)
        for client in (api, member, outsider, anon):
            for method, path, body in READABLE:
                response = call(client, method, path, body)
                expected = 200 if client in (api, member) or visibility == "public" else 404
                assert response.status == expected
                assert response.headers["cache-control"] == "private, no-store"


def test_public_projects_do_not_open_org_administration_or_imports(
    setup: tuple[Client, Client, Client, Client],
) -> None:
    api, _, outsider, anon = setup
    publish(api)
    base = "/api/v1/orgs/flock"
    for suffix in (
        "/members",
        "/invites",
        "/billing",
        "/budget",
        "/budget/warnings",
        "/budget/deleted-projects",
        "/imports/hf",
    ):
        assert api.get(base + suffix).status == 200, suffix
        assert outsider.get(base + suffix).status == 404, suffix
        assert anon.get(base + suffix).status == 401, suffix
    for method, suffix, body in (
        ("GET", "/import", None),
        ("GET", "/imports/hf/preview?kind=dataset&repo=org/data", None),
        ("POST", "/imports/hf", {"kind": "dataset", "repo": "org/data", "dest": "private"}),
        ("GET", "/imports/hf/1", None),
        ("DELETE", "/imports/hf/1", None),
        ("PATCH", "", {"display_name": "changed"}),
        ("POST", "/invites", {"email": "stranger@example.test", "role": "member"}),
    ):
        for client, expected in ((outsider, 404), (anon, 401)):
            response = client.request(method, base + suffix, body=body)
            assert response.status == expected, (method, suffix, response.body)
    assert api.get(base).json()["name"] == "flock"


# Frozen route inventory: additions need a deliberate authorization expectation.
ORG_SCOPED_ROUTES = [
    ("DELETE", "/api/v1/orgs/flock"),
    ("DELETE", "/api/v1/orgs/flock/avatar"),
    ("DELETE", "/api/v1/orgs/flock/buckets/private"),
    ("DELETE", "/api/v1/orgs/flock/budget/warnings/1/dismissal"),
    ("DELETE", "/api/v1/orgs/flock/datasets/public"),
    ("DELETE", "/api/v1/orgs/flock/datasets/tags/private"),
    ("DELETE", "/api/v1/orgs/flock/datasets/trash/1"),
    ("DELETE", "/api/v1/orgs/flock/import"),
    ("DELETE", "/api/v1/orgs/flock/imports/hf/1"),
    ("DELETE", "/api/v1/orgs/flock/invites/private"),
    ("DELETE", "/api/v1/orgs/flock/members/1"),
    ("DELETE", "/api/v1/orgs/flock/models/-/trash/models/1"),
    ("DELETE", "/api/v1/orgs/flock/models/-/trash/versions/1"),
    ("DELETE", "/api/v1/orgs/flock/models/private"),
    ("DELETE", "/api/v1/orgs/flock/models/private/tags/private"),
    ("DELETE", "/api/v1/orgs/flock/models/private/uploads/private"),
    ("DELETE", "/api/v1/orgs/flock/models/private/versions/1"),
    ("GET", "/api/v1/orgs/flock/billing"),
    ("GET", "/api/v1/orgs/flock/buckets"),
    ("GET", "/api/v1/orgs/flock/buckets/private"),
    ("GET", "/api/v1/orgs/flock/buckets/private/object"),
    ("GET", "/api/v1/orgs/flock/buckets/private/objects"),
    ("GET", "/api/v1/orgs/flock/budget"),
    ("GET", "/api/v1/orgs/flock/budget/deleted-projects"),
    ("GET", "/api/v1/orgs/flock/budget/warnings"),
    ("GET", "/api/v1/orgs/flock/datasets/at"),
    ("GET", "/api/v1/orgs/flock/datasets/trash"),
    ("GET", "/api/v1/orgs/flock/import"),
    ("GET", "/api/v1/orgs/flock/imports/hf"),
    ("GET", "/api/v1/orgs/flock/imports/hf/1"),
    ("GET", "/api/v1/orgs/flock/imports/hf/preview"),
    ("GET", "/api/v1/orgs/flock/invites"),
    ("GET", "/api/v1/orgs/flock/members"),
    ("GET", "/api/v1/orgs/flock/models"),
    ("GET", "/api/v1/orgs/flock/models/-/trash"),
    ("GET", "/api/v1/orgs/flock/models/private"),
    ("GET", "/api/v1/orgs/flock/models/private/resolve"),
    ("GET", "/api/v1/orgs/flock/models/private/uploads"),
    ("GET", "/api/v1/orgs/flock/models/private/uploads/private"),
    ("GET", "/api/v1/orgs/flock/models/private/versions"),
    ("GET", "/api/v1/orgs/flock/models/private/versions/1"),
    ("PATCH", "/api/v1/orgs/flock"),
    ("PATCH", "/api/v1/orgs/flock/billing"),
    ("PATCH", "/api/v1/orgs/flock/budget/deleted-projects/1"),
    ("PATCH", "/api/v1/orgs/flock/members/1"),
    ("PATCH", "/api/v1/orgs/flock/models/private"),
    ("POST", "/api/v1/orgs/flock/buckets"),
    ("POST", "/api/v1/orgs/flock/buckets/private/folders"),
    ("POST", "/api/v1/orgs/flock/buckets/private/objects/delete"),
    ("POST", "/api/v1/orgs/flock/buckets/private/presign"),
    ("POST", "/api/v1/orgs/flock/datasets/delete"),
    ("POST", "/api/v1/orgs/flock/datasets/move"),
    ("POST", "/api/v1/orgs/flock/datasets/rename"),
    ("POST", "/api/v1/orgs/flock/datasets/trash/1/restore"),
    ("POST", "/api/v1/orgs/flock/import"),
    ("POST", "/api/v1/orgs/flock/imports/hf"),
    ("POST", "/api/v1/orgs/flock/invites"),
    ("POST", "/api/v1/orgs/flock/models/-/trash/models/1/restore"),
    ("POST", "/api/v1/orgs/flock/models/-/trash/versions/1/restore"),
    ("POST", "/api/v1/orgs/flock/models/private/uploads"),
    ("POST", "/api/v1/orgs/flock/models/private/uploads/private/commit"),
    ("PUT", "/api/v1/orgs/flock/avatar"),
    ("PUT", "/api/v1/orgs/flock/budget/warnings/1/dismissal"),
    ("PUT", "/api/v1/orgs/flock/datasets/public"),
    ("PUT", "/api/v1/orgs/flock/datasets/tags/private"),
    ("PUT", "/api/v1/orgs/flock/models/private/primary"),
    ("PUT", "/api/v1/orgs/flock/models/private/tags/private"),
]


def test_org_scoped_route_boundaries_refuse_outsiders(
    setup: tuple[Client, Client, Client, Client],
) -> None:
    api, _, outsider, anon = setup
    publish(api)
    for method, path in ORG_SCOPED_ROUTES:
        for client, expected in ((anon, (401,)), (outsider, (403, 404))):
            response = client.request(
                method, path, body={} if method in ("POST", "PATCH", "PUT") else None
            )
            assert response.status in expected, (method, path, response.status, response.body)
            if response.status == 404:
                assert response.json()["error"] == "organization flock not found", (
                    method,
                    path,
                    response.body,
                )
