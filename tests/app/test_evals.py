"""Eval results in the dashboard (025): comparing two eval runs doc by doc on the server (D185), with eval's own diff, and reading their docs' text and answers with eval's own items (027)."""

from __future__ import annotations

import dataclasses
import inspect
import json
import re
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, get_args

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.api.src.api.runs import run_item, run_item_tokens, run_items
from app.api.src.core import evals
from app.api.src.core.errors import Invalid
from app.api.src.core.evals import PairFiles
from flock.eval.diff import diff
from flock.eval.items import CELLS, ONE_RUN, items
from flock.eval.results import READ_SCHEMAS, Results, WrittenResults, read_results
from tests.eval.toy import PROVENANCE, write_run

if TYPE_CHECKING:
    from collections.abc import Callable

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


def test_items_list_summaries_and_open_to_eval_items(api: Client, tmp_path: Path) -> None:
    """One run's docs by outcome, or two runs' by cell, are `items()`'s, a page of summaries at a time with `total` counting them all; a doc's own route answers its whole `Item` (028)."""
    a = write_run(tmp_path / "a", {"t": ("h", [True, False, False, True, False])})
    b = write_run(tmp_path / "b", {"t": ("h", [True, True, False, False, False])})
    log_run(api, "p", "a", a)
    log_run(api, "q", "b", b)
    runs = [read_results(a), read_results(b)]
    route = f"{P}/p/runs/a/items"

    def summaries(keep: str, *of: WrittenResults) -> list[dict]:
        """What the list says of each of `items()`'s docs: the text that varies between the shared ends, the gold and each run's answer, its margin from its probabilities (`acc_raw` ranks by them)."""
        found, kept = [], items(of, "t", keep)
        for item in kept:
            assert item.question is not None
            answers = []
            for answer in item.runs:
                assert answer.pick is not None
                answers.append(
                    {
                        "correct": answer.correct,
                        "pick": answer.pick,
                        "pick_text": item.choices[answer.pick].strip(),
                        "margin": pytest.approx(answer.logprob[0] - answer.logprob[1]),
                        "gold_prob": answer.prob[0],
                    }
                )
            # A list of one doc has no shared ends to leave out.
            text = item.question if len(kept) < 2 else item.question.removesuffix("\nAnswer:")
            found.append(
                {"id": item.id, "text": text, "gold": 0, "gold_text": "yes", "runs": answers}
            )
        return found

    wrong = api.get(route, task="t", keep="wrong", sort="doc")
    assert wrong.status == 200, wrong.body
    page = {"total": 3, "offset": 0}
    assert wrong.json() == page | {"items": summaries("wrong", runs[0])}
    one = api.get(route, task="t", keep="wrong", sort="doc", offset=1, limit=1).json()
    assert one == page | {"offset": 1, "items": summaries("wrong", runs[0])[1:2]}
    assert [i["id"] for i in api.get(route, task="t", keep="all", sort="doc").json()["items"]] == [
        f"d-{i}" for i in range(5)
    ]
    for cell in CELLS:
        found = api.get(route, task="t", keep=cell, sort="doc", **{"with": "flock/q/b"})
        assert found.status == 200, found.body
        assert found.json()["items"] == summaries(cell, *runs), cell
    assert api.get(route, task="t", keep="only_a", **{"with": "flock/q/b"}).json()["total"] == 1
    assert api.get(route, task="t", keep="wrong", offset=9).json()["items"] == []

    # A doc opens to eval's whole item, the one `flock eval items --json` prints.
    for item in items(runs[:1], "t", "wrong"):
        opened = api.get(f"{P}/p/runs/a/item", doc=item.id, task="t", keep="wrong")
        assert opened.status == 200, opened.body
        assert opened.json() == json.loads(json.dumps(item.to_dict()))
    for item in items(runs, "t", "only_b"):
        opened = api.get(
            f"{P}/p/runs/a/item", doc=item.id, task="t", keep="only_b", **{"with": "flock/q/b"}
        )
        assert opened.json() == json.loads(json.dumps(item.to_dict()))
    # Another list's doc isn't in this one.
    absent = api.get(f"{P}/p/runs/a/item", doc="d-0", task="t", keep="wrong")
    assert (
        absent.status == 404 and absent.json()["error"] == "t has no doc d-0 among its wrong docs"
    )


