import datetime as dt
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import asyncpg
import lance
import pyarrow as pa

from tests.app.conftest import Client, HubFlock

DATASETS = "/api/v1/datasets"


def upstream(server: HubFlock, info: dict) -> str:
    return f"s3://{server.bucket.bucket}/datasets/{info['id']}"


def write(
    server: HubFlock,
    info: dict,
    table: pa.Table,
    mode: str = "append",
    as_user: Client | None = None,
    **kwargs,
) -> lance.LanceDataset:
    """A Lance write through the S3 proxy as ``as_user`` (the super admin by default), as
    clients make them."""
    return lance.write_dataset(
        table,
        info["uri"],
        mode=mode,
        storage_options=server.storage_options(as_user),
        data_storage_version="2.2",
        enable_stable_row_ids=True,
        **kwargs,
    )


def open_upstream(server: HubFlock, info: dict) -> lance.LanceDataset:
    return lance.dataset(upstream(server, info), storage_options=server.bucket.storage_options())


def objects(server: HubFlock, info: dict) -> list[str]:
    listed = server.bucket.client().list_objects_v2(
        Bucket=server.bucket.bucket, Prefix=f"datasets/{info['id']}/"
    )
    return [item["Key"] for item in listed.get("Contents", [])]


def create(api, name: str = "ds", org: str = "flock", **body) -> dict:
    created = api.post(f"{DATASETS}/{org}", {"name": name, **body})
    assert created.status == 201, created.body
    return created.json()


# ---------------------------------------------------------------------- configuration


def test_hub_is_off_without_s3(api):
    assert api.get(f"{DATASETS}/flock").status == 503
    assert api.post(f"{DATASETS}/flock", {"name": "x"}).status == 503
    assert api.get("/api/v1/me/hub-keys").status == 503
    assert api.post("/api/v1/hub/credentials", {}).status == 503


def test_mirror_org_is_created_at_startup(hub: HubFlock):
    assert set(hub.store.orgs) == {"flock", "hf"}
    # Its datasets are internal, whoever creates them.
    assert create(hub.client(), "cais--mmlu", org="hf")["visibility"] == "internal"


# ---------------------------------------------------------------------- catalog


def test_dataset_crud(hub):
    api = hub.client()
    created = create(api, "fineweb-sample", description="a sample")
    assert created | {"id": "", "created_at": 0, "updated_at": 0, "created_by": None} == {
        "id": "",
        "org": "flock",
        "name": "fineweb-sample",
        "description": "a sample",
        "visibility": "private",
        "uri": "s3://datasets/flock/fineweb-sample",
        "created_by": None,
        "created_at": 0,
        "updated_at": 0,
        "latest_version": None,
        "rows": None,
        "bytes": None,
        "role": "admin",
        "mirror": None,
    }
    assert created["created_by"]["email"] == hub.ADMIN_EMAIL
    assert api.post(f"{DATASETS}/flock", {"name": "fineweb-sample"}).status == 409
    for bad in (
        {"name": "a/b"},
        {"name": ""},
        {"name": " x"},
        {"name": "x", "visibility": "public"},
        {"name": "x", "extra": 1},
    ):
        assert api.post(f"{DATASETS}/flock", bad).status == 422, bad
    assert api.post(f"{DATASETS}/nope", {"name": "x"}).status == 404

    path = f"{DATASETS}/flock/fineweb-sample"
    assert api.get(path).json()["id"] == created["id"]
    assert [d["name"] for d in api.get(f"{DATASETS}/flock").json()["datasets"]] == [
        "fineweb-sample"
    ]
    assert api.patch(path, {"bogus": 1}).status == 422
    assert api.patch(path, {"name": "a/b"}).status == 422
    updated = api.patch(path, {"name": "sample", "description": "", "visibility": "internal"})
    assert updated.status == 200, updated.body
    assert (updated.json()["name"], updated.json()["visibility"]) == ("sample", "internal")
    assert api.get(path).status == 404
    path = f"{DATASETS}/flock/sample"
    create(api, "other")
    assert api.patch(path, {"name": "other"}).status == 409

    # Written data shows in the info.
    write(hub, api.get(path).json(), pa.table({"x": [1, 2, 3]}), mode="create")
    info = api.get(path).json()
    assert (info["latest_version"], info["rows"]) == (1, 3)
    assert info["bytes"] > 0
    listed = {d["name"]: d for d in api.get(f"{DATASETS}/flock").json()["datasets"]}
    assert listed["sample"]["latest_version"] == 1
    assert listed["other"]["latest_version"] is None

    # Deleting frees the name at once; a new dataset under it starts empty, with a new uuid.
    assert objects(hub, created)
    assert api.delete(path).status == 204
    assert api.get(path).status == 404
    assert api.delete(path).status == 404
    again = create(api, "sample")
    assert again["id"] != created["id"]
    assert again["latest_version"] is None
    assert api.get(f"{DATASETS}/flock/sample/versions").json() == {"versions": [], "total": 0}
    hub.call(hub.store.datasets.flush())
    assert objects(hub, created) == []
    rows = hub.call(
        hub.store.db.fetch("SELECT pk FROM datasets WHERE uuid = $1::uuid", created["id"])
    )
    assert rows == []


