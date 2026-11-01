from tests.app.utils import history_rows

ORGS = "/api/v1/orgs"
UPSERT = """
mutation UpsertBucket($name: String, $project: String, $entity: String, $notes: String) {
  upsertBucket(input: {name: $name, modelName: $project, entityName: $entity, notes: $notes}) {
    bucket { id name }
    inserted
  }
}
"""


def upsert_error(client, entity: str | None, project: str, run: str) -> str | None:
    """The GraphQL error of an SDK-style UpsertBucket, or None if it succeeded."""
    variables = {"entity": entity, "project": project, "name": run, "notes": "x"}
    body = client.graphql(UPSERT, variables).json()
    return body["errors"][0]["message"] if "errors" in body else None


# ---------------------------------------------------------------------- organizations


def test_org_crud(flock):
    admin = flock.client()
    created = admin.post(ORGS, {"name": "lab"})
    assert created.status == 201
    assert created.json() | {"created_at": 0, "updated_at": 0} == {
        "name": "lab",
        "display_name": "lab",  # the ID, when none is given
        "created_at": 0,
        "updated_at": 0,
        "project_count": 0,
        "member_count": 1,  # its creator, as admin
        "role": "admin",
        "avatar_url": None,
        "s3_ephemeral_ttl": 8 * 3600,  # S3 key lifetimes (D56)
        "s3_key_max_ttl": 30 * 86400,
    }
    assert admin.post(ORGS, {"name": "lab"}).status == 409
    assert admin.post(ORGS, {"name": "a/b"}).status == 422
    alice = flock.add_user("alice@lab.test")  # a member of flock only
    assert alice.api.post(ORGS, {"name": "mine"}).status == 403  # super admins only
    assert [o["name"] for o in alice.api.get(ORGS).json()["orgs"]] == ["flock"]
    assert [o["name"] for o in admin.get(ORGS).json()["orgs"]] == ["flock", "lab"]
    assert alice.api.get(f"{ORGS}/lab").status == 404  # not a member: as if it didn't exist
    assert alice.api.patch(f"{ORGS}/flock", {"display_name": "x"}).status == 403
    assert alice.api.delete(f"{ORGS}/flock").status == 403


def test_org_display_names(make_flock, events):
    server = make_flock()
    api = server.client()
    created = api.post(ORGS, {"name": "cerulean-labs", "display_name": "  Cerulean Labs "})
    assert created.status == 201
    assert (created.json()["name"], created.json()["display_name"]) == (
        "cerulean-labs",
        "Cerulean Labs",  # without the surrounding spaces
    )
    # Display names are free text (and needn't be unique); IDs keep the project-name rule.
    assert api.post(ORGS, {"name": "acme", "display_name": "R&D / Vision #2?"}).status == 201
    assert api.post(ORGS, {"name": "other", "display_name": "Cerulean Labs"}).status == 201
    for bad in ("", "   ", "tab\there", "x" * 129):
        assert api.post(ORGS, {"name": "bad", "display_name": bad}).status == 422, bad
        assert api.patch(f"{ORGS}/acme", {"display_name": bad}).status == 422, bad
    assert api.post(ORGS, {"name": "a/b", "display_name": "Fine"}).status == 422

    # Lists are sorted by what people see; the ID breaks ties.
    orgs = api.get(ORGS).json()["orgs"]
    assert [(o["display_name"], o["name"]) for o in orgs] == [
        ("Cerulean Labs", "cerulean-labs"),
        ("Cerulean Labs", "other"),
        ("flock", "flock"),
        ("R&D / Vision #2?", "acme"),
    ]
    me = api.get("/api/v1/me").json()
    assert me["orgs"][0] == {
        "name": "cerulean-labs",
        "display_name": "Cerulean Labs",
        "role": "admin",
    }

    # Changing it is fine while runs are active: clients address the org by its ID.
    api.create_run("p", "r1")
    live = events("p", "r1", client=api)
    live.next_of("hello")
    changed = api.patch(f"{ORGS}/flock", {"display_name": "Flock Research"})
    assert changed.status == 200
    assert (changed.json()["name"], changed.json()["display_name"]) == ("flock", "Flock Research")
    api.stream("p", "r1", history_rows(2, loss=float))
    assert live.next_of("history")["run"] == "r1"  # the stream goes on
    assert upsert_error(api, "flock", "p", "r2") is None

    # The ID can't change: sent back as it is, it's accepted; anything else is refused.
    assert api.patch(f"{ORGS}/flock", {"name": "flock", "display_name": "Flock"}).status == 200
    refused = api.patch(f"{ORGS}/flock", {"name": "acme2"})
    assert refused.status == 400
    assert "can't be changed" in refused.json()["error"]
    assert api.patch(f"{ORGS}/flock", {}).json()["display_name"] == "Flock"

    # Invitations name the org as people know it.
    invite = api.post(f"{ORGS}/flock/invites", {"email": "ben@lab.test"}).json()
    details = server.anonymous().get(f"/api/v1/invites/{invite['token']}").json()
    assert (details["org"], details["org_display_name"]) == ("flock", "Flock")

    def check(client) -> None:
        org = client.get(f"{ORGS}/flock").json()
        assert (org["name"], org["display_name"]) == ("flock", "Flock")
        assert client.get(f"{ORGS}/acme2").status == 404
        assert client.get("/api/v1/projects/flock/p").json()["run_count"] == 2

    check(api)
    server.stop()
    check(make_flock(reuse=server).client())


