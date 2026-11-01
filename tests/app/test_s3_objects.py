"""The Storage tab's object routes (D65, D67, D69): listing, details, deletes, folders, and the
presigned URLs signed with a session's browser key."""

from urllib.parse import parse_qs, urlsplit

import pytest

from tests.app.conftest import Flocks, S3Flock
from tests.conftest import S3Server

BUCKETS = "/api/v1/orgs/flock/buckets"


@pytest.fixture
def flock(make_flock: Flocks, s3_server: S3Server) -> S3Flock:
    return make_flock.s3(s3_server)


def make_bucket(flock: S3Flock, name: str = "data", *keys: str) -> str:
    """A bucket of ``flock`` holding ``keys`` (each one's body is its key); returns its path."""
    assert flock.client().post(BUCKETS, {"name": name}).status == 201
    pk = flock.call(flock.store.org("flock")).pk
    upstream = f"{flock.bucket_prefix}{pk:05d}-{name}"
    client = flock.s3.client()
    for key in keys:
        client.put_object(Bucket=upstream, Key=key, Body=key.encode())
    return f"{BUCKETS}/{name}"


def upstream_keys(flock: S3Flock, name: str = "data") -> list[str]:
    pk = flock.call(flock.store.org("flock")).pk
    upstream = f"{flock.bucket_prefix}{pk:05d}-{name}"
    pages = flock.s3.client().get_paginator("list_objects_v2").paginate(Bucket=upstream)
    return sorted(o["Key"] for page in pages for o in page.get("Contents", []))


def akid(url: str) -> str:
    return parse_qs(urlsplit(url).query)["X-Amz-Credential"][0].partition("/")[0]


def test_listing_shows_one_level_folders_first(flock):
    path = make_bucket(
        flock, "data", "a.txt", "b/c.txt", "b/d/e.txt", "f/", "f/g.txt", "h.json", "b/"
    )
    api = flock.client()
    root = api.get(f"{path}/objects").json()
    assert root["prefix"] == ""
    assert root["folders"] == ["b/", "f/"]
    assert [o["key"] for o in root["objects"]] == ["a.txt", "h.json"]
    assert root["cursor"] is None
    first = root["objects"][0]
    assert first["size"] == len(b"a.txt")
    assert first["last_modified"] > 0 and len(first["etag"]) == 32

    # A folder lists its own level, without its marker.
    inside = api.get(f"{path}/objects", prefix="b/").json()
    assert inside["folders"] == ["b/d/"]
    assert [o["key"] for o in inside["objects"]] == ["b/c.txt"]

    # The name filter is a prefix within the folder.
    assert [o["key"] for o in api.get(f"{path}/objects", prefix="h").json()["objects"]] == [
        "h.json"
    ]


def test_listing_pages_with_a_cursor(flock):
    keys = [f"k{i:02d}" for i in range(7)]
    path = make_bucket(flock, "data", *keys)
    api = flock.client()
    seen, cursor = [], None
    for _ in range(4):
        params = {"limit": 3, **({"cursor": cursor} if cursor else {})}
        page = api.get(f"{path}/objects", **params).json()
        seen += [o["key"] for o in page["objects"]]
        cursor = page["cursor"]
        if cursor is None:
            break
    assert seen == keys
    assert api.get(f"{path}/objects", limit=0).status == 422
    assert api.get(f"{BUCKETS}/nope/objects").status == 404


def test_head_returns_an_objects_details(flock):
    path = make_bucket(flock)
    pk = flock.call(flock.store.org("flock")).pk
    flock.s3.client().put_object(
        Bucket=f"{flock.bucket_prefix}{pk:05d}-data",
        Key="dir/notes.md",
        Body=b"# hi",
        ContentType="text/markdown",
        Metadata={"run": "r1"},
    )
    api = flock.client()
    got = api.get(f"{path}/object", key="dir/notes.md")
    assert got.status == 200, got.body
    details = got.json()
    assert details["key"] == "dir/notes.md"
    assert details["size"] == 4
    assert details["content_type"] == "text/markdown"
    assert details["metadata"] == {"run": "r1"}
    assert details["last_modified"] > 0 and len(details["etag"]) == 32
    assert api.get(f"{path}/object", key="dir/missing").status == 404


