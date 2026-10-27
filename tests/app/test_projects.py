import pytest

from tests.app.utils import history_rows, system_samples

PROJECTS = "/api/v1/projects"


def test_create_list_get_and_update(api):
    created = api.post(PROJECTS, {"entity": "flock", "name": "vision", "description": "cnns"})
    assert created.status == 201, created.body
    assert created.json() | {"created_at": 0, "updated_at": 0} == {
        "entity": "flock",
        "name": "vision",
        "description": "cnns",
        "created_at": 0,
        "updated_at": 0,
        "run_count": 0,
        "active_run_count": 0,
        "column_count": 0,
    }
    duplicate = api.post(PROJECTS, {"entity": "flock", "name": "vision"})
    assert duplicate.status == 409
    assert api.post(PROJECTS, {"entity": "lab", "name": "nlp"}).status == 404  # no such org
    assert api.post("/api/v1/orgs", {"name": "lab"}).status == 201
    assert api.post(PROJECTS, {"entity": "lab", "name": "nlp"}).status == 201

    listed = {(p["entity"], p["name"]) for p in api.get(PROJECTS).json()["projects"]}
    assert listed == {("flock", "vision"), ("lab", "nlp")}
    by_entity = api.get(PROJECTS, entity="lab").json()["projects"]
    assert [p["name"] for p in by_entity] == ["nlp"]

    assert api.get(f"{PROJECTS}/flock/vision").json()["description"] == "cnns"
    kept = api.patch(f"{PROJECTS}/flock/vision", {}).json()
    assert kept["description"] == "cnns"  # fields left out don't change
    cleared = api.patch(f"{PROJECTS}/flock/vision", {"description": None}).json()
    assert (cleared["name"], cleared["description"]) == ("vision", None)
    assert api.get(f"{PROJECTS}/flock/missing").status == 404
    assert api.patch(f"{PROJECTS}/flock/missing", {"description": "x"}).status == 404


@pytest.mark.parametrize("name", ["", " padded", "a/b", "a:b", "50%", "x" * 129, "tab\there"])
def test_invalid_project_names_are_rejected(api, name):
    assert api.post(PROJECTS, {"entity": "flock", "name": name}).status == 422
    api.create_run("p", "r1")
    assert api.patch(f"{PROJECTS}/flock/p", {"name": name}).status == 422


def test_unknown_fields_are_rejected(api):
    assert api.post(PROJECTS, {"entity": "flock", "name": "x", "colour": "red"}).status == 422


def test_logging_creates_the_project(api):
    api.create_run("auto", "r1")
    project = api.get(f"{PROJECTS}/flock/auto").json()
    assert (project["run_count"], project["active_run_count"]) == (1, 1)


def test_empty_projects_survive_a_restart(make_flock):
    server = make_flock()
    api = server.client()
    api.post(PROJECTS, {"entity": "flock", "name": "empty", "description": "later"})
    server.stop()
    restarted = make_flock(reuse=server).client()
    assert restarted.get(f"{PROJECTS}/flock/empty").json()["description"] == "later"
    assert restarted.runs("empty")["total"] == 0
    assert restarted.workspace("empty")["runs"] == []


def test_file_stream_does_not_create_runs(api):
    resp = api.file_stream("flock", "p", "ghost", {"files": {}})
    assert resp.status == 404
    assert api.get(f"{PROJECTS}/flock/p").status == 404


def test_rename(make_flock, events):
    server = make_flock()
    api = server.client()
    api.create_run("old", "r1")
    api.stream("old", "r1", history_rows(3, loss=float))
    live = events("old", "r1", client=api)
    live.next_of("hello")

    refused = api.patch(f"{PROJECTS}/flock/old", {"name": "new"})
    assert refused.status == 409  # r1 is still logging to flock/old
    assert refused.json()["ids"] == ["r1"]
    api.stream("old", "r1", history_rows(1, start=3, loss=float), offset=3, complete=0)
    renamed = api.patch(f"{PROJECTS}/flock/old", {"name": "new", "description": "moved"})
    assert (renamed.json()["name"], renamed.json()["description"]) == ("new", "moved")
    assert live.next_of("project") == {"renamed_to": "new"}
    with pytest.raises(AssertionError, match="stream ended"):
        live.next()

    assert api.get(f"{PROJECTS}/flock/old").status == 404
    assert api.only_run("new")["state"] == "finished"
    assert api.run_history("new", "r1", keys="loss")["loss"]["x"] == [0, 1, 2, 3]
    assert api.workspace("new")["metrics"] == ["loss"]
    # The old name is free again: logging to it creates a new, separate project.
    api.create_run("old", "r1")
    assert api.get(f"{PROJECTS}/flock/old").json()["run_count"] == 1
    assert api.patch(f"{PROJECTS}/flock/new", {"name": "old"}).status == 409  # taken

    server.stop()
    restarted = make_flock(reuse=server).client()
    assert restarted.run_history("new", "r1", keys="loss")["loss"]["y"] == [0.0, 1.0, 2.0, 3.0]
    assert restarted.get(f"{PROJECTS}/flock/new").json()["description"] == "moved"


def test_delete_project_removes_everything(make_flock, events):
    server = make_flock()
    store = server.store
    api = server.client()
    api.create_run("gone", "r1")
    samples = system_samples(2, {"system.cpu": float})
    api.stream("gone", "r1", history_rows(3, loss=float), events=samples, complete=0)
    api.create_run("gone", "r2")  # still running
    run = store.get_run("flock", "gone", "r1")
    server.call(store.save_file(run, "config.yaml", b"lr: 0.1\n"))
    server.call(store.flush())
    pks = [run.pk, store.get_run("flock", "gone", "r2").pk]
    assert (server.files_dir / str(run.pk) / "config.yaml").is_file()
    api.create_run("kept", "r1")
    live = events("gone", "r1", client=api)
    live.next_of("hello")

    refused = api.delete(f"{PROJECTS}/flock/gone")
    assert refused.status == 409
    assert refused.json()["ids"] == ["r2"]
    assert api.delete(f"{PROJECTS}/flock/gone", force="true").status == 204
    assert live.next_of("project") == {"deleted": True}
    assert api.get(f"{PROJECTS}/flock/gone").status == 404
    assert api.get(f"{PROJECTS}/flock/gone/runs/r1").status == 404
    assert not (server.files_dir / str(run.pk)).exists()
    for table in ("runs", "history", "events", "series_chunks", "run_values", "run_files"):
        column = "pk" if table == "runs" else "run_pk"
        count = server.call(
            store.db.fetchval(f"SELECT count(*) FROM {table} WHERE {column} = ANY($1)", pks)
        )
        assert count == 0, table
    assert api.only_run("kept")["name"] == "r1"

    server.stop()
    restarted = make_flock(reuse=server).client()
    assert restarted.get(f"{PROJECTS}/flock/gone").status == 404
    assert [p["name"] for p in restarted.get(PROJECTS).json()["projects"]] == ["kept"]