def test_only_org_admins_change_display_names(flock):
    admin = flock.client()
    alice = flock.add_user("alice@lab.test")  # a member
    bob = flock.add_user("bob@lab.test", role="admin")
    assert alice.api.patch(f"{ORGS}/flock", {"display_name": "Alice's"}).status == 403
    assert alice.api.get(f"{ORGS}/flock").json()["display_name"] == "flock"
    assert bob.api.patch(f"{ORGS}/flock", {"display_name": "Bob's"}).status == 200
    assert admin.post(ORGS, {"name": "lab", "display_name": "Lab"}).status == 201
    # Not a member: as if it didn't exist.
    assert bob.api.patch(f"{ORGS}/lab", {"display_name": "Mine"}).status == 404
    # Super admins administer every org.
    assert admin.patch(f"{ORGS}/flock", {"display_name": "Flock"}).status == 200
    assert alice.api.get("/api/v1/me").json()["orgs"] == [
        {"name": "flock", "display_name": "Flock", "role": "member"}
    ]


def test_delete_org(make_flock, notification, wait_for):
    server = make_flock()
    store = server.store
    api = server.client()
    assert api.post(ORGS, {"name": "lab"}).status == 201
    alice = server.add_user("alice@lab.test", org="lab")
    assert alice.api.patch("/api/v1/me", {"default_org": "lab"}).status == 200
    assert upsert_error(alice.api, "lab", "p", "a1") is None  # still running
    run = server.call(store.get_run("lab", "p", "a1"))
    server.call(store.save_file(run, "config.yaml", b"x"))
    api.create_run("kept", "r1")

    refused = api.delete(f"{ORGS}/lab")
    assert refused.status == 409
    assert refused.json()["ids"] == ["p/a1"]
    with notification(store.bus, server.loop, "flock_auth", "orgs"):
        assert api.delete(f"{ORGS}/lab", force="true").status == 204
    server.call(store.flush())  # the purge removes the rows and files in the background (D80)
    # Another background purge can already own the rows when flush finds no work.
    wait_for(lambda: not (server.files_dir / str(run.pk)).exists())

    def check(server) -> None:
        client = server.client()
        assert client.get(f"{ORGS}/lab").status == 404
        assert client.get("/api/v1/projects/lab/p").status == 404
        assert [p["name"] for p in client.get("/api/v1/projects").json()["projects"]] == ["kept"]
        me = server.client(token=alice.token).get("/api/v1/me").json()
        assert (me["orgs"], me["default_org"]) == ([], None)

    check(server)
    server.stop()
    check(make_flock(reuse=server))


# ---------------------------------------------------------------------- invites and members


