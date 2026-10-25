import time

import pytest

from app.api.src.hub_credentials import read_session_token, secret_for

KEYS = "/api/v1/me/hub-keys"
CREDENTIALS = "/api/v1/hub/credentials"


@pytest.fixture
def hub(make_hub):
    return make_hub(hub_proxy_public_url="https://s3.flock.example", hub_credentials_ttl=3600.0)


def server_secret(server) -> str:
    return server.call(
        server.store.db.fetchval("SELECT value FROM meta WHERE key = 'hub_key_secret'")
    )


def test_keys(hub):
    api = hub.client()
    member = hub.add_user("member@lab.test")
    assert api.get(KEYS).json() == {"keys": []}

    created = api.post(KEYS, {"name": "duckdb", "read_only": True, "org": "flock"})
    assert created.status == 201, created.body
    key = created.json()
    assert key["access_key_id"].startswith("FLKH") and len(key["access_key_id"]) == 20
    assert key["secret_access_key"] == secret_for(server_secret(hub), key["access_key_id"])
    assert key["endpoint"] == "https://s3.flock.example"
    assert key["region"] == "us-east-1"
    assert (key["name"], key["read_only"], key["org"], key["last_used_at"]) == (
        "duckdb",
        True,
        "flock",
        None,
    )
    assert abs(key["created_at"] - time.time()) < 60
    other = api.post(KEYS, {"name": "boto"}).json()
    assert (other["read_only"], other["org"]) == (False, None)

    # Listed without the secret, which is shown only once.
    listed = api.get(KEYS).json()["keys"]
    assert [k["name"] for k in listed] == ["duckdb", "boto"]
    assert listed[0] == {
        k: v for k, v in key.items() if k not in ("secret_access_key", "endpoint", "region")
    }
    assert member.api.get(KEYS).json() == {"keys": []}

    for bad in ({"name": ""}, {"name": "x" * 129}, {"name": "x", "extra": 1}, {}):
        assert api.post(KEYS, bad).status == 422, bad
    assert api.post("/api/v1/orgs", {"name": "lab"}).status == 201
    assert member.api.post(KEYS, {"name": "x", "org": "lab"}).status == 404  # not a member

    # Keys are revoked by deleting them; only their owner can.
    assert member.api.delete(f"{KEYS}/{key['pk']}").status == 404
    assert api.delete(f"{KEYS}/{key['pk']}").status == 204
    assert api.delete(f"{KEYS}/{key['pk']}").status == 404
    assert [k["name"] for k in api.get(KEYS).json()["keys"]] == ["boto"]
    rows = hub.call(hub.store.db.fetch("SELECT access_key_id FROM hub_keys"))
    assert [r[0] for r in rows] == [other["access_key_id"]]


def test_temporary_credentials(hub):
    member = hub.add_user("member@lab.test")
    secret = server_secret(hub)
    # flock.hub sends an API key, and may send no body.
    response = member.api.request("POST", CREDENTIALS)
    assert response.status == 200, response.body
    credentials = response.json()
    assert set(credentials) == {
        "endpoint",
        "region",
        "access_key_id",
        "secret_access_key",
        "session_token",
        "expires_at",
    }
    assert credentials["endpoint"] == "https://s3.flock.example"
    assert credentials["region"] == "us-east-1"
    assert credentials["access_key_id"].startswith("FLKT")
    assert credentials["secret_access_key"] == secret_for(secret, credentials["access_key_id"])
    assert abs(credentials["expires_at"] - (time.time() + 3600)) < 60
    session = read_session_token(secret, credentials["session_token"], now=time.time())
    assert session is not None
    assert (session.user, session.read_only, session.mirror_writer) == (member.id, False, False)
    assert session.access_key_id == credentials["access_key_id"]
    assert session.expires_at == credentials["expires_at"]
    assert read_session_token(secret, credentials["session_token"], now=time.time() + 3601) is None

    read_only = member.api.post(CREDENTIALS, {"read_only": True}).json()
    assert read_only["access_key_id"] != credentials["access_key_id"]
    session = read_session_token(secret, read_only["session_token"], now=time.time())
    assert session is not None and session.read_only
    assert member.api.post(CREDENTIALS, {"mirror_writer": True}).status == 422
    assert hub.anonymous().post(CREDENTIALS, {}).status == 401


def test_endpoint_defaults_to_the_proxy_url(make_hub):
    server = make_hub(hub_proxy_public_url=None)
    assert server.client().post(CREDENTIALS, {}).json()["endpoint"] == server.proxy.url
