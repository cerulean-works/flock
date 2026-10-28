import base64
import json
import re
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote

import pytest

from app.api.src.core.wandb_source import parse_time

KEY = "wandb-test-key"
CREATED = "2025-01-02T03:04:05"
FINISHED = "2025-01-02T05:04:05"
H200x8 = json.dumps({"gpu": "NVIDIA H200", "gpu_count": 8, "host": "node-7", "program": "train.py"})


def wandb_run(name: str, **fields: Any) -> dict[str, Any]:
    """A run as the fake serves it; ``_history``, ``_events`` and ``_files`` hold its data."""
    return {
        "name": name,
        "displayName": f"{name}-display",
        "state": "finished",
        "config": json.dumps({"lr": {"value": 0.1, "desc": None}, "_wandb": {"value": {}}}),
        "summaryMetrics": json.dumps({"loss": 0.5, "_step": 2}),
        "tags": ["baseline"],
        "notes": None,
        "group": None,
        "jobType": "train",
        "sweepName": None,
        "commit": "abc123",
        "createdAt": CREATED,
        "heartbeatAt": FINISHED,
        "user": {"username": "bob"},
        "_history": [{"_step": s, "loss": 1 / (s + 1)} for s in range(3)],
        "_events": [],
        "_files": {},
        **fields,
    }


# Every field and argument W&B's schema has that the importer may ask for (see
# ``test_queries_use_only_fields_the_wandb_sdk_uses``). Anything else is refused, as W&B does.
FIELDS = {
    "viewer", "username", "entity", "name", "members", "email", "models", "entityName",
    "first", "after", "pageInfo", "endCursor", "hasNextPage", "edges", "node", "createdAt",
    "runCount", "project", "runs", "order", "displayName", "state", "config", "summaryMetrics",
    "tags", "notes", "group", "jobType", "sweepName", "commit", "heartbeatAt",
    "historyLineCount", "historyKeys", "user", "run", "history", "minStep", "maxStep",
    "samples", "events", "files", "names", "sizeBytes", "url", "upload",
}  # fmt: skip


def selected(query: str) -> set[str]:
    """The fields and arguments a query names (not its operation, variables or types)."""
    body = re.sub(r'\$\w+|"[^"]*"', "", query[query.index("{") :])
    return set(re.findall(r"[A-Za-z_]\w*", body)) - {"true", "false", "null"}