def test_deleting_an_org_purges_its_datasets(hub):
    api = hub.client()
    assert api.post("/api/v1/orgs", {"name": "lab"}).status == 201
    info = create(api, "ds", org="lab")
    write(hub, info, pa.table({"x": [1]}), mode="create")
    assert objects(hub, info)
    assert api.delete("/api/v1/orgs/lab").status == 204
    hub.call(hub.store.datasets.flush())
    assert objects(hub, info) == []


def test_deleted_datasets_are_purged_after_a_restart(make_hub, hub):
    api = hub.client()
    info = create(api)
    write(hub, info, pa.table({"x": [1]}), mode="create")
    # Mark it deleted behind the API's back, as if the server stopped before purging.
    hub.call(hub.store.db.execute("UPDATE datasets SET deleted_at = now()"))
    hub.stop()
    restarted = make_hub(reuse=hub)
    restarted.call(restarted.store.datasets.flush())
    assert objects(restarted, info) == []
    assert restarted.client().get(f"{DATASETS}/flock/ds").status == 404


def test_permissions(hub):
    admin = hub.client()
    member = hub.add_user("member@lab.test")
    org_admin = hub.add_user("boss@lab.test", role="admin")
    assert admin.post("/api/v1/orgs", {"name": "other"}).status == 201
    outsider = hub.add_user("out@lab.test", org="other")

    private = create(member.api, "private")
    internal = create(member.api, "internal", visibility="internal")
    assert private["role"] == "member"
    write(hub, private, pa.table({"x": [1]}), mode="create")
    write(hub, internal, pa.table({"x": [1]}), mode="create")
    p, i = f"{DATASETS}/flock/private", f"{DATASETS}/flock/internal"

    # Outsiders: private datasets don't exist for them; internal ones are readable only.
    assert outsider.api.get(p).status == 404
    assert outsider.api.get(f"{p}/versions").status == 404
    assert outsider.api.get(f"{p}/rows").status == 404
    assert outsider.api.patch(p, {"description": "x"}).status == 404
    assert outsider.api.post(f"{DATASETS}/flock", {"name": "x"}).status == 404
    got = outsider.api.get(i)
    assert got.status == 200 and got.json()["role"] is None
    assert outsider.api.get(f"{i}/rows").status == 200
    assert outsider.api.get(f"{i}/readme").status == 200
    assert [d["name"] for d in outsider.api.get(f"{DATASETS}/flock").json()["datasets"]] == [
        "internal"
    ]
    assert outsider.api.patch(i, {"description": "x"}).status == 403
    assert outsider.api.request("PUT", f"{i}/readme", body={"content": "x"}).status == 403
    assert outsider.api.request("PUT", f"{i}/tags/t", body={"version": 1}).status == 403
    assert outsider.api.post(f"{i}/optimize", {}).status == 403
    assert outsider.api.delete(i).status == 403
    # An org without internal datasets doesn't exist for outsiders.
    assert outsider.api.get(f"{DATASETS}/nope").status == 404
    assert member.api.get(f"{DATASETS}/other").status == 404

    # Members: write, but not rename, change visibility, prune or delete.
    assert member.api.patch(p, {"description": "mine"}).status == 200
    assert member.api.patch(p, {"name": "renamed"}).status == 403
    assert member.api.patch(p, {"visibility": "internal"}).status == 403
    assert member.api.request("PUT", f"{p}/tags/t", body={"version": 1}).status == 200
    assert member.api.post(f"{p}/optimize", {}).status == 200
    assert member.api.post(f"{p}/prune", {"older_than_days": 7}).status == 403
    assert member.api.delete(p).status == 403

    # Org admins and super admins do everything.
    assert org_admin.api.post(f"{p}/prune", {"older_than_days": 7}).status == 200
    assert org_admin.api.patch(p, {"visibility": "internal"}).status == 200
    assert admin.patch(p, {"name": "renamed"}).status == 200
    assert admin.get(f"{DATASETS}/flock/renamed").json()["role"] == "admin"
    assert org_admin.api.delete(f"{DATASETS}/flock/renamed").status == 204


