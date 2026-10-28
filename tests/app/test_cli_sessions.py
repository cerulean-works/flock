"""CLI sessions: the credential ``flock login`` ends with."""

import time

from tests.app.utils import cli_login

ME = "/api/v1/me"
SESSIONS = "/api/v1/me/cli-sessions"
VIEWER = "query Viewer { viewer { id username entity } }"


def browser(server, user):
    return server.client(token=user.signup["access_token"])


def db_session(server, pk: int):
    return server.call(
        server.store.db.fetchrow(
            "SELECT kind, name, extract(epoch FROM last_used_at)::float8 AS last_used_at, "
            "extract(epoch FROM expires_at)::float8 AS expires_at FROM sessions WHERE pk = $1",
            pk,
        )
    )


def token_session(server, token: str) -> int:
    """The pk of the session a CLI token belongs to."""
    digest = server.store.accounts.digest(token)
    query = "SELECT pk FROM sessions WHERE refresh_hash = $1"
    return server.call(server.store.db.fetchval(query, digest))


def test_a_cli_token_authenticates_rest_and_the_wandb_protocol(flock):
    alice = flock.add_user("alice@lab.test")
    cli = flock.client(token=cli_login(flock, browser(flock, alice)))
    assert cli.get(ME).json()["email"] == alice.email
    assert cli.get("/api/v1/projects").status == 200
    viewer = cli.graphql(VIEWER)
    assert viewer.status == 200, viewer.body
    assert viewer.json()["data"]["viewer"]["username"] == alice.email
    assert flock.client(token="flock_cli_" + "x" * 43).get(ME).status == 401


def test_listing_and_revoking_cli_sessions(flock):
    alice = flock.add_user("alice@lab.test")
    bob = flock.add_user("bob@lab.test")
    laptop = flock.client(token=cli_login(flock, browser(flock, alice), "laptop"))
    node = flock.client(token=cli_login(flock, browser(flock, alice), "node-7"))
    cli_login(flock, browser(flock, bob), "bobs-laptop")

    before = time.time()
    assert laptop.get(ME).status == 200
    listed = alice.api.get(SESSIONS).json()["sessions"]
    assert [s["name"] for s in listed] == ["tests", "laptop", "node-7"]  # "tests": add_user's
    by_name = {s["name"]: s for s in listed}
    assert by_name["laptop"]["last_used_at"] >= before
    assert by_name["node-7"]["last_used_at"] is None  # never used
    assert abs(by_name["node-7"]["expires_at"] - by_name["node-7"]["created_at"] - 90 * 86400) < 5

    # Web sessions and other people's sign-ins aren't CLI sessions of this user.
    web = flock.call(flock.store.db.fetchval("SELECT pk FROM sessions WHERE kind = 'web'"))
    assert alice.api.delete(f"{SESSIONS}/{web}").status == 404
    assert alice.api.delete(f"{SESSIONS}/{by_name['laptop']['id'] + 2}").status == 404
    assert bob.api.delete(f"{SESSIONS}/{by_name['laptop']['id']}").status == 404

    assert browser(flock, alice).delete(f"{SESSIONS}/{by_name['laptop']['id']}").status == 204
    assert laptop.get(ME).status == 401
    assert node.get(ME).status == 200
    assert [s["name"] for s in alice.api.get(SESSIONS).json()["sessions"]] == ["tests", "node-7"]


def test_logging_out_with_a_cli_token_ends_its_session(flock, notification):
    alice = flock.add_user("alice@lab.test")
    cli = flock.client(token=cli_login(flock, browser(flock, alice)))
    with notification(flock.store.bus, flock.loop, "flock_auth", "sessions"):
        assert cli.post("/api/v1/auth/logout", None).status == 204
    assert cli.get(ME).status == 401
    assert [s["name"] for s in alice.api.get(SESSIONS).json()["sessions"]] == ["tests"]


