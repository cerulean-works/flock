"""Public projects (015): read-only access for anyone with the link, on projects an org admin
made public."""

import pytest

from tests.app.conftest import Client, EventStream, FlockServer

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


def call(client: Client, method: str, path: str, body=None):
    return client.request(method, P + path, body=body)


@pytest.fixture
def setup(flock: FlockServer):
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


def test_outsiders_read_public_projects_and_get_404_on_private_ones(setup):
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


def test_members_only_routes_refuse_outsiders_on_public_projects(setup):
    api, _, outsider, anon = setup
    publish(api)
    for method, path, body in MEMBERS_ONLY:
        assert call(anon, method, path, body).status == 401, (method, path)
        assert call(outsider, method, path, body).status == 404, (method, path)
    assert anon.post("/api/v1/projects", {"entity": "flock", "name": "new"}).status == 401
    assert outsider.post("/api/v1/projects", {"entity": "flock", "name": "new"}).status == 404
    assert call(api, "GET", "/p/runs/r1").status == 200  # nothing was changed or deleted


def test_bad_credentials_are_401_not_the_public_view(setup, flock):
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


def test_outsiders_see_no_cost_or_email(setup):
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


def test_only_admins_change_visibility(setup, flock):
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


def test_going_private_ends_outsiders_streams_only(setup, flock, events):
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


def test_outsiders_see_an_org_only_with_public_things(setup):
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


def test_runs_moved_into_a_public_project_become_readable(setup):
    api, _, _, anon = setup
    api.create_run("q", "r2")
    api.stream("q", "r2", complete=0)
    publish(api)
    assert call(anon, "GET", "/p/runs/r2").status == 404
    moved = api.post(f"{P}/q/runs/move", {"runs": ["r2"], "to": "p"})
    assert moved.status == 200, moved.body
    assert call(anon, "GET", "/p/runs/r2").status == 200
    assert anon.runs("p")["total"] == 2
