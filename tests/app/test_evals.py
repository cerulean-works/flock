"""Eval results in the dashboard (025): comparing two eval runs doc by doc on the server (D185), with eval's own diff, and reading their docs' text and answers with eval's own items (027)."""

from __future__ import annotations

import dataclasses
import inspect
import json
import re
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, get_args

import pytest

from app.api.src.api import runs
from app.api.src.api.runs import run_items
from app.api.src.core.errors import Invalid
from app.api.src.core.evals import PairFiles
from flock.eval.diff import diff
from flock.eval.items import CELLS, ONE_RUN, items
from flock.eval.results import READ_SCHEMAS, Results, WrittenResults, read_results
from tests.eval.toy import PROVENANCE, write_run

if TYPE_CHECKING:
    from tests.app.conftest import Client, FlockServer

P = "/api/v1/projects/flock"


def log_run(api: Client, project: str, run: str, folder: str) -> None:
    """An eval run with a written run's files, uploaded under the names `log_to_tracker` gives
    them."""
    api.create_run(project, run)
    assert api.patch(f"{P}/{project}/runs/{run}", {"job_type": "eval"}).status == 200
    for path in sorted(Path(folder).rglob("*")):
        if path.is_file():
            api.upload(project, run, path.relative_to(folder).as_posix(), path.read_bytes())


def test_two_runs_compare_on_the_server_as_flock_eval_diff_does(
    api: Client, tmp_path: Path
) -> None:
    a = write_run(
        tmp_path / "a",
        {
            "t": ("h", [True, True, False, False]),
            "u": ("h", [True]),
            "v": ("h", None),
            "x": ("h", [True]),
        },
    )
    b = write_run(
        tmp_path / "b",
        {
            "t": ("h", [True, False, True, False]),
            "u": ("other", [True]),
            "v": ("h", [True]),
            "w": ("h", [True]),
        },
        dataclasses.replace(PROVENANCE, model="tiny@1"),
    )
    log_run(api, "p", "a", a)
    log_run(api, "q", "b", b)  # another project of the org

    found = api.get(f"{P}/p/runs/a/diff", **{"with": "flock/q/b"})

    assert found.status == 200, found.body
    # Exactly the JSON `flock eval diff --json` prints for the two folders.
    expected = json.loads(json.dumps(diff(read_results(a), read_results(b)).to_dict()))
    assert found.json() == expected
    assert found.json()["tasks"][0]["only_a"] == ["d-1"]
    assert found.json()["not_compared"] == [
        ["u", "task_hash"],
        ["v", "no_predictions"],
        ["x", "only_a"],
        ["w", "only_b"],
    ]


def test_either_private_run_is_the_same_404_to_outsiders(
    flock: FlockServer, api: Client, tmp_path: Path
) -> None:
    a = write_run(tmp_path / "a", {"t": ("h", [True, False])})
    b = write_run(tmp_path / "b", {"t": ("h", [True, False])})
    log_run(api, "p", "a", a)
    log_run(api, "q", "b", b)
    assert api.post("/api/v1/orgs", {"name": "lab"}).status == 201
    outsider = flock.add_user("o@x.io", org="lab").api
    anon = flock.anonymous()
    route = f"{P}/p/runs/a/diff"

    for client in (outsider, anon):
        refused = client.get(route, **{"with": "flock/q/b"})
        assert refused.status == 404
        assert refused.json()["error"] == "project flock/p not found"

    # A public A doesn't open a private B: the answer is the one for a project that isn't there.
    assert api.patch(f"{P}/p", {"visibility": "public"}).status == 200
    for client in (outsider, anon):
        refused = client.get(route, **{"with": "flock/q/b"})
        unknown = client.get(route, **{"with": "flock/nope/b"})
        assert refused.status == unknown.status == 404
        assert refused.body == unknown.body.replace(b"nope", b"q")

    assert api.patch(f"{P}/q", {"visibility": "public"}).status == 200
    for client in (outsider, anon):
        assert client.get(route, **{"with": "flock/q/b"}).status == 200


def folder_file(folder: str, name: str) -> str | None:
    """A written run's file as text, or None if the folder has no such file."""
    path = Path(folder) / name
    return path.read_text() if path.is_file() else None


def keep_private(folder: str, task: str) -> None:
    """Marks `task`'s dataset private and moves its predictions under `private/`, as
    `log_to_tracker` uploads a task on a private dataset."""
    path = Path(folder) / "results.json"
    data = json.loads(path.read_text())
    for entry in data["tasks"]:
        if entry["task"] == task:
            entry["dataset"]["private"] = True
    path.write_text(json.dumps(data))
    (Path(folder) / "private" / "predictions").mkdir(parents=True)
    rows = Path(folder) / "predictions" / f"{task}.jsonl"
    rows.rename(Path(folder) / "private" / "predictions" / f"{task}.jsonl")