# ---------------------------------------------------------------------- history


def test_versions_history(hub):
    api = hub.client()
    member = hub.add_user("member@lab.test")
    info = create(api)
    path = f"{DATASETS}/flock/ds"
    write(hub, info, pa.table({"x": [1, 2, 3]}), mode="create", commit_message="first")
    write(
        hub,
        info,
        pa.table({"x": [4, 5]}),
        as_user=member.api,
        transaction_properties={"__lance_commit_message": "more"},
    )
    open_upstream(hub, info).delete("x = 1")  # around the proxy, with the global key

    listed = api.get(f"{path}/versions")
    assert listed.status == 200, listed.body
    body = listed.json()
    assert body["total"] == 3
    versions = body["versions"]
    assert [v["version"] for v in versions] == [3, 2, 1]
    assert [v["op"] for v in versions] == ["Delete", "Append", "Overwrite"]
    assert [v["message"] for v in versions] == [None, "more", "first"]
    assert [v["rows"] for v in versions] == [4, 5, 3]
    assert [v["fragments"] for v in versions] == [2, 2, 1]
    assert [v["author"]["email"] if v["author"] else None for v in versions] == [
        None,  # written around the proxy: recorded without an author
        "member@lab.test",
        hub.ADMIN_EMAIL,
    ]
    assert all(not v["pruned"] and v["bytes"] > 0 for v in versions)
    assert abs(versions[0]["created_at"] - time.time()) < 300

    page = api.get(f"{path}/versions", offset=1, limit=1).json()
    assert ([v["version"] for v in page["versions"]], page["total"]) == ([2], 3)
    assert api.get(f"{path}/versions", limit=0).status == 422

    detail = api.get(f"{path}/versions/2")
    assert detail.status == 200, detail.body
    assert detail.json()["schema"] == [{"name": "x", "type": "int64", "nullable": True}]
    assert detail.json()["features"] == {"x": {"dtype": "int64", "_type": "Value"}}
    assert detail.json()["metadata"] == {}
    assert api.get(f"{path}/versions/9").status == 404

    # A message for the delete, once; members can't set messages of versions they didn't write.
    assert member.api.patch(f"{path}/versions/3", {"message": "drop 1"}).status == 403
    assert api.patch(f"{path}/versions/3", {"message": ""}).status == 422
    set_message = api.patch(f"{path}/versions/3", {"message": "drop 1"})
    assert set_message.status == 200, set_message.body
    assert set_message.json()["message"] == "drop 1"
    assert api.patch(f"{path}/versions/3", {"message": "again"}).status == 409
    assert api.patch(f"{path}/versions/2", {"message": "again"}).status == 409
    assert api.get(f"{path}/versions").json()["versions"][0]["message"] == "drop 1"