def test_rows_leave_out_the_shared_lines_and_clip_long_text(api: Client, tmp_path: Path) -> None:
    """Rows leave out the whole lines every question of the list shares at both ends, unless a row would be left with nothing; text past 240 characters is clipped, and a doc whose id holds dots and slashes opens."""
    folder = write_run(tmp_path / "a", {"t": ("h", [False, False])})
    path = Path(folder) / "predictions" / "t.jsonl"
    header, *rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0] |= {"id": "../set/0", "question": "Read this.\nIt rains.\nWhy?\nAnswer:"}
    rows[1] |= {"question": "Read this.\nIt " + "snows " * 50 + "\nWhy?\nAnswer:"}
    rows[1]["choices"] = [" yes", " " + "no" * 200]
    path.write_text("".join(json.dumps(r) + "\n" for r in [header, *rows]))
    log_run(api, "p", "a", folder)
    route = f"{P}/p/runs/a/items"

    first, second = api.get(route, task="t", keep="wrong", sort="doc").json()["items"]
    assert first["text"] == "It rains."
    assert second["text"] == ("It " + "snows " * 50)[:240].rstrip() + "…"
    assert second["runs"][0]["pick_text"] == ("no" * 200)[:240] + "…"
    opened = api.get(f"{P}/p/runs/a/item", doc="../set/0", task="t", keep="wrong")
    assert opened.status == 200 and opened.json()["question"] == rows[0]["question"]

    # The list's shared lines, not the page's: a page of one doc still leaves them out.
    alone = api.get(route, task="t", keep="wrong", sort="doc", limit=1).json()
    assert alone["items"][0]["text"] == "It rains."
    rows[1]["question"] = rows[0]["question"]
    path.write_text("".join(json.dumps(r) + "\n" for r in [header, *rows]))
    log_run(api, "p", "same", folder)
    same = api.get(f"{P}/p/runs/same/items", task="t", keep="wrong").json()  # all lines shared
    assert same["items"][0]["text"] == rows[0]["question"]


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
    margins = [r["runs"][0]["margin"] for r in api.get(route, task="t", keep="all").json()["items"]]
    assert margins == pytest.approx([-3, 2, 1, 0.5])
    assert ids(keep="all", sort="doc") == ["d-0", "d-1", "d-2", "d-3"]
    assert ids(keep="wrong") == ["d-1"]
    # Gaps: d-0 0, d-2 0 (both right); d-1 4 (only B right), d-3 4 (only A right).
    assert ids(keep="both_right", **{"with": "flock/p/b"}) == ["d-0", "d-2"]
    assert ids(keep="only_b", **{"with": "flock/p/b"}) == ["d-1"]
    assert api.get(route, task="t", keep="all", sort="best").status == 422


def with_suites(
    folder: str, suites: dict[str, list[str]], hashes: dict[str, str] | None = None
) -> str:
    """Gives a written run these suites, each with its members, as the runner records them; a suite's hash is its name unless `hashes` names another, and one named `...:bpb` is scored by `bpb`."""
    path = Path(folder) / "results.json"
    data = json.loads(path.read_text())
    data["suites"] = [
        {
            "suite": s,
            "suite_hash": (hashes or {}).get(s, s),
            "members": m,
            "metric": "bpb" if s.endswith(":bpb") else None,
            "score": 0.5,
        }
        for s, m in suites.items()
    ]
    path.write_text(json.dumps(data))
    return folder


