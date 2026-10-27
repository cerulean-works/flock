"""S3 keys (D55-D57, D60): issuing them, their lifetimes, and their revocation."""

import time

import pytest

from app.api.src import s3_credentials
from tests.app.conftest import Flocks, S3Flock
from tests.app.utils import cli_login
from tests.conftest import S3Server

CREDENTIALS = "/api/v1/s3/credentials"
KEYS = "/api/v1/me/s3-keys"
HOUR = 3600
DAY = 86400


@pytest.fixture
def flock(make_flock: Flocks, s3_server: S3Server) -> S3Flock:
    return make_flock.s3(s3_server)


def browser(server, user):
    return server.client(token=user.signup["access_token"])


def key_rows(server, user_id: int) -> list:
    return server.call(
        server.store.db.fetch(
            "SELECT access_key_id, session_pk, api_key_pk FROM s3_keys WHERE user_pk = $1 "
            "ORDER BY pk",
            user_id,
        )
    )


def test_s3_is_off_without_its_settings(make_flock):
    flock = make_flock()
    alice = flock.add_user("alice@lab.test")
    assert flock.anonymous().get("/api/v1/auth/config").json()["s3"] is None
    for method, path, body in [
        ("POST", CREDENTIALS, {}),
        ("GET", KEYS, None),
        ("POST", KEYS, {"name": "k", "ttl": DAY}),
        ("GET", "/api/v1/orgs/flock/buckets", None),
        ("POST", "/api/v1/orgs/flock/buckets", {"name": "data"}),
    ]:
        client = browser(flock, alice) if path == KEYS else alice.api
        got = client.request(method, path, body=body)
        assert got.status == 404, (path, got.body)
        assert got.json()["error"] == "S3 isn't enabled on this server"


def test_auth_config_names_the_proxy(flock):
    config = flock.anonymous().get("/api/v1/auth/config").json()
    assert config["s3"] == {"endpoint": flock.proxy.url, "region": "us-east-1"}


def test_a_cli_token_gets_an_ephemeral_key_of_its_default_org(flock):
    alice = flock.add_user("alice@lab.test")
    token = cli_login(flock, browser(flock, alice), "gpu-node-7")
    cli = flock.client(token=token)
    before = time.time()
    got = cli.post(CREDENTIALS, {})
    assert got.status == 200, got.body
    creds = got.json()
    assert creds["endpoint"] == flock.proxy.url
    assert creds["region"] == "us-east-1"
    assert creds["org"] == "flock"
    assert creds["read_only"] is False
    akid = creds["access_key_id"]
    assert len(akid) == 20 and akid.startswith("FLK")
    assert set(akid[3:]) <= set(s3_credentials.ALPHABET)
    assert abs(creds["expires_at"] - before - 8 * HOUR) < 5
    # The secret is derived from the access key id, and stored nowhere.
    secret = flock.call(
        flock.store.db.fetchval("SELECT value FROM meta WHERE key = 's3_key_secret'")
    )
    assert creds["secret_access_key"] == s3_credentials.secret_for(secret, akid)

    # Each call makes a new key; asking for less than the org's lifetime gets less, and more
    # gets the lifetime.
    short = cli.post(CREDENTIALS, {"ttl": 60, "read_only": True}).json()
    assert short["access_key_id"] != akid
    assert short["read_only"] is True
    assert abs(short["expires_at"] - time.time() - 60) < 5
    long = cli.post(CREDENTIALS, {"ttl": 30 * DAY}).json()
    assert abs(long["expires_at"] - time.time() - 8 * HOUR) < 5

    listed = cli.get(KEYS).json()["keys"]
    assert [k["access_key_id"] for k in listed] == [
        akid,
        short["access_key_id"],
        long["access_key_id"],
    ]
    first = listed[0]
    assert first["name"] == "gpu-node-7 (ephemeral)"
    assert first["ephemeral"] is True
    assert first["source"] == "gpu-node-7"
    assert first["session_id"] is not None and first["api_key_id"] is None
    assert first["org"] == "flock"
    assert "secret_access_key" not in first