def test_folders_are_empty_markers(flock):
    path = make_bucket(flock)
    api = flock.client()
    created = api.post(f"{path}/folders", {"prefix": "models/v1/"})
    assert created.status == 201, created.body
    assert upstream_keys(flock) == ["models/v1/"]
    assert api.get(f"{path}/objects", prefix="models/").json()["folders"] == ["models/v1/"]
    assert api.get(f"{path}/objects", prefix="models/v1/").json()["objects"] == []
    for bad in ("models", "/abs/", "", "a\x01/"):
        assert api.post(f"{path}/folders", {"prefix": bad}).status == 422, bad


def test_deleting_objects_and_folders(flock):
    path = make_bucket(flock, "data", "a", "b", "dir/", "dir/x", "dir/sub/y", "dirt", "keep/z")
    api = flock.client()
    got = api.post(f"{path}/objects/delete", {"keys": ["a"], "prefixes": ["dir/"]})
    assert got.status == 200, got.body
    assert got.json() == {"deleted": 4}
    assert upstream_keys(flock) == ["b", "dirt", "keep/z"]
    # Keys that don't exist are fine, as in S3.
    assert api.post(f"{path}/objects/delete", {"keys": ["gone"]}).json() == {"deleted": 1}
    assert api.post(f"{path}/objects/delete", {"prefixes": ["keep"]}).status == 422


def test_folder_deletes_are_capped(flock):
    flock.store.s3.FOLDER_DELETE_LIMIT = 5
    path = make_bucket(flock, "data", *(f"big/{i}" for i in range(6)), "small/1", "small/2")
    api = flock.client()
    refused = api.post(f"{path}/objects/delete", {"prefixes": ["big/"]})
    assert refused.status == 409, refused.body
    body = refused.json()
    assert body["limit"] == 5
    assert body["command"] == "aws s3 rm --recursive s3://data/big/ --profile flock"
    assert "more than 5 objects" in body["error"]
    # Nothing was deleted, and the cap counts everything asked for at once.
    assert len(upstream_keys(flock)) == 8
    both = {"keys": ["big/0", "big/1", "big/2", "big/3"], "prefixes": ["small/"]}
    assert api.post(f"{path}/objects/delete", both).status == 409
    assert api.post(f"{path}/objects/delete", {"prefixes": ["small/"]}).json() == {"deleted": 2}


def test_members_use_objects_and_others_cant(flock):
    path = make_bucket(flock, "data", "a")
    alice = flock.add_user("alice@lab.test")
    assert alice.api.get(f"{path}/objects").status == 200
    assert alice.api.post(f"{path}/folders", {"prefix": "mine/"}).status == 201
    assert alice.api.post(f"{path}/objects/delete", {"keys": ["a"]}).status == 200
    assert alice.api.post(f"{path}/presign", {"method": "get", "key": "a"}).status == 200

    assert flock.client().post("/api/v1/orgs", {"name": "lab"}).status == 201
    olga = flock.add_user("olga@lab.test", org="lab")
    for method, sub, body in [
        ("GET", "objects", None),
        ("GET", "object?key=a", None),
        ("POST", "folders", {"prefix": "x/"}),
        ("POST", "objects/delete", {"keys": ["a"]}),
        ("POST", "presign", {"method": "get", "key": "a"}),
    ]:
        got = olga.api.request(method, f"{path}/{sub}", body=body)
        assert got.status == 404, (sub, got.body)