def test_a_password_change_or_reset_ends_cli_sessions(flock):
    alice = flock.add_user("alice@lab.test")
    web = browser(flock, alice)
    cli = flock.client(token=cli_login(flock, web))
    changed = web.post("/api/v1/me/password", {"current": alice.password, "new": "new password"})
    assert changed.status == 204
    assert cli.get(ME).status == 401
    assert web.get(ME).status == 200
    assert alice.api.get(ME).status == 401  # add_user's CLI session ended too
    assert alice.api.graphql(VIEWER).status == 200  # wandb keys stay valid

    cli = flock.client(token=cli_login(flock, web))
    reset = flock.client().post(f"/api/v1/users/{alice.id}/password-reset", None).json()
    token = reset["url"].rpartition("/")[2]
    redeemed = flock.anonymous().post(f"/api/v1/password-resets/{token}", {"password": "third one"})
    assert redeemed.status == 200
    assert cli.get(ME).status == 401


def test_deleting_the_user_ends_cli_sessions(flock):
    alice = flock.add_user("alice@lab.test")
    cli = flock.client(token=cli_login(flock, browser(flock, alice)))
    assert flock.client().delete(f"/api/v1/users/{alice.id}").status == 204
    assert cli.get(ME).status == 401
    left = flock.call(
        flock.store.db.fetchval("SELECT count(*) FROM sessions WHERE user_pk = $1", alice.id)
    )
    assert left == 0


def test_cli_sessions_expire_when_idle(make_flock, clock):
    server = make_flock(cli_session_ttl=1.0)
    alice = server.add_user("alice@lab.test")
    cli = server.client(token=cli_login(server, browser(server, alice)))
    wall = clock("app.api.src.core.accounts")
    wall.advance(0.6)
    assert cli.get(ME).status == 200  # each use extends the session
    wall.advance(0.6)
    assert cli.get(ME).status == 200
    wall.advance(1.1)
    assert cli.get(ME).status == 401
    assert browser(server, alice).get(SESSIONS).json() == {"sessions": []}  # add_user's too


def test_use_is_written_at_most_once_an_interval(flock, wait_for):
    alice = flock.add_user("alice@lab.test")
    token = cli_login(flock, browser(flock, alice))
    cli = flock.client(token=token)
    pk = token_session(flock, token)
    created = db_session(flock, pk)
    assert (created["kind"], created["name"], created["last_used_at"]) == ("cli", "laptop", None)

    assert cli.get(ME).status == 200
    flock.call(flock.store.accounts.flush())
    assert db_session(flock, pk)["last_used_at"] is None  # within the hour

    flock.store.accounts.CLI_WRITE_INTERVAL = 0.0
    used_at = time.time()
    assert cli.get(ME).status == 200
    used = wait_for(lambda: db_session(flock, pk)["last_used_at"])
    assert abs(used - used_at) < 1
    assert db_session(flock, pk)["expires_at"] > created["expires_at"]


def test_cli_sessions_survive_a_restart(make_flock, wait_for):
    server = make_flock()
    alice = server.add_user("alice@lab.test")
    token = cli_login(server, browser(server, alice))
    server.store.accounts.CLI_WRITE_INTERVAL = 0.0
    used_at = time.time()
    assert server.client(token=token).get(ME).status == 200
    pk = token_session(server, token)
    wait_for(lambda: db_session(server, pk)["last_used_at"])
    server.stop()

    restarted = make_flock(reuse=server)
    tests, listed = restarted.client(token=alice.token).get(SESSIONS).json()["sessions"]
    assert (tests["name"], listed["name"]) == ("tests", "laptop")
    assert abs(listed["last_used_at"] - used_at) < 1
    assert restarted.client(token=token).get(ME).json()["id"] == alice.id


def test_refresh_refuses_cli_tokens(flock):
    alice = flock.add_user("alice@lab.test")
    token = cli_login(flock, browser(flock, alice))
    refused = flock.anonymous().post("/api/v1/auth/refresh", {"refresh_token": token})
    assert refused.status == 401
    assert flock.client(token=token).get(ME).status == 200