def test_private_predictions_are_compared_for_members_only(
    flock: FlockServer, api: Client, tmp_path: Path
) -> None:
    """A task on a private dataset keeps its rows under `private/` (D181): the org's members
    compare them, and anyone else gets the task as `no_predictions`, with no count or doc id."""
    a = write_run(tmp_path / "a", {"t": ("h", [True, False]), "exam": ("e", [True, False])})
    b = write_run(tmp_path / "b", {"t": ("h", [True, True]), "exam": ("e", [False, True])})
    for folder in (a, b):
        keep_private(folder, "exam")
    log_run(api, "p", "a", a)
    log_run(api, "p", "b", b)
    assert api.patch(f"{P}/p", {"visibility": "public"}).status == 200
    member = flock.add_user("m@x.io").api
    assert api.post("/api/v1/orgs", {"name": "lab"}).status == 201
    outsider = flock.add_user("o@x.io", org="lab").api
    route = f"{P}/p/runs/a/diff"
    # What `flock eval diff` prints for the two runs as their org's members read them.
    on_run = [
        WrittenResults.read(f"run:flock/p/{name}", partial(folder_file, folder))
        for name, folder in (("a", a), ("b", b))
    ]
    expected = diff(*on_run).to_dict()

    for client in (api, member):
        found = client.get(route, **{"with": "flock/p/b"})
        assert found.status == 200, found.body
        assert found.json() == json.loads(json.dumps(expected))
        exam = found.json()["tasks"][1]
        assert (exam["task"], exam["only_a"], exam["only_b"]) == ("exam", ["d-0"], ["d-1"])
    for client in (outsider, flock.anonymous()):
        found = client.get(route, **{"with": "flock/p/b"})
        assert found.status == 200, found.body
        assert [task["task"] for task in found.json()["tasks"]] == ["t"]
        assert found.json()["not_compared"] == [["exam", "no_predictions"]]


def test_runs_without_results_or_not_comparable_are_refused(api: Client, tmp_path: Path) -> None:
    a = write_run(tmp_path / "a", {"t": ("h", [True, False])})
    bf16 = dataclasses.replace(PROVENANCE, dtype="bfloat16")
    b = write_run(tmp_path / "b", {"t": ("h", [True, False])}, bf16)
    log_run(api, "p", "a", a)
    log_run(api, "p", "b", b)
    api.create_run("p", "train")  # no results.json
    route = f"{P}/p/runs/a/diff"

    other = api.get(route, **{"with": "flock/p/b"})
    assert other.status == 409
    assert "different instrument: provenance.dtype" in other.json()["error"]

    missing = api.get(route, **{"with": "flock/p/train"})
    assert missing.status == 404
    assert missing.json()["error"] == "run:flock/p/train has no results.json"
    for bad in ("flock/p", "flock/p/b/x", "flock//b", "flock/p/nope"):
        refused = api.get(route, **{"with": bad})
        assert refused.status == 404, bad
    assert api.get(f"{P}/p/runs/nope/diff", **{"with": "flock/p/b"}).status == 404


def test_a_file_over_the_limit_is_refused_before_it_is_read(
    flock: FlockServer, api: Client
) -> None:
    """The diff reads files of at most 64 MiB, so one comparison can't hold any size in memory."""
    api.create_run("p", "r")
    api.upload("p", "r", "predictions/t.jsonl", b"x" * 10)
    project = flock.call(flock.store.project("flock", "p"))
    run = flock.call(flock.store.project_run(project, "r"))

    assert flock.call(flock.store.read_file(run, "predictions/t.jsonl", max_bytes=10)) == b"x" * 10
    with pytest.raises(Invalid, match="more than the 9 bytes"):
        flock.call(flock.store.read_file(run, "predictions/t.jsonl", max_bytes=9))


def test_a_file_the_caller_may_not_read_still_counts_against_the_budget(
    flock: FlockServer, api: Client
) -> None:
    api.create_run("p", "r")
    project = flock.call(flock.store.project("flock", "p"))
    run = flock.call(flock.store.project_run(project, "r"))
    # The server's running loop, so a read the budget should have refused fails the test.
    files = PairFiles(flock.store, flock.loop, lookups=1)

    assert files.read(run, "private/predictions/t.jsonl", readable=False) is None
    with pytest.raises(ValueError, match="more files than one comparison reads"):
        files.read(run, "predictions/t.jsonl")