def test_the_row_has_a_square_per_accuracy_suite_with_its_counts_and_dots(
    flock: FlockServer, api: Client, tmp_path: Path
) -> None:
    """A suite gets a square when both runs recorded it under one hash and it holds a task of its own; its tasks are every task under it, nested suites' too, both runs scored under one hash. A square counts its docs per cell and holds them as dots of both margins and their cell; a task the viewer can't read (a private dataset's, outside the organization) is left out (029)."""
    a_tasks: dict[str, tuple[str, list[bool] | None]] = {
        "t": ("h", [True, False, False, True, True]),
        "u": ("h", [True]),
        "x": ("h", [True]),
    }
    a_tasks["exam"] = ("e", [True, False])
    b_tasks = a_tasks | {"t": ("h", [True, True, False, False, True]), "x": ("changed", [True])}
    b_tasks["exam"] = ("e", [False, False])
    suites = {"s": ["t", "sub"], "sub": ["u", "x", "exam"], "group": ["s"], "moved": ["t"]}
    suites["s:bpb"] = ["t"]
    # Two suites holding each other, as an uploaded file may: each is opened once.
    suites["loop"] = ["u", "loop2"]
    suites["loop2"] = ["loop", "t"]
    a = with_suites(write_run(tmp_path / "a", a_tasks), suites)
    b = with_suites(write_run(tmp_path / "b", b_tasks), suites, {"moved": "another"})
    for folder in (a, b):
        keep_private(folder, "exam")
    log_run(api, "p", "a", a)
    log_run(api, "p", "b", b)
    route = f"{P}/p/runs/a/items/suites"

    found = api.get(route, **{"with": "flock/p/b"})
    assert found.status == 200, found.body
    # x was scored under another hash; `group` only groups; `moved` has another suite hash;
    # `s:bpb` is scored by bits per byte.
    rows = {row["suite"]: row for row in found.json()["suites"]}
    assert list(rows) == ["s", "sub", "loop", "loop2"]
    assert rows["loop"]["tasks"] == ["u", "t"]
    assert rows["s"]["tasks"] == ["t", "u", "exam"]
    # t: A right on d-0, d-3, d-4; B on d-0, d-1, d-4. u: both right. exam: A right on d-0 only.
    assert rows["s"]["counts"] == {"only_a": 2, "only_b": 1, "both_wrong": 2, "both_right": 3}
    assert len(rows["s"]["dots"]) == 8
    # A dot is a doc's margins as the list has them, rounded to 3 decimals, its cell, its task's
    # place among the square's tasks, its place in the list and its id: t's d-1, only B right.
    listed = api.get(f"{P}/p/runs/a/items", task="t", keep="only_b", **{"with": "flock/p/b"})
    margins = [round(m["margin"], 3) for m in listed.json()["items"][0]["runs"]]
    assert [*margins, 1, 0, 0, "d-1"] in rows["s"]["dots"]

    # Names only (the command menu's benchmarks): the same suites and tasks, without the squares.
    named = api.get(route, names="true", **{"with": "flock/p/b"})
    assert named.status == 200, named.body
    assert named.json()["suites"] == [{"suite": k, "tasks": r["tasks"]} for k, r in rows.items()]

    # The same pair again is the kept answer, byte for byte.
    assert api.get(route, **{"with": "flock/p/b"}).body == found.body
    # A predictions file uploaded again under the same results file is a new answer. Its outcomes
    # must still give the results' accuracy: flipped alone, they are eval's refusal, not a kept map.
    rows_u = (Path(b) / "predictions" / "u.jsonl").read_text()
    flipped = rows_u.replace('"correct": true', '"correct": false')
    api.upload("p", "b", "predictions/u.jsonl", flipped.encode())
    refused = api.get(route, **{"with": "flock/p/b"})
    assert refused.status == 409 and "acc_raw is 1.0" in refused.json()["error"]
    # Other margins with the same outcomes: u's one doc, both right, moves on B's axis.
    assert [1.0, 1.0, 3, 1, 0, "d-0"] in rows["s"]["dots"]
    api.upload("p", "b", "predictions/u.jsonl", rows_u.replace("-2.0]", "-5.0]").encode())
    again = api.get(route, **{"with": "flock/p/b"})
    assert again.status == 200, again.body
    dots = {row["suite"]: row["dots"] for row in again.json()["suites"]}["s"]
    assert [1.0, 4.0, 3, 1, 0, "d-0"] in dots
    assert [1.0, 1.0, 3, 1, 0, "d-0"] not in dots
    assert api.get(route).status == 422

    # On the public project, an outsider's squares leave the private exam out.
    assert api.patch(f"{P}/p", {"visibility": "public"}).status == 200
    outside = flock.anonymous().get(route, **{"with": "flock/p/b"})
    assert outside.status == 200, outside.body
    counts = {row["suite"]: row["counts"] for row in outside.json()["suites"]}
    assert counts["s"] == {"only_a": 1, "only_b": 1, "both_wrong": 1, "both_right": 3}
    named = flock.anonymous().get(route, names="true", **{"with": "flock/p/b"})
    assert {row["suite"]: row["tasks"] for row in named.json()["suites"]}["s"] == ["t", "u"]


