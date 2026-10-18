FEED = "/api/v1/feed"


def kinds(items: list[dict]) -> list[tuple[str, str]]:
    return [(i["kind"], i["run"]["id"] if "run" in i else i["project"]) for i in items]


def test_the_feed_lists_what_happened_newest_first(tracker, api):
    alice = tracker.add_user("alice@lab.test")
    api.create_run("p", "r1")
    api.stream("p", "r1", [], complete=0)
    alice.api.create_run("q", "a1", tags=["sweep"])
    alice.api.stream("q", "a1", [], complete=1)
    api.create_run("p", "r2")
    expected = [
        ("run_started", "r2"),
        ("run_failed", "a1"),
        ("run_started", "a1"),
        ("project_created", "q"),
        ("run_finished", "r1"),
        ("run_started", "r1"),
        ("project_created", "p"),
    ]
    body = alice.api.get(FEED).json()
    assert kinds(body["items"]) == expected
    assert body["next"] is None
    failed = body["items"][1]
    assert failed["id"].startswith("run_failed:")
    assert (failed["org"], failed["project"]) == ("flock", "q")
    assert failed["actor"] == {
        "id": alice.id,
        "name": "alice",
        "email": alice.email,
        "avatar_url": None,
    }
    assert failed["run"] | {"display_name": None, "runtime": 0} == {
        "id": "a1",
        "display_name": None,
        "state": "failed",
        "gpu_type": None,
        "gpu_count": None,
        "runtime": 0,
        "tags": ["sweep"],
        "group": None,
        "job_type": None,
    }
    assert [i["at"] for i in body["items"]] == sorted(
        (i["at"] for i in body["items"]), reverse=True
    )

    # Pages of 2, following the cursor, list the same items.
    seen, cursor = [], None
    while True:
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        page = api.get(FEED, **params).json()
        seen += page["items"]
        cursor = page["next"]
        if cursor is None:
            break
    assert kinds(seen) == expected

    assert kinds(alice.api.get(FEED, owner="me").json()["items"]) == expected[1:3]
    assert kinds(api.get(FEED, owner=str(alice.id)).json()["items"]) == expected[1:3]
    started = api.get(FEED, kinds="run_started").json()["items"]
    assert kinds(started) == [("run_started", "r2"), ("run_started", "a1"), ("run_started", "r1")]
    in_p = api.get(FEED, org="flock", project="p").json()["items"]
    assert kinds(in_p) == [expected[0], *expected[4:]]


def test_feed_visibility_and_bad_requests(tracker, api):
    alice = tracker.add_user("alice@lab.test")
    assert api.post("/api/v1/orgs", {"name": "lab"}).status == 201
    api.create_run("secret", "s1")  # in flock
    assert api.post("/api/v1/projects", {"entity": "lab", "name": "hidden"}).status == 201
    projects = {i["project"] for i in alice.api.get(FEED).json()["items"]}
    assert projects == {"secret"}  # not lab's
    assert {i["project"] for i in api.get(FEED).json()["items"]} == {"secret", "hidden"}
    assert alice.api.get(FEED, org="lab").status == 404
    assert api.get(FEED, org="lab", project="nope").status == 404
    assert api.get(FEED, project="secret").status == 400  # needs org
    assert api.get(FEED, kinds="run_started,nonsense").status == 400
    assert api.get(FEED, owner="someone").status == 400
    assert api.get(FEED, cursor="not a cursor").status == 400
    assert api.get(FEED, limit=0).status == 422
