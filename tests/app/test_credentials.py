"""Which credential does what (D70-D72): browser sessions, CLI tokens, wandb keys and S3 keys,
each against the REST API, the wandb protocol, the S3 key exchange and the S3 proxy."""

import base64
import re

import boto3
import httpx
import pytest
from botocore.config import Config

from tests.app.conftest import Flocks, S3Flock, User
from tests.app.utils import cli_login
from tests.conftest import S3Server

ME = "/api/v1/me"
WANDB_KEYS = "/api/v1/me/wandb-keys"
CREDENTIALS = "/api/v1/s3/credentials"
VIEWER = "query Viewer { viewer { id username } }"
REFUSED = "wandb keys only work with the wandb SDK: use `flock login` or a browser session"
UNKNOWN = "invalid or missing access token, CLI token or wandb key"


@pytest.fixture
def flock(make_flock: Flocks, s3_server: S3Server) -> S3Flock:
    return make_flock.s3(s3_server)


def basic(key: str) -> dict[str, str]:
    """A wandb key sent as the wandb SDK sends it."""
    return {"Authorization": f"Basic {base64.b64encode(f'api:{key}'.encode()).decode()}"}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def credentials(flock: S3Flock, alice: User) -> dict[str, dict[str, str]]:
    """The headers of each kind of credential of ``alice``. An S3 key isn't sent as a header
    anywhere but the proxy, so it gets the closest a client could do: its secret as a token."""
    browser = flock.client(token=alice.signup["access_token"])
    s3 = flock.credentials(alice.api)
    return {
        "browser": bearer(alice.signup["access_token"]),
        "cli": bearer(cli_login(flock, browser)),
        "wandb": basic(alice.api_key),
        "s3": bearer(s3["secret_access_key"]),
    }


def test_each_credential_does_its_own_job(flock):
    alice = flock.add_user("alice@lab.test")
    anon = flock.anonymous()
    # (REST API, wandb protocol, S3 key exchange): the status, and the error if refused.
    expected = {
        "browser": (200, 200, 200),
        "cli": (200, 200, 200),
        "wandb": (401, 200, 401),
        "s3": (401, 401, 401),
    }
    for kind, headers in credentials(flock, alice).items():
        rest = anon.get(ME, headers=headers)
        wandb = anon.graphql(VIEWER, headers=headers)
        exchange = anon.post(CREDENTIALS, {}, headers=headers)
        got = (rest.status, wandb.status, exchange.status)
        assert got == expected[kind], (kind, got)
        for response in (rest, wandb, exchange):
            if response.status == 200:
                continue
            error = REFUSED if kind == "wandb" else UNKNOWN
            assert response.json()["error"] == error, (kind, response.body)
        if rest.status == 200:
            assert rest.json()["id"] == alice.id
        if wandb.status == 200:
            assert wandb.json()["data"]["viewer"]["username"] == alice.email


def test_only_s3_keys_reach_the_proxy(flock):
    alice = flock.add_user("alice@lab.test")
    for kind, headers in credentials(flock, alice).items():
        got = httpx.get(f"{flock.proxy.url}/", headers=headers)
        assert got.status_code == 403, (kind, got.text)
        assert "<Code>AccessDenied</Code>" in got.text, (kind, got.text)

    key = flock.credentials(alice.api)
    s3 = boto3.client(
        "s3",
        endpoint_url=flock.proxy.url,
        aws_access_key_id=key["access_key_id"],
        aws_secret_access_key=key["secret_access_key"],
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )
    assert s3.list_buckets()["ResponseMetadata"]["HTTPStatusCode"] == 200


def test_wandb_keys_are_refused_however_they_are_sent(flock):
    alice = flock.add_user("alice@lab.test")
    anon = flock.anonymous()
    for headers in (basic(alice.api_key), bearer(alice.api_key), {"x-api-key": alice.api_key}):
        for path in (ME, "/api/v1/projects", "/api/v1/orgs/flock/buckets", "/api/v1/feed"):
            refused = anon.get(path, headers=headers)
            assert refused.status == 401, (path, refused.body)
            assert refused.json()["error"] == REFUSED
        viewer = anon.graphql(VIEWER, headers=headers)
        assert viewer.json()["data"]["viewer"]["username"] == alice.email
    # Public routes still need no credential, and a wandb key doesn't get in their way.
    assert anon.get("/api/v1/auth/config").status == 200
    assert anon.get("/api/v1/auth/config", headers=basic(alice.api_key)).status == 200


def test_wandb_keys(flock):
    alice = flock.add_user("alice@lab.test")
    created = alice.api.post(WANDB_KEYS, {"name": "laptop"})  # with a CLI token
    assert created.status == 201, created.body
    key = created.json()["key"]
    assert re.fullmatch(r"flock-wandb-[A-Za-z0-9]{40}", key)
    assert alice.api_key.startswith("flock-wandb-")
    listed = alice.api.get(WANDB_KEYS).json()["keys"]
    assert [(k["name"], k["prefix"]) for k in listed] == [
        ("tests", "flock-wandb-"),
        ("laptop", "flock-wandb-"),
    ]
    assert all("key" not in k for k in listed)

    browser = flock.client(token=alice.signup["access_token"])
    assert browser.post(WANDB_KEYS, {"name": "web"}).status == 201

    # A wandb key can't make another, however it's sent.
    for headers in (basic(key), bearer(key), {"x-api-key": key}):
        refused = flock.anonymous().post(WANDB_KEYS, {"name": "more"}, headers=headers)
        assert refused.status == 401
        assert refused.json()["error"] == REFUSED
    assert len(alice.api.get(WANDB_KEYS).json()["keys"]) == 3

    key_id = created.json()["id"]
    assert flock.client().delete(f"{WANDB_KEYS}/{key_id}").status == 404  # not the admin's
    assert alice.api.delete(f"{WANDB_KEYS}/{key_id}").status == 204
    assert flock.client(api_key=key).graphql(VIEWER).status == 401
    assert alice.api.delete(f"{WANDB_KEYS}/{key_id}").status == 404


def test_the_old_api_keys_routes_still_work(flock):
    alice = flock.add_user("alice@lab.test")
    old = "/api/v1/me/api-keys"
    created = alice.api.post(old, {"name": "ci"})
    assert created.status == 201
    assert created.json()["key"].startswith("flock-wandb-")
    assert alice.api.get(old).json() == alice.api.get(WANDB_KEYS).json()
    assert [k["name"] for k in alice.api.get(old).json()["keys"]] == ["tests", "ci"]
    assert alice.api.delete(f"{old}/{created.json()['id']}").status == 204
    assert [k["name"] for k in alice.api.get(WANDB_KEYS).json()["keys"]] == ["tests"]
    refused = flock.client(api_key=alice.api_key).get(old)
    assert (refused.status, refused.json()["error"]) == (401, REFUSED)


def test_accepting_an_invite_signed_in_refuses_wandb_keys(flock):
    alice = flock.add_user("alice@lab.test")
    assert flock.client().post("/api/v1/orgs", {"name": "lab"}).status == 201
    invite = flock.client().post("/api/v1/orgs/lab/invites", {"email": alice.email}).json()
    accept = f"/api/v1/invites/{invite['token']}/accept"
    refused = flock.client(api_key=alice.api_key).post(accept, {})
    assert (refused.status, refused.json()["error"]) == (401, REFUSED)
    assert alice.api.post(accept, {}).status == 200