def test_rows(hub):
    api = hub.client()
    info = create(api)
    path = f"{DATASETS}/flock/ds/rows"
    long = "y" * 2500
    table = pa.table(
        {
            "i": list(range(10)),
            "s": [long if i == 2 else f"r{i}" for i in range(10)],
            "b": [b"\x00" * i for i in range(10)],
            "n": [{"k": i, "l": [i, i + 1]} for i in range(10)],
            "t": pa.array(
                [dt.datetime(2026, 1, 1, 0, 0, i) for i in range(10)], pa.timestamp("us")
            ),
            "f": [0.5 * i if i != 3 else None for i in range(10)],
        }
    )
    assert api.get(path).status == 404  # nothing written yet
    write(hub, info, table, mode="create")
    write(hub, info, table.slice(0, 2))

    page = api.get(path, offset=1, limit=3)
    assert page.status == 200, page.body
    body = page.json()
    assert (body["version"], body["offset"], body["total"]) == (2, 1, 12)
    assert body["columns"] == [
        {"name": "i", "type": "int64"},
        {"name": "s", "type": "string"},
        {"name": "b", "type": "binary"},
        {"name": "n", "type": "struct<k: int64, l: list<item: int64>>"},
        {"name": "t", "type": "timestamp[us]"},
        {"name": "f", "type": "double"},
    ]
    assert body["rows"][0] == [
        1,
        "r1",
        {"$binary": 1},
        {"k": 1, "l": [1, 2]},
        "2026-01-01T00:00:01",
        0.5,
    ]
    assert body["rows"][1][1] == "y" * 2000
    assert body["rows"][2][5] is None
    assert body["truncated"] == [[1, 1]]

    chosen = api.get(path, columns="f,i", limit=2).json()
    assert [c["name"] for c in chosen["columns"]] == ["f", "i"]
    assert chosen["rows"] == [[0.0, 0], [0.5, 1]]
    assert api.get(path, columns="i,nope").status == 400
    assert api.get(path, limit=501).status == 422
    assert api.get(path, offset=-1).status == 422
    assert api.get(path, offset=50).json()["rows"] == []
    old = api.get(path, version=1, offset=9).json()
    assert (old["version"], old["total"], len(old["rows"])) == (1, 10, 1)
    assert api.get(path, version=7).status == 404


# ---------------------------------------------------------------------- writes


def test_readme(hub):
    api = hub.client()
    member = hub.add_user("member@lab.test")
    info = create(api)
    path = f"{DATASETS}/flock/ds/readme"
    assert api.get(path).json() == {"version": None, "sha": None, "content": None}
    # A card needs a version to point from.
    assert member.api.request("PUT", path, body={"content": "x"}).status == 409
    write(hub, info, pa.table({"x": [1]}), mode="create")
    assert api.get(path).json() == {"version": 1, "sha": None, "content": None}

    first = member.api.request("PUT", path, body={"content": "# Hello", "message": "card"})
    assert first.status == 200, first.body
    assert first.json() == {"version": 2}
    card = api.get(path).json()
    assert (card["version"], card["content"]) == (2, "# Hello")
    assert open_upstream(hub, info).metadata == {"flock.card": card["sha"]}
    assert f"datasets/{info['id']}/_flock/cards/{card['sha']}.md" in objects(hub, info)

    assert member.api.request("PUT", path, body={"content": "# Bye"}).json() == {"version": 3}
    assert api.get(path).json()["content"] == "# Bye"
    assert api.get(path, version=2).json()["content"] == "# Hello"
    assert api.get(path, version=1).json()["content"] is None
    assert api.get(path, version=9).status == 404
    assert member.api.request("PUT", path, body={"content": 1}).status == 422

    versions = api.get(f"{DATASETS}/flock/ds/versions").json()["versions"]
    assert [(v["op"], v["message"]) for v in versions] == [
        ("UpdateConfig", None),
        ("UpdateConfig", "card"),
        ("Overwrite", None),
    ]
    assert [v["author"]["email"] for v in versions] == [
        "member@lab.test",
        "member@lab.test",
        hub.ADMIN_EMAIL,
    ]
    # The author of a version can give it a message.
    assert member.api.patch(f"{DATASETS}/flock/ds/versions/3", {"message": "bye"}).status == 200


