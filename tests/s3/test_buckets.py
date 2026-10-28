"""Org buckets (D53, D54): creating and deleting them in Flock, and who reaches them."""

import asyncio
import threading

import pytest
from botocore.exceptions import ClientError

from tests.app.conftest import Flocks, S3Flock
from tests.app.utils import cli_login
from tests.conftest import S3Server
from tests.s3.conftest import S3Env, code, make_env

BUCKETS = "/api/v1/orgs/{org}/buckets"


def buckets_of(env: S3Env, org: str = "flock", user: str = "admin") -> list[str]:
    listed = env.api(user).get(BUCKETS.format(org=org))
    assert listed.status == 200, listed.body
    return [b["name"] for b in listed.json()["buckets"]]


def test_creating_a_bucket(env: S3Env):
    name = env.name("ckpt")
    alice = env.users["alice"]
    cli = env.flock.client(
        token=cli_login(
            env.flock, env.flock.client(token=alice.signup["access_token"]), "gpu-node-7"
        )
    )
    created = cli.post(BUCKETS.format(org="flock"), {"name": name})
    assert created.status == 201, created.body
    bucket = created.json()
    pk = env.flock.call(env.flock.store.org("flock")).pk
    upstream = f"{env.flock.bucket_prefix}{pk:05d}-{name}"
    assert bucket["upstream_name"] == upstream
    assert bucket["created_by"]["email"] == alice.email
    assert upstream in env.server.buckets(env.flock.bucket_prefix)

    detail = env.api("bob").get(f"{BUCKETS.format(org='flock')}/{name}").json()
    (event,) = detail["events"]
    assert event["kind"] == "created"
    assert event["user"]["email"] == alice.email
    assert event["detail"]["via"] == "cli"
    assert event["detail"]["machine"] == "gpu-node-7"

    # Not with a wandb key (D72), and from the dashboard (a browser session).
    wandb_key = env.flock.client(api_key=alice.api_key)
    assert wandb_key.post(BUCKETS.format(org="flock"), {"name": env.name()}).status == 401
    web = env.flock.client(token=alice.signup["access_token"])
    third = env.name()
    assert web.post(BUCKETS.format(org="flock"), {"name": third}).status == 201
    events = env.api("admin").get(f"{BUCKETS.format(org='flock')}/{third}").json()["events"]
    assert events[0]["detail"]["via"] == "ui"

    assert cli.post(BUCKETS.format(org="flock"), {"name": name}).status == 409
    assert {name, third} <= set(buckets_of(env, user="bob"))


def test_bucket_names(env: S3Env):
    limit = 57 - len(env.flock.bucket_prefix)
    listed = env.api("alice").get(BUCKETS.format(org="flock")).json()
    assert listed["name_limit"] == limit
    api = env.api("alice")
    for bad in [
        "",
        "UPPER",
        "-start",
        "end-",
        "a..b",
        "under_score",
        "x" * (limit + 1),
        "a-s3alias",
        "spa ce",
    ]:
        refused = api.post(BUCKETS.format(org="flock"), {"name": bad})
        assert refused.status == 422, (bad, refused.body)
    longest = env.name("l")
    longest += "x" * (limit - len(longest))
    created = api.post(BUCKETS.format(org="flock"), {"name": longest})
    assert created.status == 201, created.body
    assert len(created.json()["upstream_name"]) == 63
    for good in [env.name("a.b"), "7" + env.name()]:
        assert api.post(BUCKETS.format(org="flock"), {"name": good}).status == 201


def test_s3_cannot_create_or_delete_buckets(env: S3Env):
    name = env.bucket()
    s3 = env.s3(env.key("carol"))
    for call in (
        lambda: s3.create_bucket(Bucket=env.name()),
        lambda: s3.delete_bucket(Bucket=name),
    ):
        with pytest.raises(ClientError) as error:
            call()
        assert code(error.value) == "NotImplemented"
    assert "flock s3 buckets" in error.value.response["Error"]["Message"]
    assert name in buckets_of(env)


def test_list_buckets_shows_the_keys_org(env: S3Env):
    mine, theirs = env.bucket(), env.bucket(org="lab")
    names = [b["Name"] for b in env.s3(env.key("alice")).list_buckets()["Buckets"]]
    assert names == sorted(buckets_of(env))
    assert mine in names and theirs not in names
    lab = [b["Name"] for b in env.s3(env.key("olga")).list_buckets()["Buckets"]]
    assert theirs in lab and mine not in lab


def test_two_orgs_with_a_bucket_of_the_same_name(env: S3Env):
    name = env.name("shared")
    env.bucket(org="flock", name=name)
    env.bucket(org="lab", name=name)
    flock, lab = env.s3(env.key("alice")), env.s3(env.key("olga"))
    flock.put_object(Bucket=name, Key="who", Body=b"flock")
    lab.put_object(Bucket=name, Key="who", Body=b"lab")
    assert flock.get_object(Bucket=name, Key="who")["Body"].read() == b"flock"
    assert lab.get_object(Bucket=name, Key="who")["Body"].read() == b"lab"
    assert env.upstream_name(name, "flock") != env.upstream_name(name, "lab")