def test_items_are_eval_items_paged(api: Client, tmp_path: Path) -> None:
    """One run's docs by outcome, or two runs' by cell, are exactly `items()`'s, a page at a time, with `total` counting them all."""
    a = write_run(tmp_path / "a", {"t": ("h", [True, False, False, True, False])})
    b = write_run(tmp_path / "b", {"t": ("h", [True, True, False, False, False])})
    log_run(api, "p", "a", a)
    log_run(api, "q", "b", b)
    runs = [read_results(a), read_results(b)]
    route = f"{P}/p/runs/a/items"

    def expected(keep: str, *of: WrittenResults) -> list[dict]:
        return json.loads(json.dumps([i.to_dict() for i in items(of, "t", keep)]))

    wrong = api.get(route, task="t", keep="wrong")
    assert wrong.status == 200, wrong.body
    assert wrong.json() == {"total": 3, "offset": 0, "items": expected("wrong", runs[0])}
    page = api.get(route, task="t", keep="wrong", offset=1, limit=1).json()
    assert page == {"total": 3, "offset": 1, "items": expected("wrong", runs[0])[1:2]}
    assert [i["id"] for i in api.get(route, task="t", keep="all").json()["items"]] == [
        f"d-{i}" for i in range(5)
    ]
    for cell in ("only_a", "only_b", "both_wrong", "both_right"):
        found = api.get(route, task="t", keep=cell, **{"with": "flock/q/b"})
        assert found.status == 200, found.body
        assert found.json()["items"] == expected(cell, *runs), cell
    assert api.get(route, task="t", keep="only_a", **{"with": "flock/q/b"}).json()["total"] == 1
    assert api.get(route, task="t", keep="wrong", offset=9).json()["items"] == []


def test_items_come_by_margin_unless_asked_for_the_datasets_order(
    api: Client, tmp_path: Path
) -> None:
    """A run's margin is its gold choice's score minus the best wrong one's: one run lists its surest docs first, right or wrong, and two runs the docs whose margins differ most (D198)."""

    def with_logprobs(folder: str, task: str, logprobs: list[list[float]]) -> str:
        """Gives a written run's docs these choice logprobs, each doc's pick its best choice and its outcome whether that is gold (choice 0), as `acc_raw` scores them."""
        path = Path(folder) / "predictions" / f"{task}.jsonl"
        header, *rows = [json.loads(line) for line in path.read_text().splitlines()]
        for row, logprob in zip(rows, logprobs, strict=True):
            pick = logprob.index(max(logprob))
            row |= {"logprob": logprob, "pick": pick, "correct": pick == 0}
        path.write_text("".join(json.dumps(r) + "\n" for r in [header, *rows]))
        return folder

    # Margins (gold first): A 1, -3, 0.5, 2; B 1, 1, 0.5, -2.
    a = [[-1.0, -2.0], [-4.0, -1.0], [-1.0, -1.5], [-1.0, -3.0]]
    b = [[-1.0, -2.0], [-1.0, -2.0], [-1.0, -1.5], [-3.0, -1.0]]
    runs = (("a", a, [True, False, True, True]), ("b", b, [True, True, True, False]))
    for name, logprobs, outcomes in runs:
        folder = write_run(tmp_path / name, {"t": ("h", outcomes)})
        log_run(api, "p", name, with_logprobs(folder, "t", logprobs))
    route = f"{P}/p/runs/a/items"

    def ids(**query: Any) -> list[str]:
        found = api.get(route, task="t", **query)
        assert found.status == 200, found.body
        return [item["id"] for item in found.json()["items"]]

    assert ids(keep="all") == ["d-1", "d-3", "d-0", "d-2"]
    assert ids(keep="all", sort="doc") == ["d-0", "d-1", "d-2", "d-3"]
    assert ids(keep="wrong") == ["d-1"]
    # Gaps: d-0 0, d-2 0 (both right); d-1 4 (only B right), d-3 4 (only A right).
    assert ids(keep="both_right", **{"with": "flock/p/b"}) == ["d-0", "d-2"]
    assert ids(keep="only_b", **{"with": "flock/p/b"}) == ["d-1"]
    assert api.get(route, task="t", keep="all", sort="best").status == 422