def test_tags_restore_optimize_prune(hub):
    api = hub.client()
    member = hub.add_user("member@lab.test")
    info = create(api)
    path = f"{DATASETS}/flock/ds"
    assert api.request("PUT", f"{path}/tags/v1", body={"version": 1}).status == 409  # empty
    for i in range(3):
        write(hub, info, pa.table({"x": [i]}), mode="create" if i == 0 else "append")

    tagged = api.request("PUT", f"{path}/tags/v1.0", body={"version": 1})
    assert tagged.status == 200, tagged.body
    assert tagged.json() == {"tags": {"v1.0": 1}}
    assert api.request("PUT", f"{path}/tags/v1.0", body={"version": 2}).json() == {
        "tags": {"v1.0": 2}
    }
    assert api.request("PUT", f"{path}/tags/latest", body={"version": 3}).json() == {
        "tags": {"latest": 3, "v1.0": 2}
    }
    assert api.request("PUT", f"{path}/tags/x", body={"version": 9}).status == 404
    assert api.request("PUT", f"{path}/tags/.bad", body={"version": 1}).status == 422
    assert api.request("PUT", f"{path}/tags/x", body={"version": 0}).status == 422
    assert api.delete(f"{path}/tags/latest").json() == {"tags": {"v1.0": 2}}
    assert api.delete(f"{path}/tags/latest").status == 404
    versions = api.get(f"{path}/versions").json()["versions"]
    assert [v["tags"] for v in versions] == [[], ["v1.0"], []]  # tags make no versions

    restored = member.api.post(f"{path}/restore", {"version": 1, "message": "back to one"})
    assert restored.status == 200, restored.body
    assert restored.json() == {"version": 4}
    assert api.post(f"{path}/restore", {"version": 9}).status == 404
    assert open_upstream(hub, info).to_table().to_pydict() == {"x": [0]}
    latest = api.get(f"{path}/versions").json()["versions"][0]
    assert (latest["op"], latest["message"], latest["rows"]) == ("Restore", "back to one", 1)
    # The proxy recorded the acting user, as for any client's commit.
    assert latest["author"]["email"] == "member@lab.test"

    for i in range(3):
        write(hub, info, pa.table({"x": [i]}))
    optimized = member.api.post(f"{path}/optimize", {})
    assert optimized.status == 200, optimized.body
    # With stable row ids, compaction first reserves fragment ids: an op pylance doesn't name.
    assert optimized.json() == {"version": 9}
    assert len(open_upstream(hub, info).get_fragments()) == 1
    newest = api.get(f"{path}/versions").json()["versions"][:3]
    assert [v["op"] for v in newest] == ["Rewrite", None, "Append"]
    assert [v["author"]["email"] for v in newest] == [
        "member@lab.test",
        "member@lab.test",
        hub.ADMIN_EMAIL,
    ]
    # Nothing left to compact: no new version.
    assert api.post(f"{path}/optimize", {}).json() == {"version": 9}

    assert api.post(f"{path}/prune", {"older_than_days": -1}).status == 422
    assert api.post(f"{path}/prune", {"older_than_days": 1}).json() == {
        "versions_removed": 0,
        "bytes_removed": 0,
    }
    pruned = api.post(f"{path}/prune", {"older_than_days": 0})
    assert pruned.status == 200, pruned.body
    assert pruned.json()["versions_removed"] == 7  # all but the latest and the tagged one
    assert pruned.json()["bytes_removed"] > 0
    assert sorted(v["version"] for v in open_upstream(hub, info).versions()) == [2, 9]
    history = api.get(f"{path}/versions").json()
    assert history["total"] == 9  # the history keeps every version
    assert [v["version"] for v in history["versions"] if not v["pruned"]] == [9, 2]
    first = history["versions"][-1]
    assert (first["op"], first["rows"], first["pruned"]) == ("Overwrite", 1, True)
    assert api.get(f"{path}/versions/1").json()["schema"] is None
    assert api.get(f"{path}/rows", version=1).status == 404
    assert api.get(f"{path}/rows", version=2).json()["total"] == 2