def test_buckets_outside_flock_are_not_there(env: S3Env):
    """A bucket that exists upstream but has no row in Flock is NoSuchBucket, even when its
    name is one Flock would give it."""
    name = env.name("ghost")
    env.upstream().create_bucket(Bucket=env.upstream_name(name))
    s3 = env.s3(env.key("alice"))
    with pytest.raises(ClientError) as error:
        s3.list_objects_v2(Bucket=name)
    assert code(error.value) == "NoSuchBucket"
    with pytest.raises(ClientError) as error:
        s3.put_object(Bucket=name, Key="x", Body=b"x")
    assert code(error.value) == "NoSuchBucket"
    # Nor can Flock create it over the one outside.
    refused = env.api("alice").post(BUCKETS.format(org="flock"), {"name": name})
    assert refused.status == 409, refused.body


def test_permissions(env: S3Env):
    """D54: members and admins read and write objects; only admins delete buckets; anyone else
    reaches nothing."""
    name = env.bucket(user="alice")
    for user in ("alice", "carol"):
        s3 = env.s3(env.key(user))
        s3.put_object(Bucket=name, Key=user, Body=user.encode())
        assert s3.get_object(Bucket=name, Key="alice")["Body"].read() == b"alice"
    # Members delete objects too, anyone's.
    env.s3(env.key("alice")).delete_object(Bucket=name, Key="carol")
    env.s3(env.key("carol")).put_object(Bucket=name, Key="carol", Body=b"carol")
    # Olga is in lab: she can't get a key for flock, and her keys don't see flock's bucket.
    assert env.api("olga").post("/api/v1/s3/credentials", {"org": "flock"}).status == 404
    with pytest.raises(ClientError) as error:
        env.s3(env.key("olga")).get_object(Bucket=name, Key="alice")
    assert code(error.value) == "NoSuchBucket"
    assert env.api("olga").get(BUCKETS.format(org="flock")).status == 404
    assert env.api("olga").post(BUCKETS.format(org="flock"), {"name": env.name()}).status == 404

    path = f"{BUCKETS.format(org='flock')}/{name}"
    assert env.api("alice").delete(path).status == 403
    assert env.api("carol").delete(path).status == 204
    assert env.api("carol").delete(path).status == 404


def block_purges(flock: S3Flock) -> threading.Event:
    """Makes the flock's purges wait until the returned event is set."""
    release = threading.Event()
    upstream = flock.store.s3.upstream
    assert upstream is not None
    purge = upstream.purge_bucket

    async def blocked(name: str) -> int:
        while not release.is_set():
            await asyncio.sleep(0.02)
        return await purge(name)

    upstream.purge_bucket = blocked  # ty: ignore[invalid-assignment]
    return release


def purged(env: S3Env, upstream_name: str) -> bool:
    rows = env.sql(
        "SELECT purged_at FROM s3_buckets WHERE upstream_name = $1 ORDER BY pk DESC LIMIT 1",
        upstream_name,
    )
    return rows[0]["purged_at"] is not None


def test_deleting_a_bucket(make_flock: Flocks, s3_server: S3Server, wait_for):
    env = make_env(make_flock, s3_server)
    name = env.bucket()
    upstream = env.upstream_name(name)
    s3 = env.s3(env.key("alice"))
    s3.put_object(Bucket=name, Key="x", Body=b"old")
    upload = s3.create_multipart_upload(Bucket=name, Key="unfinished")
    s3.upload_part(
        Bucket=name, Key="unfinished", UploadId=upload["UploadId"], PartNumber=1, Body=b"p"
    )
    release = block_purges(env.flock)

    assert env.api("carol").delete(f"{BUCKETS.format(org='flock')}/{name}").status == 204
    assert name not in buckets_of(env)
    with pytest.raises(ClientError) as error:
        s3.get_object(Bucket=name, Key="x")
    assert code(error.value) == "NoSuchBucket"
    # Until it's gone upstream, the name can't be used again.
    again = env.api("alice").post(BUCKETS.format(org="flock"), {"name": name})
    assert again.status == 409
    assert "still being deleted" in again.json()["error"]
    assert upstream in s3_server.buckets(env.flock.bucket_prefix)

    release.set()
    wait_for(lambda: purged(env, upstream))
    assert upstream not in s3_server.buckets(env.flock.bucket_prefix)
    kinds = env.sql(
        "SELECT e.kind, e.detail FROM s3_bucket_events e JOIN s3_buckets b ON b.pk = e.bucket_pk "
        "WHERE b.upstream_name = $1 ORDER BY e.pk",
        upstream,
    )
    assert [k["kind"] for k in kinds] == ["created", "deleted", "purged"]
    assert '"objects": 1' in kinds[2]["detail"]

    # Now the name is free, and the new bucket starts empty.
    assert env.api("alice").post(BUCKETS.format(org="flock"), {"name": name}).status == 201
    assert s3.list_objects_v2(Bucket=name).get("Contents", []) == []