def test_keys_cover_one_org_the_caller_belongs_to(flock):
    alice = flock.add_user("alice@lab.test")
    assert flock.client().post("/api/v1/orgs", {"name": "lab"}).status == 201
    assert alice.api.post(CREDENTIALS, {"org": "lab"}).status == 404
    assert alice.api.post(CREDENTIALS, {"org": "nope"}).status == 404
    admin = flock.client()
    assert admin.post(CREDENTIALS, {"org": "lab"}).json()["org"] == "lab"
    assert admin.post(CREDENTIALS, {"ttl": 0}).status == 422


def test_an_api_key_gets_keys_that_go_with_it(flock):
    alice = flock.add_user("alice@lab.test")
    ci = alice.api.post("/api/v1/me/api-keys", {"name": "ci"}).json()
    creds = flock.client(api_key=ci["key"]).post(CREDENTIALS, {}).json()
    (listed,) = alice.api.get(KEYS).json()["keys"]
    assert listed["access_key_id"] == creds["access_key_id"]
    assert listed["name"] == "ci (ephemeral)"
    assert listed["api_key_id"] == ci["id"]
    assert listed["source"] == "ci"
    assert alice.api.delete(f"/api/v1/me/api-keys/{ci['id']}").status == 204
    assert alice.api.get(KEYS).json()["keys"] == []
    assert key_rows(flock, alice.id) == []


def test_long_lived_keys_need_a_browser_and_respect_the_maximum(flock):
    alice = flock.add_user("alice@lab.test")
    cli = flock.client(token=cli_login(flock, browser(flock, alice)))
    web = browser(flock, alice)
    body = {"name": "notebook", "ttl": 7 * DAY}
    for client in (cli, alice.api):
        refused = client.post(KEYS, body)
        assert refused.status == 403, refused.body

    created = web.post(KEYS, body)
    assert created.status == 201, created.body
    key = created.json()
    assert key["ephemeral"] is False
    assert key["session_id"] is None and key["api_key_id"] is None
    assert abs(key["expires_at"] - time.time() - 7 * DAY) < 5
    assert key["secret_access_key"] and key["endpoint"] == flock.proxy.url
    assert key["org"] == "flock"

    # The default maximum is 30 days, so 90 days and never are refused.
    assert web.post(KEYS, {"name": "k", "ttl": 90 * DAY}).status == 422
    assert web.post(KEYS, {"name": "k", "ttl": None}).status == 422
    assert web.post(KEYS, {"name": "k"}).status == 422  # the expiry must be chosen
    admin = flock.client()
    assert admin.patch("/api/v1/orgs/flock", {"s3_key_max_ttl": None}).status == 200
    forever = web.post(KEYS, {"name": "forever", "ttl": None})
    assert forever.status == 201, forever.body
    assert forever.json()["expires_at"] is None
    assert "secret_access_key" not in cli.get(KEYS).json()["keys"][0]

    # Anyone's own keys can be revoked with any credential; others' can't.
    bob = flock.add_user("bob@lab.test")
    assert bob.api.delete(f"{KEYS}/{key['id']}").status == 404
    assert alice.api.delete(f"{KEYS}/{key['id']}").status == 204
    assert [k["name"] for k in cli.get(KEYS).json()["keys"]] == ["forever"]


def test_the_two_lifetimes(flock):
    admin = flock.client()
    org = admin.get("/api/v1/orgs/flock").json()
    assert (org["s3_ephemeral_ttl"], org["s3_key_max_ttl"]) == (8 * HOUR, 30 * DAY)

    def patch(**body):
        return admin.patch("/api/v1/orgs/flock", body)

    assert patch(s3_ephemeral_ttl=31 * DAY).status == 422  # longer than the maximum
    assert patch(s3_ephemeral_ttl=HOUR, s3_key_max_ttl=60).status == 422  # too short
    assert patch(s3_ephemeral_ttl=None).status == 422
    assert patch(s3_ephemeral_ttl=2 * DAY, s3_key_max_ttl=DAY).status == 422
    changed = patch(s3_ephemeral_ttl=2 * HOUR)
    assert changed.status == 200, changed.body
    assert changed.json()["s3_ephemeral_ttl"] == 2 * HOUR
    assert abs(flock.credentials()["expires_at"] - time.time() - 2 * HOUR) < 5

    # Lowering the maximum below the ephemeral lifetime lowers it too.
    lowered = patch(s3_key_max_ttl=HOUR).json()
    assert (lowered["s3_ephemeral_ttl"], lowered["s3_key_max_ttl"]) == (HOUR, HOUR)
    never = patch(s3_key_max_ttl=None, s3_ephemeral_ttl=DAY).json()
    assert (never["s3_ephemeral_ttl"], never["s3_key_max_ttl"]) == (DAY, None)

    member = flock.add_user("alice@lab.test")
    assert member.api.patch("/api/v1/orgs/flock", {"s3_ephemeral_ttl": HOUR}).status == 403


