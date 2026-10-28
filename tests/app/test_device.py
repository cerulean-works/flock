"""Device sign-in (RFC 8628), the flow behind ``flock login``."""

import re
import time

from tests.app.utils import cli_login

START = "/api/v1/auth/device"
TOKEN = "/api/v1/auth/device/token"
CLIENT = {"hostname": "node-7", "platform": "Linux x86_64"}


def start(server) -> dict:
    resp = server.anonymous().post(START, {"client": CLIENT})
    assert resp.status == 200, resp.body
    return resp.json()


def poll(server, started: dict):
    return server.anonymous().post(TOKEN, {"device_code": started["device_code"]})


def error(resp) -> str:
    assert resp.status == 400, resp.body
    return resp.json()["error"]


def browser(server, user):
    """A client on ``user``'s browser session (an access token), like the approval page."""
    return server.client(token=user.signup["access_token"])


def test_a_sign_in_waits_for_approval_and_gives_a_token_once(flock):
    alice = flock.add_user("alice@lab.test")
    started = start(flock)
    assert re.fullmatch(
        r"[BCDFGHJKLMNPQRSTVWXZ]{4}-[BCDFGHJKLMNPQRSTVWXZ]{4}", started["user_code"]
    )
    assert started["verification_uri"].endswith("/device")
    assert started["verification_uri_complete"] == (
        f"{started['verification_uri']}?code={started['user_code']}"
    )
    assert (started["expires_in"], started["interval"]) == (900, 5)
    assert error(poll(flock, started)) == "authorization_pending"

    # Typed in any case, with or without the dash.
    typed = started["user_code"].replace("-", "").lower()
    page = browser(flock, alice).get(f"{START}/{typed}")
    assert page.status == 200, page.body
    shown = page.json()
    assert shown | {"created_at": 0, "expires_at": 0} == {
        "user_code": started["user_code"],
        "hostname": "node-7",
        "platform": "Linux x86_64",
        "ip": "127.0.0.1",
        "created_at": 0,
        "expires_at": 0,
    }
    assert shown["expires_at"] - shown["created_at"] == 900
    approved = browser(flock, alice).post(f"{START}/{typed}", {"approve": True})
    assert approved.status == 204

    issued = poll(flock, started)
    assert issued.status == 200, issued.body
    body = issued.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == "alice@lab.test"
    token = body["access_token"]
    assert re.fullmatch(r"flock_cli_[\w-]{43}", token)
    assert flock.client(token=token).get("/api/v1/me").json()["id"] == alice.id

    # Used up: neither the CLI nor the browser can use the codes again.
    assert error(poll(flock, started)) == "expired_token"
    assert browser(flock, alice).post(f"{START}/{typed}", {"approve": True}).status == 404


def test_a_denied_sign_in(flock):
    alice = flock.add_user("alice@lab.test")
    started = start(flock)
    code = started["user_code"]
    assert browser(flock, alice).post(f"{START}/{code}", {"approve": False}).status == 204
    assert browser(flock, alice).get(f"{START}/{code}").status == 404  # decided
    assert error(poll(flock, started)) == "access_denied"
    assert error(poll(flock, started)) == "expired_token"
    listed = alice.api.get("/api/v1/me/cli-sessions").json()["sessions"]
    assert [s["name"] for s in listed] == ["tests"]  # add_user's only


def test_codes_expire(make_flock):
    server = make_flock(device_code_ttl=0.5)
    alice = server.add_user("alice@lab.test")
    started = start(server)
    assert started["expires_in"] == 0  # rounded
    time.sleep(0.6)
    assert browser(server, alice).get(f"{START}/{started['user_code']}").status == 404
    assert error(poll(server, started)) == "expired_token"
    assert error(server.anonymous().post(TOKEN, {"device_code": "unknown"})) == "expired_token"


def test_polling_too_often_slows_the_client_down(flock):
    started = start(flock)
    assert error(poll(flock, started)) == "authorization_pending"
    slowed = poll(flock, started)
    assert slowed.json()["error"] == "slow_down"
    assert slowed.json()["error_description"]
    assert error(poll(flock, started)) == "slow_down"
    interval = flock.call(flock.store.db.fetchval("SELECT poll_interval FROM device_grants"))
    assert interval == 15  # 5 more seconds per slow_down
    query = "UPDATE device_grants SET polled_at = polled_at - interval '15 seconds'"
    flock.call(flock.store.db.execute(query))
    assert error(poll(flock, started)) == "authorization_pending"


def test_only_a_browser_session_can_approve(flock):
    alice = flock.add_user("alice@lab.test")
    cli = flock.client(token=cli_login(flock, browser(flock, alice)))
    started = start(flock)
    code = started["user_code"]
    for client in (alice.api, cli, flock.anonymous()):
        assert client.get(f"{START}/{code}").status in (401, 403)
        assert client.post(f"{START}/{code}", {"approve": True}).status in (401, 403)
    assert alice.api.post(f"{START}/{code}", {"approve": True}).status == 403
    assert (
        cli.post(f"{START}/{code}", {"approve": True})
        .json()["error"]
        .startswith("only a signed-in browser")
    )
    assert error(poll(flock, started)) == "authorization_pending"


def test_wrong_codes_are_limited_per_user(flock):
    alice = flock.add_user("alice@lab.test")
    bob = flock.add_user("bob@lab.test")
    started = start(flock)
    for _ in range(10):
        assert browser(flock, alice).get(f"{START}/BBBB-BBBB").status == 404
    limited = browser(flock, alice).get(f"{START}/{started['user_code']}")
    assert limited.status == 429  # even the right code, for a while
    assert (
        browser(flock, alice).post(f"{START}/{started['user_code']}", {"approve": True}).status
        == 429
    )
    assert browser(flock, bob).get(f"{START}/{started['user_code']}").status == 200

    passed = "UPDATE auth_failures SET at = now() - interval '601 seconds' WHERE user_pk = $1"
    flock.call(flock.store.db.execute(passed, alice.id))  # the window passed
    assert browser(flock, alice).get(f"{START}/{started['user_code']}").status == 200


def test_pending_sign_ins_are_capped(flock):
    flock.store.accounts.MAX_DEVICE_GRANTS = 3
    for _ in range(3):
        start(flock)
    refused = flock.anonymous().post(START, {"client": CLIENT})
    assert refused.status == 503
    # Expired ones are swept to make room.
    flock.call(flock.store.db.execute("UPDATE device_grants SET expires_at = to_timestamp(0)"))
    start(flock)


def test_pending_sign_ins_survive_a_restart(make_flock):
    """Pending sign-ins are rows (D84), so a restart (or another replica) keeps them."""
    server = make_flock()
    alice = server.add_user("alice@lab.test")
    started = start(server)
    server.stop()
    restarted = make_flock(reuse=server)
    assert error(poll(restarted, started)) == "authorization_pending"
    code = started["user_code"]
    assert browser(restarted, alice).post(f"{START}/{code}", {"approve": True}).status == 204
    assert poll(restarted, started).json()["user"]["id"] == alice.id