def test_deleting_an_org_purges_its_buckets_after_a_restart(
    make_flock: Flocks, s3_server: S3Server, wait_for
):
    env = make_env(make_flock, s3_server)
    admin = env.api("admin")
    assert admin.post("/api/v1/orgs", {"name": "doomed"}).status == 201
    names = [env.bucket(org="doomed") for _ in range(2)]
    upstream = [env.upstream_name(n, "doomed") for n in names]
    s3 = env.s3(env.key("admin", org="doomed"))
    for name in names:
        s3.put_object(Bucket=name, Key="x", Body=b"x")
    block_purges(env.flock)
    assert admin.delete("/api/v1/orgs/doomed").status == 204
    rows = env.sql(
        "SELECT org_pk, deleted_at, purged_at FROM s3_buckets WHERE upstream_name = ANY($1)",
        upstream,
    )
    assert [(r["org_pk"], r["purged_at"]) for r in rows] == [(None, None), (None, None)]
    assert all(r["deleted_at"] is not None for r in rows)

    # The flock stops before the purge finishes; started again, it picks the purge up.
    env.flock.stop()
    assert set(upstream) <= set(s3_server.buckets(env.flock.bucket_prefix))
    restarted = make_flock.s3(s3_server, reuse=env.flock)
    env.flock = restarted
    for name in upstream:
        wait_for(lambda name=name: purged(env, name))
    assert not set(upstream) & set(s3_server.buckets(env.flock.bucket_prefix))


def test_renaming_an_org_leaves_its_buckets(env: S3Env):
    name = env.bucket(org="lab")
    s3 = env.s3(env.key("olga"))
    s3.put_object(Bucket=name, Key="x", Body=b"x")
    before = env.api("admin").get(f"{BUCKETS.format(org='lab')}/{name}").json()["upstream_name"]
    assert env.api("admin").patch("/api/v1/orgs/lab", {"display_name": "The Lab"}).status == 200
    after = env.api("admin").get(f"{BUCKETS.format(org='lab')}/{name}").json()["upstream_name"]
    assert after == before
    assert s3.get_object(Bucket=name, Key="x")["Body"].read() == b"x"


def test_orgs_stop_at_pk_99999(make_flock: Flocks, s3_server: S3Server):
    flock = make_flock.s3(s3_server)
    admin = flock.client()

    def org_names():
        return flock.call(flock.store.db.fetch("SELECT name FROM orgs ORDER BY pk"))

    flock.call(flock.store.db.execute("ALTER TABLE orgs ALTER COLUMN pk RESTART WITH 99999"))
    assert admin.post("/api/v1/orgs", {"name": "last"}).status == 201
    assert flock.call(flock.store.org("last")).pk == 99999
    # Its buckets get the same 57 characters as everyone's.
    name = "x" * (57 - len(flock.bucket_prefix))
    created = admin.post(BUCKETS.format(org="last"), {"name": name})
    assert created.status == 201, created.body
    assert created.json()["upstream_name"] == f"{flock.bucket_prefix}99999-{name}"

    refused = admin.post("/api/v1/orgs", {"name": "one-too-many"})
    assert refused.status == 409
    assert refused.json()["error"] == "this server can't hold more organizations"
    assert flock.call(flock.store.find_org("one-too-many")) is None
    assert [r["name"] for r in org_names()] == ["flock", "last"]
    assert status_of_org(admin, "one-too-many") == 404


def status_of_org(client, name: str) -> int:
    return client.get(f"/api/v1/orgs/{name}").status


def test_server_errors_creating_upstream_leave_nothing(env: S3Env):
    """If the S3 server refuses the bucket, Flock keeps no row or event of it."""
    name = env.name("refused")
    upstream = env.flock.store.s3.upstream
    assert upstream is not None
    create = upstream.create_bucket

    async def refuse(bucket: str) -> None:
        from app.api.src.s3_proxy.upstream import UpstreamError

        raise UpstreamError("InvalidBucketName", "nope", 400)

    upstream.create_bucket = refuse  # ty: ignore[invalid-assignment]
    try:
        refused = env.api("alice").post(BUCKETS.format(org="flock"), {"name": name})
    finally:
        upstream.create_bucket = create  # ty: ignore[invalid-assignment]
    assert refused.status == 502
    assert "InvalidBucketName" in refused.json()["error"]
    assert env.sql("SELECT pk FROM s3_buckets WHERE name = $1", name) == []
    assert status_of_org(env.api("alice"), "flock") == 200