def test_invites(make_flock):
    server = make_flock(web_url="https://ui.test/", api_public_url="https://api.test/")
    admin = server.client()
    anon = server.anonymous()
    invites = f"{ORGS}/flock/invites"

    made = admin.post(invites, {"email": "Carol@Lab.test", "role": "admin"})
    assert made.status == 201
    invite = made.json()
    assert invite["url"] == f"https://ui.test/invite/{invite['token']}"
    assert (invite["email"], invite["role"]) == ("carol@lab.test", "admin")
    link = f"/api/v1/invites/{invite['token']}"
    assert anon.get(link).json() | {"expires_at": 0} == {
        "org": "flock",
        "org_display_name": "flock",
        "org_avatar_url": None,
        "email": "carol@lab.test",
        "role": "admin",
        "expires_at": 0,
        "account_exists": False,
    }
    assert [i["email"] for i in admin.get(invites).json()["invites"]] == ["carol@lab.test"]
    assert anon.post(f"{link}/accept", {}).status == 401  # neither a new account nor signed in
    accepted = anon.post(f"{link}/accept", {"name": "Carol", "password": "carolcarol"})
    assert accepted.status == 201
    assert accepted.json()["user"]["orgs"] == [
        {"name": "flock", "display_name": "flock", "role": "admin"}
    ]
    assert anon.get(link).status == 404  # used up
    reused = anon.post(f"{link}/accept", {"name": "Eve", "password": "evilevil"})
    assert reused.status == 404
    assert admin.get(invites).json()["invites"] == []
    assert admin.post(invites, {"email": "carol@lab.test"}).status == 409  # already a member
    assert admin.post(invites, {"email": "not an email"}).status == 422

    # An existing account joins another org by accepting signed in as the invited email.
    assert admin.post(ORGS, {"name": "lab"}).status == 201
    bob = server.add_user("bob@lab.test")
    token = admin.post(f"{ORGS}/lab/invites", {"email": "bob@lab.test"}).json()["token"]
    assert anon.get(f"/api/v1/invites/{token}").json()["account_exists"] is True
    taken = anon.post(f"/api/v1/invites/{token}/accept", {"name": "Bob", "password": "bobbybob"})
    assert taken.status == 409  # sign in instead
    carol = server.client(token=accepted.json()["access_token"])
    assert carol.post(f"/api/v1/invites/{token}/accept", {}).status == 403  # not carol's
    assert bob.api.post(f"/api/v1/invites/{token}/accept", {}).status == 200
    orgs = bob.api.get("/api/v1/me").json()["orgs"]
    assert orgs == [
        {"name": "flock", "display_name": "flock", "role": "member"},
        {"name": "lab", "display_name": "lab", "role": "member"},
    ]

    withdrawn = admin.post(invites, {"email": "dave@lab.test"}).json()
    assert carol.delete(f"{invites}/{withdrawn['id']}").status == 204  # carol is an admin
    assert anon.get(f"/api/v1/invites/{withdrawn['token']}").status == 404
    assert bob.api.post(invites, {"email": "eve@lab.test"}).status == 403  # members can't


def test_invites_expire(make_flock):
    server = make_flock(invite_ttl=60)
    token = server.client().post(f"{ORGS}/flock/invites", {"email": "x@lab.test"}).json()["token"]
    assert server.anonymous().get(f"/api/v1/invites/{token}").status == 200
    server.call(
        server.store.db.execute("UPDATE invites SET expires_at = now() - interval '1 second'")
    )
    assert server.anonymous().get(f"/api/v1/invites/{token}").status == 404


