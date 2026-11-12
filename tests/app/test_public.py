"""Public projects (015): read-only access for anyone with the link, on projects an org admin
made public."""

from __future__ import annotations

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