def test_writes_need_the_proxy_url(make_hub):
    server = make_hub(hub_proxy_url=None)
    api = server.client()
    create(api)
    assert api.get(f"{DATASETS}/flock/ds").status == 200
    assert api.post(f"{DATASETS}/flock/ds/optimize", {}).status == 503


def test_concurrent_first_reads(hub):
    """Many reads of datasets nobody has opened yet, at once (as the web's dataset page makes
    them), share the cached Lance handles without tripping over each other."""
    api = hub.client()
    clients = [hub.client() for _ in range(20)]
    for round_ in range(3):
        info = create(api, f"fresh-{round_}")
        write(hub, info, pa.table({"x": [1, 2, 3]}), mode="create")
        write(hub, info, pa.table({"x": [4]}))
        path = f"{DATASETS}/flock/{info['name']}"
        paths = [path, f"{path}/versions", f"{path}/rows", f"{path}/readme", f"{path}/versions/1"]
        barrier = threading.Barrier(len(clients))

        def get(i: int, barrier: threading.Barrier = barrier, paths: list[str] = paths) -> int:
            barrier.wait()
            return clients[i].get(paths[i % len(paths)]).status

        with ThreadPoolExecutor(len(clients)) as pool:
            statuses = list(pool.map(get, range(len(clients))))
        assert statuses == [200] * len(clients)
    for client in clients:
        client.close()


# ---------------------------------------------------------------------- invalidation


def test_changes_notify_s3_proxies(hub):
    notified: list[str] = []
    conn = hub.call(asyncpg.connect(hub.database_url))
    hub.call(conn.add_listener("hub_invalidate", lambda *args: notified.append(args[2])))

    def expect(action) -> None:
        before = len(notified)
        action()
        deadline = time.monotonic() + 5
        while len(notified) == before:
            assert time.monotonic() < deadline, "no NOTIFY hub_invalidate"
            time.sleep(0.01)

    api = hub.client()
    try:
        expect(lambda: create(api))
        expect(lambda: api.patch(f"{DATASETS}/flock/ds", {"name": "renamed"}))
        expect(lambda: api.patch(f"{DATASETS}/flock/renamed", {"visibility": "internal"}))
        expect(lambda: api.delete(f"{DATASETS}/flock/renamed"))
        added = []
        expect(lambda: added.append(hub.add_user("member@lab.test")))
        user = added[0]
        expect(lambda: api.patch(f"/api/v1/orgs/flock/members/{user.id}", {"role": "admin"}))
        expect(lambda: api.delete(f"/api/v1/orgs/flock/members/{user.id}"))
        key = api.post("/api/v1/me/hub-keys", {"name": "k"}).json()
        expect(lambda: api.delete(f"/api/v1/me/hub-keys/{key['pk']}"))
        # A description doesn't change who can reach what.
        create(api, "quiet")
        before = len(notified)
        api.patch(f"{DATASETS}/flock/quiet", {"description": "d"})
        time.sleep(0.2)
        assert len(notified) == before
    finally:
        hub.call(conn.close())