def test_a_lower_maximum_shortens_existing_keys(flock):
    alice = flock.add_user("alice@lab.test")
    web = browser(flock, alice)
    key = web.post(KEYS, {"name": "notebook", "ttl": 30 * DAY}).json()
    flock.call(
        flock.store.db.execute(
            "UPDATE s3_keys SET created_at = now() - interval '2 days' WHERE pk = $1", key["id"]
        )
    )
    assert flock.client().patch("/api/v1/orgs/flock", {"s3_key_max_ttl": DAY}).status == 200
    # Older than the new maximum: it no longer works, so it isn't listed (the proxy refuses it,
    # see tests/s3).
    assert web.get(KEYS).json()["keys"] == []
    assert flock.client().patch("/api/v1/orgs/flock", {"s3_key_max_ttl": 7 * DAY}).status == 200
    (listed,) = web.get(KEYS).json()["keys"]
    assert abs(listed["expires_at"] - (time.time() + 5 * DAY)) < 60


def test_keys_go_with_the_cli_session_that_got_them(flock):
    alice = flock.add_user("alice@lab.test")
    web = browser(flock, alice)
    tokens = {name: cli_login(flock, web, name) for name in ("laptop", "node", "box")}
    for token in tokens.values():
        flock.credentials(flock.client(token=token))
    long_lived = web.post(KEYS, {"name": "notebook", "ttl": DAY}).json()

    # flock logout ends the session, and its keys.
    assert flock.client(token=tokens["laptop"]).post("/api/v1/auth/logout", None).status == 204
    sources = [k["source"] for k in web.get(KEYS).json()["keys"]]
    assert sources == ["node", "box", None]

    # Revoking the machine on the account page.
    node = next(s for s in web.get("/api/v1/me/cli-sessions").json()["sessions"])
    assert node["name"] == "node"
    assert web.delete(f"/api/v1/me/cli-sessions/{node['id']}").status == 204
    assert [k["source"] for k in web.get(KEYS).json()["keys"]] == ["box", None]

    # A password change ends every other sign-in; long-lived keys stay, as API keys do.
    changed = web.post("/api/v1/me/password", {"current": alice.password, "new": "password2"})
    assert changed.status == 204
    assert [k["id"] for k in web.get(KEYS).json()["keys"]] == [long_lived["id"]]


def test_a_password_reset_ends_cli_keys(flock):
    alice = flock.add_user("alice@lab.test")
    flock.credentials(flock.client(token=cli_login(flock, browser(flock, alice))))
    assert len(key_rows(flock, alice.id)) == 1
    reset = flock.client().post(f"/api/v1/users/{alice.id}/password-reset", {}).json()
    token = reset["url"].rpartition("/")[2]
    redeemed = flock.anonymous().post(f"/api/v1/password-resets/{token}", {"password": "pass9999"})
    assert redeemed.status == 200, redeemed.body
    assert key_rows(flock, alice.id) == []


def test_the_sweep_deletes_expired_keys(flock):
    alice = flock.add_user("alice@lab.test")
    short = flock.credentials(alice.api, ttl=0.5)
    kept = flock.credentials(alice.api)
    time.sleep(0.6)
    assert [k["access_key_id"] for k in alice.api.get(KEYS).json()["keys"]] == [
        kept["access_key_id"]
    ]
    assert len(key_rows(flock, alice.id)) == 2
    assert flock.call(flock.store.s3.sweep()) == 1
    assert [r["access_key_id"] for r in key_rows(flock, alice.id)] == [kept["access_key_id"]]
    assert short["access_key_id"] != kept["access_key_id"]