def test_a_page_of_items_over_its_budget_is_refused(
    api: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run's model name repeats in every answer, so the page is held to a size of its own."""
    log_run(api, "p", "a", write_run(tmp_path / "a", {"t": ("h", [False, False])}))
    route = f"{P}/p/runs/a/items"
    one = len(api.get(route, task="t", keep="wrong", limit=1).body)

    # The whole answer counts, its envelope and separators too.
    monkeypatch.setattr(runs, "PAGE_BYTES", one)
    assert api.get(route, task="t", keep="wrong", limit=1).status == 200
    monkeypatch.setattr(runs, "PAGE_BYTES", one - 1)
    assert api.get(route, task="t", keep="wrong", limit=1).status == 409
    monkeypatch.setattr(runs, "PAGE_BYTES", one)
    over = api.get(route, task="t", keep="wrong", limit=2)
    assert over.status == 409 and "ask for fewer" in over.json()["error"]


def test_items_are_refused_as_eval_refuses_them(api: Client, tmp_path: Path) -> None:
    """A run before schema 11 has no text, and eval says how to get it; a keep that doesn't fit the number of runs, or an unknown task, is eval's refusal too; an unknown keep is invalid."""
    log_run(api, "p", "new", write_run(tmp_path / "new", {"t": ("h", [True, False])}))
    log_run(api, "p", "old", write_run(tmp_path / "old", {"t": ("h", [True, False])}, schema=10))
    api.create_run("p", "train")  # no results.json
    route = f"{P}/p/runs/new/items"

    old = api.get(f"{P}/p/runs/old/items", task="t", keep="wrong")
    assert old.status == 409
    assert "schema 10" in old.json()["error"] and "score the model again" in old.json()["error"]
    pair = api.get(route, task="t", keep="only_a", **{"with": "flock/p/old"})
    assert pair.status == 409 and "schema 10" in pair.json()["error"]
    assert api.get(route, task="t", keep="only_a").status == 409
    assert api.get(route, task="t", keep="wrong", **{"with": "flock/p/new"}).status == 409
    assert api.get(route, task="nope", keep="wrong").status == 409
    assert api.get(route, task="t", keep="worst").status == 422
    assert api.get(route, task="t", keep="wrong", limit=101).status == 422
    missing = api.get(route, task="t", keep="only_a", **{"with": "flock/p/train"})
    assert missing.status == 404
    assert missing.json()["error"] == "run:flock/p/train has no results.json"
    for bad in ("flock/p", "flock/nope/b", "flock/p/nope"):
        assert api.get(route, task="t", keep="only_a", **{"with": bad}).status == 404, bad


def test_a_private_tasks_items_reach_members_only(
    flock: FlockServer, api: Client, tmp_path: Path
) -> None:
    """On a public project, a task on a private dataset (D181) lists its text for the org's admin and members; an outsider and an anonymous viewer get eval's refusal, holding no text, while the public task's items reach everyone."""
    a = write_run(tmp_path / "a", {"t": ("h", [True, False]), "exam": ("e", [True, False])})
    b = write_run(tmp_path / "b", {"t": ("h", [True, True]), "exam": ("e", [False, True])})
    for folder in (a, b):
        keep_private(folder, "exam")
    log_run(api, "p", "a", a)
    log_run(api, "p", "b", b)
    assert api.patch(f"{P}/p", {"visibility": "public"}).status == 200
    member = flock.add_user("m@x.io").api
    assert api.post("/api/v1/orgs", {"name": "lab"}).status == 201
    outsider = flock.add_user("o@x.io", org="lab").api
    route = f"{P}/p/runs/a/items"

    for client in (api, member):
        found = client.get(route, task="exam", keep="only_a", **{"with": "flock/p/b"})
        assert found.status == 200, found.body
        assert found.json()["items"][0]["question"] == "Question: exam 0?\nAnswer:"
        assert client.get(route, task="exam", keep="all").status == 200
    for client in (outsider, flock.anonymous()):
        pair = client.get(route, task="exam", keep="only_a", **{"with": "flock/p/b"})
        one = client.get(route, task="exam", keep="all")
        for refused in (pair, one):
            assert refused.status == 409
            assert "has no predictions for exam" in refused.json()["error"]
            assert b"Question" not in refused.body
        public = client.get(route, task="t", keep="only_b", **{"with": "flock/p/b"})
        assert public.json()["total"] == 1


def test_the_items_route_takes_the_keeps_eval_takes() -> None:
    """The route's `keep` values are eval's: one it lacked would be a 422 for a filter `items()` reads, and an extra one a 409 nobody could get past."""
    keep = inspect.signature(run_items).parameters["keep"].annotation
    assert get_args(get_args(keep)[0]) == ONE_RUN + CELLS


def test_the_dashboard_reads_the_schemas_eval_reads() -> None:
    """The web's `RESULT_SCHEMAS` is eval's `READ_SCHEMAS`: a schema on one side only would hide
    results the other reads, or show a layout the pages don't check."""
    source = Path(__file__).parents[2] / "app/web/src/lib/evals.ts"
    found = re.search(r"export const RESULT_SCHEMAS = \[([0-9, ]*)\];", source.read_text())
    assert found is not None
    assert tuple(int(n) for n in found[1].split(",")) == READ_SCHEMAS
    assert Results(PROVENANCE, []).schema_version in READ_SCHEMAS