class FakeWandb:
    """A W&B server answering the GraphQL queries Flock's importer sends, and serving files.

    ``gate`` (when cleared) holds history requests, to keep an import running."""

    PAGE = 2  # runs per page, to exercise pagination

    def __init__(self) -> None:
        self.entity = "acme"
        self.members = [{"username": "alice", "email": "Alice@flock.test"}]
        self.projects: dict[str, dict[str, Any]] = {}
        self.broken: set[str] = set()  # runs whose files can't be listed
        self.refused: set[str] = set()  # files ("<run>/<name>") whose download is refused
        self.gate = threading.Event()
        self.gate.set()
        self.queries: list[str] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def reply(self, status: int, body: bytes, kind: str = "application/json") -> None:
                self.send_response(status)
                self.send_header("content-type", kind)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def authorized(self) -> bool:
                scheme, _, credentials = self.headers.get("authorization", "").partition(" ")
                return scheme == "Basic" and base64.b64decode(credentials) == f"api:{KEY}".encode()

            def do_POST(self) -> None:
                if not self.authorized():
                    return self.reply(401, b'{"errors": [{"message": "unauthorized"}]}')
                request = json.loads(self.rfile.read(int(self.headers["content-length"])))
                operation = re.findall(r"query (\w+)", request["query"])[0]
                fake.queries.append(operation)
                if unknown := sorted(selected(request["query"]) - FIELDS):
                    message = f'Cannot query field "{unknown[0]}"'
                    return self.reply(200, json.dumps({"errors": [{"message": message}]}).encode())
                try:
                    data = fake.answer(operation, request["variables"])
                except LookupError as exc:
                    return self.reply(200, json.dumps({"errors": [{"message": str(exc)}]}).encode())
                self.reply(200, json.dumps({"data": data}).encode())

            def do_GET(self) -> None:  # file downloads: /files/<project>/<run>/<name>
                # As W&B's file route does: the API key is required.
                if not self.authorized():
                    return self.reply(401, b"unauthorized")
                _, _, project, run, name = (unquote(p) for p in self.path.split("/", 4))
                if f"{run}/{name}" in fake.refused:
                    return self.reply(403, b"forbidden")
                content = fake.run(project, run)["_files"][name]
                if not content:  # listed, but never stored (as W&B does with empty files)
                    return self.reply(404, b"not found")
                self.reply(200, content, "application/octet-stream")

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True
        )
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def add(self, project: str, *runs: dict[str, Any], created: str = CREATED) -> None:
        found = self.projects.setdefault(project, {"createdAt": created, "runs": []})
        found["runs"] += runs

    def run(self, project: str, name: str) -> dict[str, Any]:
        return next(r for r in self.projects[project]["runs"] if r["name"] == name)

    def answer(self, operation: str, v: dict[str, Any]) -> dict[str, Any]:
        def page(items: list, cursor: str | None) -> tuple[list, dict]:
            start = int(cursor or 0)
            end = start + self.PAGE
            return items[start:end], {"endCursor": str(end), "hasNextPage": end < len(items)}

        def public(run: dict[str, Any]) -> dict[str, Any]:
            node = {k: v for k, v in run.items() if not k.startswith("_")}
            history = run["_history"]
            node["historyLineCount"] = len(history)
            node["historyKeys"] = json.dumps({"lastStep": history[-1]["_step"] if history else -1})
            return node

        known = v.get("entity") == self.entity
        if operation == "FlockViewer":
            return {
                "viewer": {"username": "importer"},
                "entity": {"name": v["entity"]} if known else None,
            }
        if operation == "FlockMembers":
            return {"entity": {"members": self.members}}
        if operation == "FlockProjects":
            names, info = page(sorted(self.projects), v["cursor"])
            edges = [
                {"node": {"name": n, "createdAt": self.projects[n]["createdAt"],
                          "runCount": len(self.projects[n]["runs"])}}
                for n in names
            ]  # fmt: skip
            return {"models": {"edges": edges, "pageInfo": info}}
        project = self.projects[v["project"]]
        if operation == "FlockRuns":
            runs, info = page(project["runs"], v["cursor"])
            edges = [{"node": public(run)} for run in runs]
            return {"project": {"runs": {"edges": edges, "pageInfo": info}}}
        run = self.run(v["project"], v["run"])
        if operation == "FlockHistory":
            self.gate.wait()
            rows = [r for r in run["_history"] if v["minStep"] <= r["_step"] < v["maxStep"]]
            return {"project": {"run": {"history": [json.dumps(r) for r in rows]}}}
        if operation == "FlockEvents":
            return {"project": {"run": {"events": [json.dumps(e) for e in run["_events"]]}}}
        if operation == "FlockFiles":
            if run["name"] in self.broken:
                raise LookupError("files are unavailable")
            edges = [
                {"node": {"name": name, "sizeBytes": len(content),
                          "url": f"{self.url}/files/{v['project']}/{v['run']}/{name}"}}
                for name, content in run["_files"].items()
                if name in v["names"]
            ]  # fmt: skip
            return {"project": {"run": {"files": {"edges": edges}}}}
        raise LookupError(f"unknown query {operation}")


@pytest.fixture
def wandb() -> Iterator[FakeWandb]:
    fake = FakeWandb()
    yield fake
    fake.gate.set()
    fake.server.shutdown()
    fake.server.server_close()
    fake.thread.join(timeout=5)
    assert not fake.thread.is_alive()


def source(fake: FakeWandb, **fields: Any) -> dict[str, Any]:
    return {"entity": fake.entity, "api_key": KEY, "base_url": fake.url, **fields}


def finished(api, wait_for, org: str = "lab") -> dict:
    def done():
        job = api.get(f"/api/v1/orgs/{org}/import").json()
        return job if job["state"] != "running" else None

    return wait_for(done)


