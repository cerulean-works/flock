"""Run groups (026): a group is a list of runs (D216), its new matches, bests with seeds
(D217), column kinds (D218), suggestions (D219), Compare (D221), who may read and change
groups (D226), and the proposals route (D229)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

import httpx

from app.api.src.core import proposals
from app.api.src.core.group_analysis import signature

if TYPE_CHECKING:
    from typing import Any

    import pytest

    from tests.app.conftest import Client, Flocks, FlockServer

P = "/api/v1/projects/flock/p"
G = f"{P}/groups"


def log(
    api: Client,
    name: str,
    config: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    group: str | None = None,
    sweep: str | None = None,
    finished: bool = True,
    project: str = "p",
) -> None:
    """Log a run as the SDK does: UpsertBucket with its config, W&B group and sweep, then its
    summary, and finish it unless ``finished`` is false."""
    query = """
    mutation UpsertBucket($name: String, $project: String, $entity: String,
                          $config: JSONString, $groupName: String, $sweep: String) {
      upsertBucket(input: {name: $name, modelName: $project, entityName: $entity,
                           config: $config, groupName: $groupName, sweep: $sweep}) {
        bucket { id name }
      }
    }
    """
    variables = {
        "name": name,
        "project": project,
        "entity": "flock",
        "config": json.dumps({k: {"value": v} for k, v in (config or {}).items()}),
        "groupName": group,
        "sweep": sweep,
    }
    resp = api.graphql(query, variables)
    assert resp.status == 200 and "errors" not in resp.json(), resp.body
    if summary is not None or finished:
        api.stream(project, name, summary=summary, complete=0 if finished else None)


def create(api: Client, **body: Any) -> dict:
    resp = api.post(G, body)
    assert resp.status == 201, resp.body
    return resp.json()


def group(api: Client, pk: int) -> dict:
    resp = api.get(f"{G}/{pk}")
    assert resp.status == 200, resp.body
    return resp.json()


def patch(api: Client, pk: int, **body: Any) -> dict:
    resp = api.patch(f"{G}/{pk}", body)
    assert resp.status == 200, resp.body
    return resp.json()


def new_ids(api: Client, pk: int) -> list[str]:
    return [run["id"] for run in api.get(f"{G}/{pk}/new").json()["runs"]]


def listed(api: Client) -> dict[str, dict]:
    return {g["name"]: g for g in api.get(G).json()["groups"]}


ADAM = {"config.opt": "adam"}


def three_runs(api: Client) -> None:
    log(api, "a", {"opt": "adam", "lr": 0.1})
    log(api, "b", {"opt": "adam", "lr": 0.2})
    log(api, "c", {"opt": "sgd", "lr": 0.1})


# ---------------------------------------------------------------------- membership (D216)


def test_create_from_a_filter_by_hand_and_both(api: Client) -> None:
    three_runs(api)
    by_filter = create(api, name="adam", filter=ADAM, runs=["a", "b"])
    assert (by_filter["runs"], by_filter["dropped"]) == (["a", "b"], [])
    assert by_filter["added_by"] == {"a": "filter", "b": "filter"}
    assert (by_filter["run_count"], by_filter["new_count"]) == (2, 0)
    assert by_filter["filter"] == ADAM
    assert by_filter["color"].startswith("#")

    by_hand = create(api, name="picked", runs=["c", "a"])
    assert by_hand["runs"] == ["a", "c"]  # in creation order
    assert by_hand["added_by"] == {"a": "hand", "c": "hand"}
    assert by_hand["filter"] == {}
    assert new_ids(api, by_hand["id"]) == []  # no filter: never any new matches

    # The filter's runs, plus one ticked by hand, minus one unticked: dropped, never offered.
    both = create(api, name="both", filter=ADAM, runs=["a", "c"])
    assert both["runs"] == ["a", "c"]
    assert both["added_by"] == {"a": "filter", "c": "hand"}
    assert both["dropped"] == ["b"]
    assert new_ids(api, both["id"]) == []

    assert api.post(G, {"name": "adam", "runs": []}).status == 409  # names are unique
    missing = api.post(G, {"name": "x", "runs": ["a", "ghost"]})
    assert (missing.status, missing.json()["ids"]) == (404, ["ghost"])
    assert api.post(G, {"name": "x", "filter": {"$bogus": 1}}).status == 422
    assert api.post(G, {"name": "  ", "runs": []}).status == 422
    assert api.post(G, {"name": "x", "color": "red"}).status == 422
    assert set(listed(api)) == {"adam", "picked", "both"}


def test_a_matching_run_logged_later_is_offered_not_added(api: Client) -> None:
    three_runs(api)
    made = create(api, name="adam", filter=ADAM, runs=["a", "b"])
    log(api, "d", {"opt": "adam", "lr": 0.3})
    found = group(api, made["id"])
    assert (found["runs"], found["run_count"], found["new_count"]) == (["a", "b"], 2, 1)
    new = api.get(f"{G}/{made['id']}/new").json()["runs"]
    assert [(r["id"], r["state"]) for r in new] == [("d", "finished")]
    assert listed(api)["adam"]["new_count"] == 1

    added = patch(api, made["id"], add=["d"], loaded=True)
    assert added["runs"] == ["a", "b", "d"]
    assert added["added_by"]["d"] == "filter"
    assert (added["run_count"], added["new_count"]) == (3, 0)
    loaded = datetime.fromisoformat(added["loaded_at"])
    assert loaded > datetime.fromisoformat(made["loaded_at"])


def test_a_run_that_matches_only_once_it_finishes_is_offered(api: Client) -> None:
    log(api, "a", {"opt": "adam"})
    log(api, "e", {"opt": "adam"}, finished=False)
    made = create(api, name="done", filter={"state": "finished"}, runs=["a"])
    assert new_ids(api, made["id"]) == []  # running: not a match yet
    api.stream("p", "e", complete=0)
    assert new_ids(api, made["id"]) == ["e"]
    assert group(api, made["id"])["runs"] == ["a"]  # offered, never added


def test_a_dropped_run_is_never_offered_again(api: Client) -> None:
    three_runs(api)
    made = create(api, name="adam", filter=ADAM, runs=["a"])
    assert made["dropped"] == ["b"]  # unticked at creation
    log(api, "d", {"opt": "adam"})
    assert new_ids(api, made["id"]) == ["d"]
    dropped = patch(api, made["id"], drop=["d", "a"])
    assert (dropped["runs"], dropped["dropped"]) == ([], ["a", "b", "d"])
    assert new_ids(api, made["id"]) == []

    restored = patch(api, made["id"], restore=["d"])
    assert restored["dropped"] == ["a", "b"]
    assert new_ids(api, made["id"]) == ["d"]  # offered again, not added
    assert api.patch(f"{G}/{made['id']}", {"add": ["d"], "drop": ["d"]}).status == 400
    assert api.patch(f"{G}/{made['id']}", {"add": ["ghost"]}).status == 404


def test_changing_the_filter_never_removes_a_member(api: Client) -> None:
    three_runs(api)
    made = create(api, name="adam", filter=ADAM, runs=["a", "b"])
    changed = patch(api, made["id"], filter={"config.opt": "sgd"}, name="renamed")
    assert (changed["name"], changed["runs"]) == ("renamed", ["a", "b"])
    assert new_ids(api, made["id"]) == ["c"]
    cleared = patch(api, made["id"], filter=None)
    assert (cleared["filter"], cleared["runs"]) == ({}, ["a", "b"])
    assert new_ids(api, made["id"]) == []


def test_reading_a_groups_runs_is_the_runs_query(api: Client) -> None:
    three_runs(api)
    made = create(api, name="g", runs=["a", "c"])
    path = f"{G}/{made['id']}/runs/query"
    resp = api.post(path, {"columns": ["config.lr"], "sort": ["name"]}).json()
    assert (resp["ids"], resp["total"], resp["data"]) == (["a", "c"], 2, {"config.lr": [0.1, 0.1]})
    narrowed = api.post(path, {"filter": {"config.opt": "sgd"}}).json()
    assert narrowed["ids"] == ["c"]
    assert api.post(f"{G}/999/runs/query", {}).status == 404
    empty = create(api, name="empty", runs=[])
    assert api.post(f"{G}/{empty['id']}/runs/query", {}).json()["total"] == 0


def test_a_groups_workspace_is_the_snapshot_of_its_runs(api: Client) -> None:
    three_runs(api)
    made = create(api, name="g", runs=["a", "c"])
    path = f"{G}/{made['id']}/workspace"

    def header(**params: Any) -> dict:
        resp = api.get(path, **params)
        assert resp.status == 200, resp.body
        assert resp.body[:4] == b"FLK2"
        n = int.from_bytes(resp.body[4:8], "little")
        return json.loads(resp.body[8 : 8 + n])

    whole = header(sort="name")
    assert ([r["id"] for r in whole["runs"]], whole["total"]) == (["a", "c"], 2)
    narrowed = header(filter=json.dumps({"config.opt": "sgd"}))
    assert ([r["id"] for r in narrowed["runs"]], narrowed["total"]) == (["c"], 1)
    assert api.get(f"{G}/999/workspace").status == 404
    counted = api.get(f"{G}/{made['id']}/runs/values", keys="config.opt").json()["values"]
    assert counted["config.opt"] == [{"value": "adam", "runs": 1}, {"value": "sgd", "runs": 1}]


def test_deleted_and_moved_runs_leave_their_groups(api: Client) -> None:
    three_runs(api)
    made = create(api, name="g", filter=ADAM, runs=["a", "b", "c"])
    assert api.delete(f"{P}/runs/a").status == 204
    assert group(api, made["id"])["runs"] == ["b", "c"]

    moved = api.post(f"{P}/runs/move", {"runs": ["b"], "to": "q", "create": True})
    assert moved.status == 200, moved.body
    assert group(api, made["id"])["runs"] == ["c"]
    # Moved back, it's a new match again: its rows went with the move (D216).
    assert api.post("/api/v1/projects/flock/q/runs/move", {"runs": ["b"], "to": "p"}).status == 200
    assert group(api, made["id"])["runs"] == ["c"]
    assert new_ids(api, made["id"]) == ["b"]

    assert api.delete(f"{G}/{made['id']}").status == 204
    assert api.get(f"{G}/{made['id']}").status == 404
    assert sorted(api.runs("p")["ids"]) == ["b", "c"]  # its runs stay
    assert api.get(G).json()["ungrouped_count"] == 2


# ---------------------------------------------------------------------- kinds and best


def kinds_of(api: Client, pk: int) -> dict[str, str]:
    return {c["key"]: c["kind"] for c in api.get(f"{G}/{pk}/columns").json()["columns"]}


def test_column_kinds(api: Client) -> None:
    for i, (lr, bs) in enumerate([(0.1, 32), (0.1, 64), (0.2, 32), (0.2, 64)]):
        config = {
            "lr": lr,
            "batch_size": bs,
            "seed": i,
            "model": "1b",
            "out_dir": f"/ckpt/run-{i}",
            "wd": 0.01 * (i + 1) ** 1.5,  # a random search: distinct in every run
        }
        if i < 2:
            config["warmup"] = 100  # missing in some, the same in the rest
        log(api, f"r{i}", config)
    made = create(api, name="sweep", runs=["r0", "r1", "r2", "r3"])
    assert kinds_of(api, made["id"]) == {
        "config.lr": "varies",
        "config.batch_size": "varies",
        "config.seed": "repeat",
        "config.model": "same",
        "config.out_dir": "unique",
        "config.wd": "varies",
        "config.warmup": "varies",
    }
    assert group(api, made["id"])["repeat_key"] == "config.seed"
    columns = {c["key"]: c for c in api.get(f"{G}/{made['id']}/columns").json()["columns"]}
    assert columns["config.lr"]["distinct"] == 2
    assert not columns["config.out_dir"]["overridden"]

    # Dragged by hand: saved on the group.
    patch(api, made["id"], columns={"config.out_dir": "varies"})
    assert kinds_of(api, made["id"])["config.out_dir"] == "varies"
    columns = {c["key"]: c for c in api.get(f"{G}/{made['id']}/columns").json()["columns"]}
    assert columns["config.out_dir"]["overridden"]

    # Under 4 runs, strings distinct in every run are an axis, not paths.
    small = create(api, name="small", runs=["r0", "r1", "r2"])
    assert kinds_of(api, small["id"])["config.out_dir"] == "varies"


def test_seeds_and_nan_is_never_best(api: Client) -> None:
    losses = {(0.1, 0): 1.0, (0.1, 1): 1.2, (0.2, 0): 0.9, (0.2, 1): 1.5, (0.3, 0): float("nan")}
    for i, ((lr, seed), loss) in enumerate(losses.items()):
        log(api, f"r{i}", {"lr": lr, "seed": seed}, {"val/loss": loss})
    made = create(api, name="seeds", runs=[f"r{i}" for i in range(5)])
    # Means: lr 0.1 → 1.1, lr 0.2 → 1.2; lr 0.3 has no finite value and is never best.
    best = group(api, made["id"])["best"]
    assert best["run"] == "r0"
    assert abs(best["value"] - 1.1) < 1e-9
    assert abs(best["std"] - 0.1) < 1e-9
    assert best["n"] == 2

    single = create(api, name="nan", runs=["r4"])
    assert group(api, single["id"])["best"] is None

    # The project's default best by is summary.val/loss, lowest first (D217).
    body = api.get(G).json()
    assert (body["best_key"], body["best_order"]) == ("summary.val/loss", "asc")
    resp = api.request(
        "PUT", f"{G}/settings", body={"best_key": "summary.val/loss", "best_order": "desc"}
    )
    assert resp.status == 200, resp.body
    assert listed(api)["seeds"]["best"]["run"] == "r3"  # the highest mean: lr 0.2
    # A group's own setting wins.
    patch(api, made["id"], best_key="summary.val/loss", best_order="asc")
    assert listed(api)["seeds"]["best"]["run"] == "r0"


def test_primary_columns_without_the_assistant_are_the_varying_keys_and_best(
    api: Client,
) -> None:
    for i, (lr, bs) in enumerate([(0.1, 32), (0.1, 64), (0.2, 32), (0.2, 64)]):
        config = {"lr": lr, "batch_size": bs, "seed": i, "model": "1b", "out": f"/ckpt/{i}"}
        log(api, f"r{i}", config, {"val/loss": 1.0 + i, "tokens_per_sec": 100.0})
    made = create(api, name="sweep", runs=["r0", "r1", "r2", "r3"])
    path = f"{G}/{made['id']}/primary"
    expected = ["config.batch_size", "config.lr", "summary.val/loss", "summary.tokens_per_sec"]
    assert api.get(path).json() == {"keys": expected, "source": "analysis"}
    # Turned on, but this server has no OpenAI API key: still the analysis, never an error.
    assert api.patch("/api/v1/orgs/flock", {"assistant_enabled": True}).status == 200
    assert api.get(path).json() == {"keys": expected, "source": "analysis"}
    # The group's own best-by column is always among them.
    patch(api, made["id"], best_key="summary.tokens_per_sec", best_order="desc")
    assert api.get(path).json()["keys"] == [
        *expected[:2],
        "summary.tokens_per_sec",
        "summary.val/loss",
    ]
    assert api.get(f"{G}/12345/primary").status == 404


def test_compare(api: Client) -> None:
    log(api, "base", {"lr": 0.1, "opt": "adam"}, {"val/loss": 2.0})
    log(api, "a1", {"lr": 0.1, "opt": "adam"}, {"val/loss": 1.5})
    log(api, "a2", {"lr": 0.2, "opt": "adam"}, {"val/loss": 1.0})
    log(api, "s1", {"lr": 0.1, "opt": "sgd"}, {"val/loss": 1.8})
    log(api, "lone", {"lr": 0.3, "opt": "adam"}, {"val/loss": 0.5})
    log(api, "lost", {"lr": 0.3, "opt": "adam"})
    adam = create(api, name="adam", runs=["a1", "a2"])
    sgd = create(api, name="sgd", runs=["s1"])
    other = create(api, name="other", runs=["s1"], best_key="summary.other")

    body = api.get(f"{G}/compare").json()
    assert (body["best_key"], body["best_order"]) == ("summary.val/loss", "asc")
    assert "reference" not in body
    rows = [(r["group"], r["run"], r["value"], r["runs"]) for r in body["rows"]]
    assert rows == [
        (adam["id"], "a2", 1.0, 2),
        (sgd["id"], "s1", 1.8, 1),
        (other["id"], None, None, 1),  # ranked by another column: last, named
    ]
    assert body["rows"][2]["best_key"] == "summary.other"
    # Every member's value, for the chart of each group's spread.
    assert sorted(body["rows"][0]["values"]) == [1.0, 1.5]
    assert body["rows"][2]["values"] == []
    # The best run's summary numbers, for the suggested metric columns.
    assert body["rows"][0]["summary"] == {"summary.val/loss": 1.0}
    assert body["rows"][2]["summary"] == {}
    assert body["ungrouped"][0]["summary"] == {"summary.val/loss": 0.5}
    assert body["keys"] == ["config.lr", "config.opt"]
    assert [r["run"] for r in body["ungrouped"]] == ["lone", "base", "lost"]
    assert body["ungrouped_count"] == 3


# ---------------------------------------------------------------------- suggestions (D219)


def test_suggestions_from_wandb_groups_and_config_differences(api: Client) -> None:
    log(api, "w1", {"lr": 0.1, "model": "a"}, group="pretrain-1b")
    log(api, "w2", {"lr": 0.2, "model": "b"}, group="pretrain-1b")
    for i in range(3):
        log(api, f"c{i}", {"lr": 0.1 * (i + 1), "model": "c", "data": "web"}, sweep="sw1")
    log(api, "x", {"lr": 9.0, "model": "zzz", "data": "books", "bs": 1, "wd": 2}, sweep="sw1")

    found = api.get(f"{G}/suggestions").json()["suggestions"]
    by_source = {s["source"]: s for s in found}
    assert set(by_source) == {"wandb_group", "config_diff"}
    wandb = by_source["wandb_group"]
    assert (wandb["runs"], wandb["filter"]) == (["w1", "w2"], {"group": "pretrain-1b"})
    assert wandb["name"].startswith("pretrain-1b")
    cluster = by_source["config_diff"]
    assert (cluster["runs"], cluster["filter"]) == (["c0", "c1", "c2"], None)
    # The sweep attribute shared by c0-c2 and x made no suggestion of its own.
    assert not any("x" in s["runs"] for s in found)
    assert wandb["signature"] == signature(["w1", "w2"]).hex()

    assert api.post(f"{G}/suggestions/dismiss", {"signature": wandb["signature"]}).status == 204
    left = api.get(f"{G}/suggestions").json()["suggestions"]
    assert [s["source"] for s in left] == ["config_diff"]
    assert api.post(f"{G}/suggestions/dismiss", {"signature": "zz"}).status == 422

    # Runs already in a saved group aren't suggested.
    create(api, name="mine", runs=["c0"])
    assert all("c0" not in s["runs"] for s in api.get(f"{G}/suggestions").json()["suggestions"])


def test_a_wandb_group_a_saved_group_filters_on_isnt_suggested_again(api: Client) -> None:
    log(api, "o1", {"lr": 0.1}, group="other")
    log(api, "o2", {"lr": 0.2}, group="other")
    # As a suggestion saves its filter, and as the filter popover does.
    for name, saved in (("s", {"group": "s"}), ("t", {"group": {"$eq": "t"}})):
        log(api, f"{name}1", {"lr": 0.1}, group=name)
        log(api, f"{name}2", {"lr": 0.2}, group=name)
        made = create(api, name=name, filter=saved, runs=[f"{name}1", f"{name}2"])
        # Two later runs of the W&B group are the saved group's new matches, not a suggestion.
        log(api, f"{name}3", {"lr": 0.3}, group=name)
        log(api, f"{name}4", {"lr": 0.4}, group=name)
        found = api.get(f"{G}/suggestions").json()["suggestions"]
        assert [s["filter"] for s in found] == [{"group": "other"}]
        assert sorted(new_ids(api, made["id"])) == [f"{name}3", f"{name}4"]


# ---------------------------------------------------------------------- access (D226)


def test_outsiders_read_groups_of_public_projects_and_members_change_them(
    flock: FlockServer,
) -> None:
    api = flock.client()
    three_runs(api)
    made = create(api, name="adam", filter=ADAM, runs=["a", "b"])
    assert api.post("/api/v1/orgs", {"name": "lab"}).status == 201
    member = flock.add_user("m@x.io").api
    outsider = flock.add_user("o@x.io", org="lab").api
    anon = flock.anonymous()
    pk = made["id"]
    reads = [
        ("GET", G, None),
        ("GET", f"{G}/compare", None),
        ("GET", f"{G}/suggestions", None),
        ("GET", f"{G}/{pk}", None),
        ("POST", f"{G}/{pk}/runs/query", {}),
        ("GET", f"{G}/{pk}/workspace", None),
        ("GET", f"{G}/{pk}/runs/values?keys=state", None),
        ("GET", f"{G}/{pk}/columns", None),
        ("GET", f"{G}/{pk}/new", None),
        ("GET", f"{G}/{pk}/primary", None),
    ]
    writes = [
        ("PUT", f"{G}/settings", {"best_key": "summary.loss"}),
        ("POST", G, {"name": "theirs", "runs": ["a"]}),
        ("PATCH", f"{G}/{pk}", {"name": "theirs"}),
        ("POST", f"{G}/suggestions/dismiss", {"signature": "00" * 32}),
        ("POST", f"{G}/assist", {"request": "adam runs"}),
        ("DELETE", f"{G}/{pk}", None),
    ]
    for client in (outsider, anon):
        for method, path, body in reads:
            assert client.request(method, path, body=body).status == 404, (method, path)
    assert api.patch(P, {"visibility": "public"}).status == 200
    for client in (outsider, anon):
        for method, path, body in reads:
            resp = client.request(method, path, body=body)
            assert resp.status == 200, (method, path, resp.body)
        assert client.get(G).json()["assistant"] is False  # never for outsiders (D224)
        for method, path, body in writes:
            expected = 401 if client is anon else 404
            assert client.request(method, path, body=body).status == expected, (method, path)
    assert group(api, pk)["name"] == "adam"  # nothing was changed

    # Members create and edit groups, whoever logged the runs.
    theirs = member.post(G, {"name": "theirs", "runs": ["c"]})
    assert theirs.status == 201, theirs.body
    assert member.patch(f"{G}/{pk}", {"add": ["c"]}).status == 200
    assert member.delete(f"{G}/{theirs.json()['id']}").status == 204
    assert member.get(f"{G}/12345").status == 404


def test_the_assistant_is_off_until_an_org_admin_turns_it_on(flock: FlockServer) -> None:
    api = flock.client()
    member = flock.add_user("m@x.io").api
    log(api, "a", {"opt": "adam"})
    assert api.get(G).json()["assistant"] is False
    assert api.post(f"{G}/assist", {"request": "adam runs"}).status == 404
    assert api.get("/api/v1/orgs/flock").json()["assistant_enabled"] is False
    assert member.patch("/api/v1/orgs/flock", {"assistant_enabled": True}).status == 403
    turned = api.patch("/api/v1/orgs/flock", {"assistant_enabled": True})
    assert turned.status == 200 and turned.json()["assistant_enabled"] is True
    # Still off: this server has no OpenAI API key, and nothing is sent anywhere.
    assert api.get(G).json()["assistant"] is False
    assert api.post(f"{G}/assist", {"request": "adam runs"}).status == 404


def test_org_admins_pick_the_proposal_model_and_tier(flock: FlockServer) -> None:
    api = flock.client()
    member = flock.add_user("m@x.io").api
    org = "/api/v1/orgs/flock"
    assert member.patch(org, {"assistant_model": "gpt-x"}).status == 403
    set_ = api.patch(org, {"assistant_model": "gpt-6.2-astra", "assistant_tier": "ultrafast"})
    assert set_.status == 200
    got = api.get(org).json()
    assert (got["assistant_model"], got["assistant_tier"]) == ("gpt-6.2-astra", "ultrafast")
    # Another change leaves them be; a null model goes back to the server's.
    api.patch(org, {"display_name": "Flock"})
    assert api.get(org).json()["assistant_model"] == "gpt-6.2-astra"
    reset = api.patch(org, {"assistant_model": None, "assistant_tier": "standard"}).json()
    assert (reset["assistant_model"], reset["assistant_tier"]) == (None, "standard")
    for bad in ({"assistant_tier": "turbo"}, {"assistant_model": ""},
                {"assistant_model": "gpt 6"}, {"assistant_tier": None}):  # fmt: skip
        assert api.patch(org, bad).status == 422, bad


# ---------------------------------------------------------------------- proposals (D229)


def test_proposals_answer_403_while_the_assistant_is_off(flock: FlockServer) -> None:
    api = flock.client()
    log(api, "a", {"opt": "adam"})
    resp = api.post(f"{G}/propose", {"messages": []})
    assert resp.status == 403 and "off" in resp.json()["error"]
    api.patch("/api/v1/orgs/flock", {"assistant_enabled": True})
    # Still off without an OpenAI key on the server.
    assert api.post(f"{G}/propose", {"messages": []}).status == 403


def test_a_proposal_turn_checks_the_models_groups_against_the_runs(
    make_flock: Flocks, monkeypatch: pytest.MonkeyPatch
) -> None:
    flock = make_flock(openai_api_key="sk-test")
    api = flock.client()
    three_runs(api)
    log(api, "d", {"opt": "adam", "lr": 0.3}, finished=False)
    api.patch("/api/v1/orgs/flock", {"assistant_enabled": True})
    create(api, name="saved", runs=["c"])

    sent: list[dict] = []
    reply = {
        "message": "Two groups.",
        "proposals": [
            {"id": "adam", "name": "lr: 0.1, 0.2, 0.3", "why": "Adam's lr.",
             "filter": json.dumps(ADAM), "runs": ["c"], "axes": ["lr"], "best_key": None,
             "best_order": None},
            {"id": "ghost", "name": "ghost", "why": "", "filter": None,
             "runs": ["a", "nope"], "axes": [], "best_key": None, "best_order": None},
        ],
    }  # fmt: skip

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        text = json.dumps(reply)
        message = {"type": "message", "content": [{"type": "output_text", "text": text}]}
        return httpx.Response(200, json={"status": "completed", "output": [message]})

    monkeypatch.setattr(proposals, "TRANSPORT", httpx.MockTransport(handler))
    body = {
        "messages": [{"role": "user", "content": "group adam runs"}],
        "proposals": [{"id": "x", "name": "x", "runs": ["a", "b"], "grid": None}],
    }
    resp = api.post(f"{G}/propose", body)
    assert resp.status == 200, resp.body
    (request,) = sent
    assert request["model"] == "gpt-6.2-sol"
    project = request["input"][0]["content"]
    assert "flock/p, 4 live runs" in project and '- S1 "saved": 1 runs' in project
    assert request["input"][-1] == {"role": "user", "content": "group adam runs"}
    out = resp.json()
    assert out["message"] == "Two groups." and out["runs"] == 4
    # The filter's matches from the database; the one-run hand list is dropped.
    (adam,) = out["proposals"]
    assert adam["runs"] == ["a", "b", "d"] and adam["filter"] == ADAM
    assert adam["axes"] == ["config.lr"] and adam["values"] == {"config.lr": [0.1, 0.2, 0.3]}
    assert [cell["runs"] for cell in adam["grid"]["cells"]] == [1, 1, 1]
    assert out["ungrouped"] == 0  # c is saved

    # Accepting it is the create route with its ``create`` body: runs and filter.
    created = create(api, **adam["create"])
    assert created["runs"] == ["a", "b", "d"] and created["filter"] == ADAM
    assert set(created["added_by"].values()) == {"filter"} and created["dropped"] == []

    # Bad bodies are refused before anything is sent.
    bad = api.post(f"{G}/propose", {"messages": [{"role": "system", "content": "x"}]})
    assert bad.status == 422 and len(sent) == 1


def test_the_model_failing_is_a_502(make_flock: Flocks, monkeypatch: pytest.MonkeyPatch) -> None:
    flock = make_flock(openai_api_key="sk-test")
    api = flock.client()
    three_runs(api)
    api.patch("/api/v1/orgs/flock", {"assistant_enabled": True})
    monkeypatch.setattr(
        proposals,
        "TRANSPORT",
        httpx.MockTransport(lambda _: httpx.Response(500, json={"error": {"message": "down"}})),
    )
    resp = api.post(f"{G}/propose", {"messages": []})
    assert resp.status == 502 and "down" in resp.json()["error"]