def test_the_row_is_refused_as_the_list_is(api: Client, tmp_path: Path) -> None:
    """A run before schema 11 has no items to draw: eval's refusal, as the list gives it (029)."""
    new = with_suites(write_run(tmp_path / "new", {"t": ("h", [True])}), {"s": ["t"]})
    old = with_suites(write_run(tmp_path / "old", {"t": ("h", [True])}, schema=10), {"s": ["t"]})
    log_run(api, "p", "new", new)
    log_run(api, "p", "old", old)
    stale = api.get(f"{P}/p/runs/new/items/suites", **{"with": "flock/p/old"})
    assert stale.status == 409 and "schema 10" in stale.json()["error"]


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

    # A doc is refused as its list is.
    assert api.get(f"{P}/p/runs/old/item", doc="d-1", task="t", keep="wrong").status == 409
    assert api.get(f"{P}/p/runs/new/item", doc="d-1", task="t", keep="only_a").status == 409
    assert api.get(f"{P}/p/runs/new/item", doc="d-1", task="t", keep="worst").status == 422
    assert api.get(f"{P}/p/runs/train/item", doc="d-1", task="t", keep="wrong").status == 404


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
        assert found.json()["items"][0]["text"] == "Question: exam 0?\nAnswer:"  # one doc: whole
        assert client.get(route, task="exam", keep="all").status == 200
        opened = client.get(
            f"{P}/p/runs/a/item", doc="d-0", task="exam", keep="only_a", **{"with": "flock/p/b"}
        )
        assert opened.json()["question"] == "Question: exam 0?\nAnswer:"
    for client in (outsider, flock.anonymous()):
        pair = client.get(route, task="exam", keep="only_a", **{"with": "flock/p/b"})
        one = client.get(route, task="exam", keep="all")
        opened = client.get(f"{P}/p/runs/a/item", doc="d-0", task="exam", keep="all")
        for refused in (pair, one, opened):
            assert refused.status == 409
            assert "has no predictions for exam" in refused.json()["error"]
            assert b"Question" not in refused.body
        public = client.get(route, task="t", keep="only_b", **{"with": "flock/p/b"})
        assert public.json()["total"] == 1


class Bytes:
    """Stands in for a fetched tokenizer: each id is a few bytes, and a decode is their UTF-8, a
    character cut short reading as U+FFFD, as a byte-level BPE decodes."""

    PIECES: ClassVar[dict[int, bytes]] = {
        0: b" ye",
        1: b"s",
        2: b" no",
        3: b" \xe8\x87",
        4: b"\xba",
    }

    def decode(self, ids: list[int], skip_special_tokens: bool = True) -> str:
        return b"".join(self.PIECES[i] for i in ids).decode(errors="replace")