def test_create_org_imports_wandb_projects_and_runs(flock, api, wandb, wait_for):
    alice = flock.add_user("alice@flock.test")
    me = api.get("/api/v1/me").json()["id"]
    wandb.add(
        "vision",
        wandb_run(
            "r1",
            user={"username": "alice"},
            _events=[{"_runtime": t, "_timestamp": t, "system.cpu": t * 10} for t in range(4)],
            _files={
                "wandb-metadata.json": H200x8.encode(),
                "output.log": b"epoch 1\nepoch 2\n",
                "requirements.txt": b"torch\n",
                "media/images/big.png": b"not copied",
            },
        ),
        wandb_run(
            "r2",
            state="running",
            tags=[],
            createdAt="2025-01-02T04:00:00",
            heartbeatAt="2025-01-02T04:00:00",
            _history=[],
        ),
        wandb_run("r3", state="killed", createdAt="2024-12-31T23:00:00Z"),
    )
    wandb.add("audio", wandb_run("a1", user=None), created="2024-06-01T00:00:00")

    resp = api.post("/api/v1/orgs", {"name": "lab", "wandb": source(wandb)})
    assert resp.status == 201, resp.body
    body = resp.json()
    assert body["name"] == "lab"
    assert body["import"]["state"] == "running"
    assert body["import"]["entity"] == "acme"
    assert "api_key" not in body["import"]

    job = finished(api, wait_for)
    assert job["state"] == "finished", job
    assert (job["projects_total"], job["projects_done"]) == (2, 2)
    assert (job["runs_total"], job["runs_done"], job["runs_skipped"], job["runs_failed"]) == (
        4, 4, 0, 0,
    )  # fmt: skip
    assert job["error"] is None

    projects = {p["name"]: p for p in api.get("/api/v1/projects", entity="lab").json()["projects"]}
    assert set(projects) == {"vision", "audio"}
    assert projects["audio"]["created_at"] == parse_time("2024-06-01T00:00:00")

    # Rows are in creation order (r3 is the oldest), and states are finished ones.
    runs = api.runs("vision", entity="lab", columns=["state", "owner"], sort="created_at")
    assert runs["ids"] == ["r3", "r1", "r2"]
    assert runs["data"]["state"] == ["failed", "finished", "crashed"]
    # alice's run is hers (matched by email); the rest belong to the importer.
    assert runs["data"]["owner"] == [me, alice.id, me]

    run = api.get("/api/v1/projects/lab/vision/runs/r1").json()
    assert run["display_name"] == "r1-display"
    assert run["created_at"] == parse_time(CREATED)
    assert run["heartbeat_at"] == parse_time(FINISHED)
    assert run["config"] == {"lr": 0.1}
    assert run["summary"] == {"loss": 0.5, "_step": 2}
    assert run["tags"] == ["baseline"]
    assert (run["job_type"], run["commit"]) == ("train", "abc123")
    assert (run["host"], run["program"]) == ("node-7", "train.py")
    assert (run["gpu_type"], run["gpu_count"]) == ("H200", 8)
    assert run["runtime"] == 7200
    assert run["gpu_hours"] == 16
    assert run["system_keys"] == ["_runtime", "_timestamp", "system.cpu"]

    curves = api.run_history("vision", "r1", entity="lab")
    assert curves["loss"] == {"x": [0, 1, 2], "y": pytest.approx([1, 1 / 2, 1 / 3])}
    system = api.run_history("vision", "r1", entity="lab", stream="system")
    assert system["system.cpu"] == {"x": [0, 1, 2, 3], "y": [0, 10, 20, 30]}

    logs = api.get("/api/v1/projects/lab/vision/runs/r1/logs").json()
    assert [line["text"] for line in logs["lines"]] == ["epoch 1", "epoch 2"]
    files = api.get("/api/v1/projects/lab/vision/runs/r1/files").json()["files"]
    assert {f["name"] for f in files} == {"wandb-metadata.json", "output.log", "requirements.txt"}
    content = api.get("/api/v1/projects/lab/vision/runs/r1/files/requirements.txt")
    assert content.body == b"torch\n"

    # Imported series are stored as one chunk per metric, so there is nothing to compact.
    store = flock.store
    r1 = flock.call(store.get_run("lab", "vision", "r1"))
    chunks = flock.call(
        store.db.fetch(
            "SELECT stream, max(first_line), sum(n) FROM series_chunks WHERE run_pk = $1 "
            "AND key IN ('_step', '_runtime') GROUP BY stream",
            r1.pk,
        )
    )
    assert sorted(tuple(c) for c in chunks) == [("history", 0, 3), ("system", 0, 4)]
    assert flock.call(store.db.fetchval("SELECT uncompacted FROM runs WHERE pk = $1", r1.pk)) == 0
    assert alice.api.get("/api/v1/orgs/lab/import").status == 404  # not a member