def test_roles_and_leaving(flock):
    admin = flock.client()
    members = f"{ORGS}/flock/members"
    alice = flock.add_user("alice@lab.test")
    bob = flock.add_user("bob@lab.test")
    listed = admin.get(members).json()["members"]
    assert [(m["email"], m["role"]) for m in listed] == [
        ("admin@flock.test", "admin"),
        ("alice@lab.test", "member"),
        ("bob@lab.test", "member"),
    ]
    assert bob.api.get(members).status == 200  # members see each other
    assert bob.api.patch(f"{members}/{alice.id}", {"role": "admin"}).status == 403
    assert admin.patch(f"{members}/{alice.id}", {"role": "owner"}).status == 422
    assert admin.patch(f"{members}/{alice.id}", {"role": "admin"}).status == 200
    admin_id = admin.get("/api/v1/me").json()["id"]
    assert alice.api.patch(f"{members}/{admin_id}", {"role": "member"}).status == 200
    # alice is now the only admin: she can't step down or leave, until someone else is admin.
    assert alice.api.patch(f"{members}/{alice.id}", {"role": "member"}).status == 409
    assert alice.api.delete(f"{members}/{alice.id}").status == 409
    assert bob.api.delete(f"{members}/{alice.id}").status == 403
    assert bob.api.delete(f"{members}/{bob.id}").status == 204  # leaving
    assert bob.api.get("/api/v1/projects/flock/anything").status == 404
    assert alice.api.delete(f"{members}/{bob.id}").status == 404  # not a member any more


# ---------------------------------------------------------------------- permissions


def test_members_change_only_their_own_runs(flock):
    admin = flock.client()
    assert admin.post(ORGS, {"name": "lab"}).status == 201
    alice = flock.add_user("alice@lab.test")
    bob = flock.add_user("bob@lab.test")
    carol = flock.add_user("carol@lab.test", role="admin")
    dave = flock.add_user("dave@lab.test", org="lab")
    for user, run in ((alice, "a1"), (alice, "a2"), (bob, "b1")):
        assert upsert_error(user.api, "flock", "p", run) is None
        user.api.stream("p", run, history_rows(2, loss=float), complete=0)
    runs = "/api/v1/projects/flock/p/runs"
    owners = admin.runs("p", columns="owner", sort="name")["data"]["owner"]
    assert owners == [alice.id, alice.id, bob.id]

    # Members read everything in their org, and change only what they logged.
    assert alice.api.runs("p")["total"] == 3
    assert alice.api.patch(f"{runs}/a1", {"notes": "mine"}).status == 200
    denied = alice.api.patch(f"{runs}/b1", {"notes": "bob's"})
    assert (denied.status, denied.json()["ids"]) == (403, ["b1"])
    assert alice.api.delete(f"{runs}/b1").status == 403
    bulk = alice.api.post(f"{runs}/delete", {"all": True})
    assert (bulk.status, bulk.json()["ids"]) == (403, ["b1"])
    moved = alice.api.post(f"{runs}/move", {"runs": ["a2", "b1"], "to": "q", "create": True})
    assert (moved.status, moved.json()["ids"]) == (403, ["b1"])
    mine = alice.api.post(f"{runs}/delete", {"filter": {"owner": alice.id}, "dry_run": True})
    assert sorted(mine.json()["ids"]) == ["a1", "a2"]
    assert alice.api.post(f"{runs}/move", {"runs": ["a2"], "to": "q", "create": True}).status == 200
    assert alice.api.delete("/api/v1/projects/flock/q/runs/a2").status == 204

    # ...and so do SDK writes: a member can't resume someone else's run.
    assert "another user" in (upsert_error(alice.api, "flock", "p", "b1") or "")
    stream = alice.api.file_stream("flock", "p", "b1", {"files": {}})
    assert stream.status == 403
    assert "not found" in (upsert_error(dave.api, "flock", "p", "d1") or "")  # not dave's org
    assert dave.api.file_stream("flock", "p", "b1", {"files": {}}).status == 404

    # Members create and read projects; admins update and delete them, and any run.
    assert alice.api.post("/api/v1/projects", {"entity": "flock", "name": "new"}).status == 201
    assert alice.api.post("/api/v1/projects", {"entity": "lab", "name": "new"}).status == 404
    assert alice.api.patch("/api/v1/projects/flock/p", {"description": "x"}).status == 403
    assert alice.api.delete("/api/v1/projects/flock/new").status == 403
    assert carol.api.patch(f"{runs}/b1", {"notes": "admin edit"}).status == 200
    assert upsert_error(carol.api, "flock", "p", "b1") is None  # admins may write any run
    assert carol.api.delete(f"{runs}/b1").status == 204
    assert carol.api.delete("/api/v1/projects/flock/new").status == 204

    # Outsiders see nothing.
    assert dave.api.get("/api/v1/projects/flock/p").status == 404
    assert dave.api.get(f"{runs}/a1").status == 404
    assert dave.api.get("/api/v1/projects/flock/p/workspace").status == 404
    assert dave.api.get("/api/v1/projects").json()["projects"] == []
    assert dave.api.get("/api/v1/projects", entity="flock").json()["projects"] == []