def test_the_browser_key_is_reused_rotated_and_ends_with_the_session(flock):
    path = make_bucket(flock, "data", "a")
    alice = flock.add_user("alice@lab.test")
    web = flock.client(token=alice.signup["access_token"])

    def presign(**body):
        got = web.post(f"{path}/presign", {"key": "a", **body})
        assert got.status == 200, got.body
        return got.json()

    def keys():
        return flock.call(
            flock.store.db.fetch(
                "SELECT access_key_id, name, session_pk, extract(epoch FROM expires_at)::float8 "
                "FROM s3_keys WHERE user_pk = $1 ORDER BY pk",
                alice.id,
            )
        )

    first = presign(method="get")
    assert akid(first["url"]) == akid(presign(method="put", size=10)["url"])
    ((key, name, session, _),) = keys()
    assert key == akid(first["url"]) and name == "browser (ephemeral)" and session is not None
    query = parse_qs(urlsplit(first["url"]).query)
    assert query["X-Amz-Expires"] == ["900"]
    assert query["response-content-disposition"] == ["attachment; filename*=UTF-8''a"]
    assert presign(method="get", disposition="inline")["url"] != first["url"]

    # A link lasts an hour; the key, with 8 hours left, still does.
    link = presign(method="get", link=True)
    assert parse_qs(urlsplit(link["url"]).query)["X-Amz-Expires"] == ["3600"]
    assert akid(link["url"]) == key

    # With 30 minutes left the key still serves 15-minute URLs, but not an hour's link.
    flock.call(
        flock.store.db.execute(
            "UPDATE s3_keys SET expires_at = now() + interval '30 minutes' WHERE access_key_id = $1",
            key,
        )
    )
    assert akid(presign(method="get")["url"]) == key
    second = akid(presign(method="get", link=True)["url"])
    assert second != key
    # With 10 minutes left it is replaced for everything.
    flock.call(
        flock.store.db.execute(
            "UPDATE s3_keys SET expires_at = now() + interval '10 minutes' WHERE access_key_id = $1",
            second,
        )
    )
    flock.call(flock.store.db.execute("DELETE FROM s3_keys WHERE access_key_id = $1", key))
    third = akid(presign(method="get")["url"])
    assert third not in (key, second)
    assert len(keys()) == 2

    # Signing out deletes the session's keys.
    assert web.post("/api/v1/auth/logout", None).status == 204
    assert keys() == []


def test_presign_refuses_what_the_browser_shouldnt_do(flock):
    path = make_bucket(flock, "data", "a")
    api = flock.client()

    def presign(**body):
        return api.post(f"{path}/presign", {"key": "a", **body})

    assert presign(method="put", size=101 * 1024 * 1024).status == 422
    assert presign(method="multipart").status == 422
    assert presign(method="multipart", size=51 * 1024**3).status == 422
    assert presign(method="parts", parts=[1]).status == 422  # no upload_id
    assert presign(method="get", disposition="inline", content_type="text/html").status == 422
    assert presign(method="get", content_type="application/pdf").status == 422  # not inline
    inline = presign(method="get", disposition="inline", content_type="application/pdf")
    assert inline.status == 200, inline.body
    query = parse_qs(urlsplit(inline.json()["url"]).query)
    assert query["response-content-type"] == ["application/pdf"]
    assert presign(method="delete").status == 422
    assert api.post(f"{BUCKETS}/nope/presign", {"key": "a", "method": "get"}).status == 404


def test_deleting_buckets_are_listed_until_purged(flock):
    make_bucket(flock, "data")
    api = flock.client()
    # Keep the purge from finishing, so the bucket stays "being deleted".
    flock.store.s3.purge_later = lambda: None
    assert api.delete(f"{BUCKETS}/data").status == 204
    # Only the buckets Flock manages (013, 014) are left.
    names = [b["name"] for b in api.get(BUCKETS).json()["buckets"]]
    assert names == ["flock-datasets", "flock-models"]
    (listed,) = [
        b for b in api.get(BUCKETS, deleting="true").json()["buckets"] if b["name"] == "data"
    ]
    assert listed["name"] == "data" and listed["deleted_at"] > 0