def test_import_problems_are_refused_before_creating_the_org(flock, api, wandb):
    bad_key = api.post("/api/v1/orgs", {"name": "lab", "wandb": source(wandb, api_key="nope")})
    assert bad_key.status == 400
    assert bad_key.json()["error"] == "W&B rejected the API key"
    unknown = api.post("/api/v1/orgs", {"name": "lab", "wandb": source(wandb, entity="other")})
    assert unknown.status == 404
    unreachable = api.post(
        "/api/v1/orgs", {"name": "lab", "wandb": source(wandb, base_url="http://127.0.0.1:9")}
    )
    assert unreachable.status == 502
    assert api.get("/api/v1/orgs/lab").status == 404
    bad_url = api.post("/api/v1/orgs", {"name": "lab", "wandb": source(wandb, base_url="ftp://x")})
    assert bad_url.status == 422

    member = flock.add_user("member@flock.test")
    refused = member.api.post("/api/v1/orgs", {"name": "lab", "wandb": source(wandb)})
    assert refused.status == 403


def test_failed_runs_are_counted_and_imports_resume(flock, api, wandb, wait_for):
    logged = {"output.log": b"hello\n", "requirements.txt": b"torch\n"}
    wandb.add("p", *(wandb_run(f"r{i}", _files=logged) for i in range(5)))
    wandb.broken = {"r3"}
    assert api.post("/api/v1/orgs", {"name": "lab", "wandb": source(wandb)}).status == 201
    job = finished(api, wait_for)
    assert job["state"] == "finished"
    assert (job["runs_done"], job["runs_failed"]) == (4, 1)
    assert job["error"].startswith("p/r3: ")
    assert api.runs("p", entity="lab")["total"] == 4
    # Files are downloaded with the API key.
    assert api.get("/api/v1/projects/lab/p/runs/r0/logs").json()["lines"][0]["text"] == "hello"

    # Starting again copies only what is missing.
    wandb.broken = set()
    resp = api.post("/api/v1/orgs/lab/import", source(wandb))
    assert resp.status == 202, resp.body
    job = finished(api, wait_for)
    assert (job["runs_done"], job["runs_skipped"], job["runs_failed"]) == (1, 4, 0)
    assert sorted(api.runs("p", entity="lab")["ids"]) == [f"r{i}" for i in range(5)]


def test_running_imports_can_be_cancelled_or_stopped_by_deleting_the_org(
    flock, api, wandb, wait_for
):
    wandb.add("p", wandb_run("r1"), wandb_run("r2"))
    wandb.gate.clear()  # history requests hang
    assert api.post("/api/v1/orgs", {"name": "lab", "wandb": source(wandb)}).status == 201
    wait_for(lambda: "FlockHistory" in wandb.queries)
    again = api.post("/api/v1/orgs/lab/import", source(wandb))
    assert again.status == 409
    cancelled = api.delete("/api/v1/orgs/lab/import")
    assert cancelled.status == 200
    assert cancelled.json()["state"] == "cancelled"
    assert api.get("/api/v1/orgs/lab/import").json()["state"] == "cancelled"
    assert api.runs("p", entity="lab")["total"] == 0

    previous_history = wandb.queries.count("FlockHistory")
    assert api.post("/api/v1/orgs/lab/import", source(wandb)).status == 202
    wait_for(
        lambda: (
            any(job.current_project == "p" for job in flock.store.imports.jobs.values())
            and wandb.queries.count("FlockHistory") > previous_history
        )
    )
    assert api.delete("/api/v1/orgs/lab").status == 204
    assert api.get("/api/v1/orgs/lab").status == 404
    wait_for(lambda: flock.store.imports.jobs == {})