def test_password_resets(flock):
    admin = flock.client()
    assert admin.post(ORGS, {"name": "lab"}).status == 201
    alice = flock.add_user("alice@lab.test")
    carol = flock.add_user("carol@lab.test", role="admin")  # admin of flock
    dave = flock.add_user("dave@lab.test", org="lab", role="admin")  # admin of lab only
    session = flock.client(token=alice.signup["access_token"])
    admin_id = admin.get("/api/v1/me").json()["id"]

    def reset(actor, user_id: int):
        return actor.post(f"/api/v1/users/{user_id}/password-reset", {})

    assert reset(alice.api, carol.id).status == 403  # members can't
    assert reset(carol.api, admin_id).status == 403  # nor can org admins reset super admins
    assert reset(dave.api, alice.id).status == 404  # alice isn't in dave's org
    made = reset(carol.api, alice.id)
    assert made.status == 201
    token = made.json()["url"].rpartition("/")[2]
    anon = flock.anonymous()
    assert anon.get(f"/api/v1/password-resets/{token}").json() == {"email": alice.email}
    assert anon.post(f"/api/v1/password-resets/{token}", {"password": "short"}).status == 422
    done = anon.post(f"/api/v1/password-resets/{token}", {"password": "brand new password"})
    assert done.status == 200
    assert done.json()["user"]["id"] == alice.id
    assert session.get("/api/v1/me").status == 401  # every earlier session ended
    assert anon.post(f"/api/v1/password-resets/{token}", {"password": "again again"}).status == 404
    login = {"email": alice.email, "password": "brand new password"}
    assert anon.post("/api/v1/auth/login", login).status == 200
    assert reset(admin, dave.id).status == 201  # super admins reset anyone


def test_user_administration(flock):
    admin = flock.client()
    admin_id = admin.get("/api/v1/me").json()["id"]
    alice = flock.add_user("alice@lab.test")
    assert upsert_error(alice.api, "flock", "p", "a1") is None
    assert alice.api.get("/api/v1/users").status == 403
    emails = [u["email"] for u in admin.get("/api/v1/users").json()["users"]]
    assert emails == ["admin@flock.test", "alice@lab.test"]

    assert admin.patch(f"/api/v1/users/{admin_id}", {"is_superadmin": False}).status == 409
    promoted = admin.patch(f"/api/v1/users/{alice.id}", {"is_superadmin": True}).json()
    assert promoted["is_superadmin"] is True
    assert alice.api.post(ORGS, {"name": "alices"}).status == 201
    assert alice.api.patch(f"/api/v1/users/{admin_id}", {"is_superadmin": False}).status == 200
    assert admin.get("/api/v1/users").status == 403

    assert alice.api.delete(f"/api/v1/users/{alice.id}").status == 409  # the last super admin
    assert alice.api.delete(f"/api/v1/users/{admin_id}").status == 204
    assert admin.get("/api/v1/me").status == 401
    flock.token = alice.token  # add_user invites as the super admin
    bob = flock.add_user("bob@lab.test", org="alices")
    assert upsert_error(bob.api, "alices", "p", "b1") is None
    assert alice.api.delete(f"/api/v1/users/{bob.id}").status == 204
    detail = alice.api.get("/api/v1/projects/alices/p/runs/b1").json()
    assert detail["owner"] is None  # runs stay, without an owner
    assert bob.api.get("/api/v1/me").status == 401