def with_tokens(folder: str, task: str, ids: dict[int, list[int]], private: bool = False) -> str:
    """Gives a written run schema 12's per-token sidecar for `task`: each doc's choice `k` scored as tokens `ids[k]`, with log-probabilities -0.5, -1.5, ..., ranks 1, 2, ... and entropy 0.25 per token, and a greedy row that isn't shown."""
    path = Path(folder) / "results.json"
    data = json.loads(path.read_text())
    data["schema_version"] = 12
    data["provenance"]["tokenizer"] = "tiny"
    path.write_text(json.dumps(data))
    predictions = Path(folder) / ("private/predictions" if private else "predictions")
    rows = [json.loads(line) for line in (predictions / f"{task}.jsonl").read_text().splitlines()]
    table: dict[str, list[Any]] = {
        "id": [],
        "choice": [],
        "part": [],
        "token_ids": [],
        "logprob": [],
        "rank": [],
        "entropy": [],
    }
    for row in rows[1:]:
        for choice, tokens in [*ids.items(), (-1, [2])]:
            table["id"].append(row["id"])
            table["choice"].append(choice)
            table["part"].append("continuation" if choice >= 0 else "greedy")
            table["token_ids"].append(tokens)
            table["logprob"].append([-0.5 - k for k in range(len(tokens))])
            table["rank"].append([1 + k for k in range(len(tokens))])
            table["entropy"].append([0.25] * len(tokens))
    pq.write_table(pa.table(table), predictions / f"{task}.tokens.parquet")
    return folder


