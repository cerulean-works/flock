"""Eval results in the dashboard (023): comparing two eval runs doc by doc on the server (D174),
with eval's own diff."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import TYPE_CHECKING

from flock.eval.diff import diff
from flock.eval.results import read_results
from tests.eval.toy import PROVENANCE, write_run

if TYPE_CHECKING:
    from tests.app.conftest import Client, FlockServer

P = "/api/v1/projects/flock"


def log_run(api: Client, project: str, run: str, folder: str) -> None:
    """A run with a written run's files, uploaded under the names `log_to_tracker` gives them."""
    api.create_run(project, run, config={"job_type": "eval"})
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
    assert missing.json()["error"] == "flock/p/train has no results.json"
    for bad in ("flock/p", "flock/p/b/x", "flock//b", "flock/p/nope"):
        refused = api.get(route, **{"with": bad})
        assert refused.status == 404, bad
    assert api.get(f"{P}/p/runs/nope/diff", **{"with": "flock/p/b"}).status == 404