def test_imports_cut_short_by_a_restart_are_interrupted(make_flock, wandb, wait_for):
    server = make_flock()
    api = server.client()
    wandb.add("p", wandb_run("r1"))
    wandb.gate.clear()
    assert api.post("/api/v1/orgs", {"name": "lab", "wandb": source(wandb)}).status == 201
    wait_for(lambda: "FlockHistory" in wandb.queries)
    server.stop()

    restarted = make_flock(reuse=server)
    api = restarted.client()
    job = api.get("/api/v1/orgs/lab/import").json()
    assert job["state"] == "interrupted"
    assert job["finished_at"] is not None
    wandb.gate.set()
    assert api.post("/api/v1/orgs/lab/import", source(wandb)).status == 202
    assert finished(api, wait_for)["state"] == "finished"
    assert api.runs("p", entity="lab")["ids"] == ["r1"]


def test_queries_use_only_fields_the_wandb_sdk_uses():
    """W&B refuses unknown fields; the run, project and file fields are the SDK's own."""
    pytest.importorskip("wandb")
    from wandb.apis._generated.operations import GET_PROJECTS_GQL
    from wandb.apis.public.files import FILE_FRAGMENT
    from wandb.apis.public.runs import RUN_FRAGMENT, _create_runs_query

    from app.api.src.core import wandb_source

    known = (
        selected(RUN_FRAGMENT)
        | selected(GET_PROJECTS_GQL)
        | selected(_create_runs_query(lazy=False))
        | selected(FILE_FRAGMENT)
        | {"run"}  # project { run(name:) }, from the SDK's Run query (built inside Run.load)
    )
    assert selected(wandb_source.RUNS) <= known, selected(wandb_source.RUNS) - known
    assert selected(wandb_source.PROJECTS) <= known, selected(wandb_source.PROJECTS) - known
    assert selected(wandb_source.FILES) <= known, selected(wandb_source.FILES) - known
    for query in (wandb_source.VIEWER, wandb_source.MEMBERS, wandb_source.HISTORY,
                  wandb_source.EVENTS):  # fmt: skip
        assert selected(query) <= FIELDS, selected(query) - FIELDS


def test_a_file_that_cannot_be_downloaded_is_skipped(flock, api, wandb, wait_for):
    wandb.add(
        "p",
        wandb_run("r1", _files={"output.log": b"hello\n", "requirements.txt": b"torch\n"}),
    )
    wandb.refused = {"r1/output.log"}
    assert api.post("/api/v1/orgs", {"name": "lab", "wandb": source(wandb)}).status == 201
    job = finished(api, wait_for)
    assert (job["state"], job["runs_done"], job["runs_failed"]) == ("finished", 1, 0)
    assert job["error"] == "p/r1: skipped output.log: could not download a run file (403)"
    files = api.get("/api/v1/projects/lab/p/runs/r1/files").json()["files"]
    assert [f["name"] for f in files] == ["requirements.txt"]
    assert api.get("/api/v1/projects/lab/p/runs/r1/logs").json()["lines"] == []


def test_files_listed_but_not_stored_are_not_failures(flock, api, wandb, wait_for):
    # As on wandb.ai: wandb-events.jsonl is listed with no content, and the system metrics
    # come from the run's events.
    events = [{"_runtime": t, "_timestamp": t, "system.cpu": t} for t in range(3)]
    wandb.add("p", wandb_run("r1", _events=events, _files={"wandb-events.jsonl": b""}))
    assert api.post("/api/v1/orgs", {"name": "lab", "wandb": source(wandb)}).status == 201
    job = finished(api, wait_for)
    assert (job["state"], job["runs_done"], job["error"]) == ("finished", 1, None)
    system = api.run_history("p", "r1", entity="lab", stream="system")
    assert system["system.cpu"] == {"x": [0, 1, 2], "y": [0, 1, 2]}