def test_an_opened_doc_has_each_runs_token_values_named_by_its_tokenizer(
    flock: FlockServer, api: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A doc's tokens route answers each run's continuation values per choice from its sidecar, each token named by the run's tokenizer at the model's revision; a run without a sidecar, or whose tokenizer can't be fetched or doesn't give back the choice, says why; a private task's tokens reach members only (030)."""
    fetched = []

    def tokenizer(name: str, revision: str | None) -> Bytes:
        fetched.append((name, revision))
        return Bytes()

    monkeypatch.setattr(evals, "_tokenizer", tokenizer)
    a = with_tokens(
        write_run(tmp_path / "a", {"t": ("h", [True, False])}), "t", {0: [0, 1], 1: [2]}
    )
    b = write_run(tmp_path / "b", {"t": ("h", [True, True])})  # schema 11: no sidecar
    log_run(api, "p", "a", a)
    log_run(api, "p", "b", b)
    route = f"{P}/p/runs/a/item/tokens"

    found = api.get(route, doc="d-1", task="t", keep="only_b", **{"with": "flock/p/b"})
    assert found.status == 200, found.body
    ours, theirs = found.json()["runs"]
    assert ours == {
        "note": None,
        "choices": [
            {"text": [" ye", "s"], "logprob": [-0.5, -1.5], "rank": [1, 2], "entropy": [0.25] * 2},
            {"text": [" no"], "logprob": [-0.5], "rank": [1], "entropy": [0.25]},
        ],
    }
    assert fetched == [("tiny", "0")]  # the model's repo, so its revision
    assert theirs == {"choices": None, "note": "run:flock/p/b has no per-token values for t"}

    # Ids its tokenizer doesn't give the choice back from: the values, without text.
    other = with_tokens(write_run(tmp_path / "c", {"t": ("h", [True])}), "t", {0: [2], 1: [2]})
    log_run(api, "p", "c", other)
    wrong = api.get(f"{P}/p/runs/c/item/tokens", doc="d-0", task="t", keep="all").json()["runs"][0]
    assert [c["text"] for c in wrong["choices"]] == [None, None]
    assert wrong["choices"][0]["logprob"] == [-0.5]
    assert wrong["note"] == "tiny doesn't give back the text of 'yes'"

    # A tokenizer that can't be fetched, the same.
    def missing(name: str, revision: str | None) -> Bytes:
        raise OSError("no such repo")

    monkeypatch.setattr(evals, "_tokenizer", missing)
    gone = api.get(route, doc="d-0", task="t", keep="all").json()["runs"][0]
    assert gone["note"] == "no tokenizer tiny to name the tokens: no such repo"
    assert gone["choices"][1]["rank"] == [1]

    # Refused as the doc is; a private task's tokens reach no outsider.
    assert api.get(route, doc="d-0", task="t", keep="wrong").status == 404
    exam = write_run(tmp_path / "e", {"exam": ("e", [True])})
    keep_private(exam, "exam")
    log_run(api, "p", "e", with_tokens(exam, "exam", {0: [0, 1], 1: [2]}, private=True))
    monkeypatch.setattr(evals, "_tokenizer", tokenizer)
    query: dict[str, Any] = {"doc": "d-0", "task": "exam", "keep": "all"}
    member = api.get(f"{P}/p/runs/e/item/tokens", **query).json()["runs"][0]
    assert member["choices"][0]["text"] == [" ye", "s"]
    assert api.patch(f"{P}/p", {"visibility": "public"}).status == 200
    outside = flock.anonymous().get(f"{P}/p/runs/e/item/tokens", **query)
    assert outside.status == 409 and b"ye" not in outside.body


def test_a_character_split_across_tokens_goes_to_the_token_that_completes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A byte-level token can end inside a character: its piece is empty and the next token's holds the whole character, so the pieces still join to the choice."""
    monkeypatch.setattr(evals, "_tokenizer", lambda name, revision: Bytes())
    texts = evals.token_texts("tiny", None, [[3, 4], [0, 1]], [" 臺", " yes"])
    assert texts == [["", " 臺"], [" ye", "s"]]


def test_a_sidecar_that_isnt_the_writers_counts_as_none(
    api: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sidecar whose arrays for a choice have other lengths (an entropy missing for a token) answers as a run without values, not with arrays the page would misread (030)."""
    monkeypatch.setattr(evals, "_tokenizer", lambda name, revision: Bytes())
    folder = with_tokens(write_run(tmp_path / "a", {"t": ("h", [True])}), "t", {0: [0, 1], 1: [2]})
    path = Path(folder) / "predictions" / "t.tokens.parquet"
    table = pq.read_table(path)
    entropy = [[0.25] for _ in range(table.num_rows)]
    pq.write_table(table.set_column(6, "entropy", pa.array(entropy)), path)
    log_run(api, "p", "a", folder)

    found = api.get(f"{P}/p/runs/a/item/tokens", doc="d-0", task="t", keep="all")
    assert found.status == 200, found.body
    assert found.json()["runs"] == [
        {"choices": None, "note": "run:flock/p/a has no per-token values for t"}
    ]


@pytest.mark.parametrize(
    ("column", "values"),
    [
        ("token_ids", [-1, 1]),
        ("token_ids", [0.5, 1]),
        ("rank", [0, 1]),
        ("logprob", [float("nan"), -1.0]),
    ],
)
def test_sidecar_values_the_writer_cant_write_count_as_none(
    api: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, column: str, values: list
) -> None:
    """Token ids below 0 or not whole, ranks below 1 and non-finite numbers aren't the writer's: that run has no values (030)."""
    monkeypatch.setattr(evals, "_tokenizer", lambda name, revision: Bytes())
    folder = with_tokens(write_run(tmp_path / "a", {"t": ("h", [True])}), "t", {0: [0, 1], 1: [2]})
    path = Path(folder) / "predictions" / "t.tokens.parquet"
    table = pq.read_table(path)
    column_of = table.column(column).to_pylist()
    column_of[0] = values
    pq.write_table(
        table.set_column(table.schema.get_field_index(column), column, pa.array(column_of)), path
    )
    log_run(api, "p", "a", folder)

    found = api.get(f"{P}/p/runs/a/item/tokens", doc="d-0", task="t", keep="all")
    assert found.json()["runs"][0]["choices"] is None, column


def test_choices_that_arent_whole_numbers_once_each_count_as_none(
    api: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sidecar whose choices are lists, or name a choice twice, isn't the writer's: that run has no values, and the route answers (030)."""
    monkeypatch.setattr(evals, "_tokenizer", lambda name, revision: Bytes())
    for name, choices in (("lists", lambda c: [[c]]), ("twice", lambda c: 0)):
        folder = with_tokens(
            write_run(tmp_path / name, {"t": ("h", [True])}), "t", {0: [0, 1], 1: [2]}
        )
        path = Path(folder) / "predictions" / "t.tokens.parquet"
        table = pq.read_table(path)
        column = pa.array([choices(c) for c in table.column("choice").to_pylist()])
        pq.write_table(
            table.set_column(table.schema.get_field_index("choice"), "choice", column), path
        )
        log_run(api, "p", name, folder)

        found = api.get(f"{P}/p/runs/{name}/item/tokens", doc="d-0", task="t", keep="all")
        assert found.status == 200, found.body
        assert found.json()["runs"][0]["choices"] is None, name


def test_a_sidecar_parquet_cant_read_costs_only_its_own_run(
    api: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run whose sidecar isn't Parquet answers without values; the other run keeps its tokens (030)."""
    monkeypatch.setattr(evals, "_tokenizer", lambda name, revision: Bytes())
    a = with_tokens(write_run(tmp_path / "a", {"t": ("h", [True])}), "t", {0: [0, 1], 1: [2]})
    b = with_tokens(write_run(tmp_path / "b", {"t": ("h", [True])}), "t", {0: [0, 1], 1: [2]})
    (Path(b) / "predictions" / "t.tokens.parquet").write_bytes(b"not parquet")
    log_run(api, "p", "a", a)
    log_run(api, "p", "b", b)

    found = api.get(
        f"{P}/p/runs/a/item/tokens", doc="d-0", task="t", keep="both_right", **{"with": "flock/p/b"}
    )
    assert found.status == 200, found.body
    ours, theirs = found.json()["runs"]
    assert ours["choices"][0]["text"] == [" ye", "s"]
    assert theirs == {"choices": None, "note": "run:flock/p/b has no per-token values for t"}


def test_a_private_tokenizer_repo_is_never_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """The name comes from an uploaded run: a repo the Hub lists as private is refused before any fetch, so the server's own token never opens one for a viewer (030)."""

    class Hub:
        def model_info(self, name: str, revision: str | None, token: bool, timeout: float) -> Any:
            assert token is False  # asked without the server's token
            return dataclasses.make_dataclass("Info", ["private"])(True)

    monkeypatch.setattr(evals, "HfApi", Hub)
    with pytest.raises(ValueError, match="secret/tok isn't a public repo"):
        evals._tokenizer.__wrapped__("secret/tok", None)


def test_a_choice_too_long_to_name_keeps_its_values_without_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Naming tokens costs a choice's length squared, so past 256 tokens it is refused (030)."""
    monkeypatch.setattr(evals, "_tokenizer", lambda name, revision: Bytes())
    with pytest.raises(ValueError, match="a choice of 257 tokens is too long"):
        evals.token_texts("tiny", None, [[2] * 257], [" no" * 257])


@pytest.mark.parametrize("route", [run_items, run_item, run_item_tokens])
def test_the_items_routes_take_the_keeps_eval_takes(route: Callable[..., Any]) -> None:
    """The routes' `keep` values are eval's: one they lacked would be a 422 for a filter `items()` reads, and an extra one a 409 nobody could get past."""
    keep = inspect.signature(route).parameters["keep"].annotation
    assert get_args(get_args(keep)[0]) == ONE_RUN + CELLS


def test_the_dashboard_reads_the_schemas_eval_reads() -> None:
    """The web's `RESULT_SCHEMAS` is eval's `READ_SCHEMAS`: a schema on one side only would hide
    results the other reads, or show a layout the pages don't check."""
    source = Path(__file__).parents[2] / "app/web/src/lib/evals.ts"
    found = re.search(r"export const RESULT_SCHEMAS = \[([0-9, ]*)\];", source.read_text())
    assert found is not None
    assert tuple(int(n) for n in found[1].split(",")) == READ_SCHEMAS
    assert Results(PROVENANCE, []).schema_version in READ_SCHEMAS
