import asyncio
import base64
import gzip
import http.client
import itertools
import json
import os
import shutil
import socket
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Coroutine, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import asyncpg
import numpy as np
import orjson
import pytest
import uvicorn
import uvloop

from app import create_app
from src.core.store import Store
from src.db.migrate import Migrations
from src.settings import Settings


class Response:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.body = body
        self.headers = headers or {}  # lowercased names

    def json(self) -> Any:
        return orjson.loads(self.body)


class Client:
    """Minimal keep-alive HTTP client (stdlib only) for talking to the tracker in tests.

    It authenticates with ``api_key`` (sent as the wandb SDK does) or an access ``token``.
    """

    def __init__(self, port: int, api_key: str | None = None, token: str | None = None) -> None:
        self.port = port
        self.headers: dict[str, str] = {}
        if api_key:  # the wandb SDK's scheme: basic auth with user "api"
            basic = base64.b64encode(f"api:{api_key}".encode()).decode()
            self.headers["Authorization"] = f"Basic {basic}"
        elif token:
            self.headers["Authorization"] = f"Bearer {token}"
        self._conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        if params:
            path = f"{path}?{urlencode(params)}"
        hdrs = {**self.headers, **(headers or {})}
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else orjson.dumps(body)
            hdrs.setdefault("Content-Type", "application/json")
        for attempt in range(2):
            try:
                self._conn.request(method, path, body=data, headers=hdrs)
                resp = self._conn.getresponse()
                headers = {name.lower(): value for name, value in resp.getheaders()}
                return Response(resp.status, resp.read(), headers)
            except ConnectionError, http.client.HTTPException:
                self._conn.close()
                if attempt:
                    raise
        raise AssertionError("unreachable")

    def get(self, path: str, headers: dict[str, str] | None = None, **params: Any) -> Response:
        return self.request("GET", path, params=params or None, headers=headers)

    def post(self, path: str, body: Any, **kwargs: Any) -> Response:
        return self.request("POST", path, body=body, **kwargs)

    def patch(self, path: str, body: Any) -> Response:
        return self.request("PATCH", path, body=body)

    def delete(self, path: str, **params: Any) -> Response:
        return self.request("DELETE", path, params=params or None)

    def graphql(self, query: str, variables: dict | None = None, **kwargs: Any) -> Response:
        return self.post("/graphql", {"query": query, "variables": variables or {}}, **kwargs)

    def file_stream(self, entity: str, project: str, run: str, payload: dict, gzipped=False):
        body = orjson.dumps(payload)
        headers = {}
        if gzipped:
            body = gzip.compress(body)
            headers["Content-Encoding"] = "gzip"
        return self.post(f"/files/{entity}/{project}/{run}/file_stream", body, headers=headers)

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------ protocol shorthands

    def create_run(
        self, project: str, name: str, config=None, tags=None, display_name=None
    ) -> dict:
        """Create or update a run the way the SDK does (UpsertBucket); returns the payload."""
        query = """
        mutation UpsertBucket($name: String, $project: String, $entity: String,
                              $config: JSONString, $tags: [String!], $displayName: String,
                              $state: String) {
          upsertBucket(input: {name: $name, modelName: $project, entityName: $entity,
                               config: $config, tags: $tags, displayName: $displayName,
                               state: $state}) {
            bucket { id name displayName config historyLineCount project { name entity { name } } }
            inserted
          }
        }
        """
        resp = self.graphql(
            query,
            {
                "name": name,
                "project": project,
                "config": json.dumps({k: {"value": v} for k, v in (config or {}).items()}),
                "tags": tags,
                "displayName": display_name,
            },
        )
        assert resp.status == 200, resp.body
        body = resp.json()
        assert "errors" not in body, body
        return body["data"]["upsertBucket"]

    def stream(
        self,
        project,
        name,
        history=None,
        offset=0,
        summary=None,
        complete=None,
        events=None,
        events_offset=0,
        **extra,
    ) -> dict:
        """Send one file_stream request; ``complete`` is the exit code that ends the run.

        ``events`` are system metric samples (lines of wandb-events.jsonl).
        """
        files = {}
        for file, lines, start in (
            ("wandb-history.jsonl", history, offset),
            ("wandb-events.jsonl", events, events_offset),
        ):
            if lines is not None:
                files[file] = {
                    "offset": start,
                    "content": [r if isinstance(r, str) else json.dumps(r) for r in lines],
                }
        if summary is not None:
            files["wandb-summary.json"] = {"offset": 0, "content": [json.dumps(summary)]}
        payload = {"files": files, **extra}
        if complete is not None:
            payload.update(complete=True, exitcode=complete)
        resp = self.file_stream("flock", project, name, payload, gzipped=True)
        assert resp.status == 200, resp.body
        return resp.json()

    # ------------------------------------------------------------------ read API shorthands

    def runs(self, project: str, entity: str = "flock", **body: Any) -> dict:
        """Query the runs table (POST, so filters can be plain dicts)."""
        resp = self.post(f"/api/v1/projects/{entity}/{project}/runs/query", body)
        assert resp.status == 200, resp.body
        return resp.json()

    def only_run(self, project: str, entity: str = "flock") -> dict:
        """Every column of the project's single run, as {column: value}."""
        data = self.runs(project, entity, columns="*")
        assert data["total"] == 1, data
        return {name: values[0] for name, values in data["data"].items()}

    def run_history(self, project: str, run: str, entity: str = "flock", **params) -> dict:
        resp = self.get(f"/api/v1/projects/{entity}/{project}/runs/{run}/history", **params)
        assert resp.status == 200, resp.body
        return resp.json()["series"]

    def workspace(self, project: str = "p", **params: Any) -> dict:
        """Fetch a dashboard snapshot and decode it as a dashboard would (wire format: docs)."""
        resp = self.get(f"/api/v1/projects/flock/{project}/workspace", **params)
        assert resp.status == 200, resp.body
        body = resp.body
        assert body[:4] == b"FLK2"
        n = int(np.frombuffer(body, "<u4", 1, 4)[0])
        header = orjson.loads(body[8 : 8 + n])
        pos = 8 + n + (-(8 + n)) % 8
        # Each run's curves: history metrics, then system metrics (when requested).
        runs, metrics = header["runs"], header["metrics"] + header["system_metrics"]
        n_num = len(header["numeric"]) * len(runs)
        num = np.frombuffer(body, "<f8", n_num, pos).reshape(-1, len(runs) or 1)
        pos += n_num * 8
        meta = np.frombuffer(body, "<f8", len(runs) * len(metrics) * 6, pos).reshape(-1, 6)
        pos += meta.size * 8
        xs = np.frombuffer(body, "<f8", header["x_len"], pos)
        pos += header["x_len"] * 8
        ys = np.frombuffer(body, "<f4", header["y_len"], pos)
        assert pos + header["y_len"] * 4 == len(body)

        columns = [c["key"] for c in header["columns"]]
        cells: dict[str, list] = {}
        for i, col in enumerate(header["numeric"]):
            cells[columns[col]] = [None if np.isnan(v) else float(v) for v in num[i]]
        for col, values in header["text"].items():
            cells[columns[int(col)]] = values

        curves: dict[tuple[str, str], tuple[list[float], list[float]]] = {}
        for r, run in enumerate(runs):
            for m, key in enumerate(metrics):
                y_off, count, raw, x0, dx, x_off = meta[r * len(metrics) + m]
                count, raw = int(count), int(raw)
                if count == 0:
                    continue
                i = np.arange(count)
                k = (i * (raw - 1) + (count - 1) // 2) // max(count - 1, 1)
                x = xs[int(x_off) : int(x_off) + count] if x_off >= 0 else x0 + dx * k
                y = ys[int(y_off) : int(y_off) + count]
                curves[(run["id"], key)] = (x.tolist(), y.tolist())
        return {**header, "cells": cells, "curves": curves}


class EventStream:
    """Reads a dashboard's Server-Sent Events from the tracker in the test thread."""

    def __init__(self, api: Client, path: str) -> None:
        self.conn = http.client.HTTPConnection("127.0.0.1", api.port, timeout=10)
        self.conn.request("GET", path, headers=api.headers)
        self.resp = self.conn.getresponse()

    def next(self) -> tuple[str, dict]:
        """The next event; the socket timeout bounds the wait."""
        event, data = None, None
        while True:
            line = self.resp.readline().decode().rstrip("\n")
            if not line and event is None and self.resp.isclosed():
                raise AssertionError("stream ended")
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = orjson.loads(line[6:])
            elif line == "" and event is not None:
                return event, data or {}

    def next_of(self, kind: str) -> dict:
        while True:
            event, data = self.next()
            if event == kind:
                return data

    def close(self) -> None:
        self.conn.close()


class Postgres:
    """A PostgreSQL server for tests, handing out fresh databases."""

    def __init__(self, admin_url: str) -> None:
        self.admin_url = admin_url
        self.template = f"tracker_template_{os.getpid()}"
        self._counter = itertools.count()
        self.created: list[str] = []

    @staticmethod
    def start_container(image: str = "postgres:18.4") -> tuple[str, str]:
        """Start a throwaway PostgreSQL container; returns (container name, admin URL)."""
        image = os.environ.get("TRACKER_TEST_POSTGRES_IMAGE", image)
        name = f"flock-tracker-test-{os.getpid()}"
        started = subprocess.run(
            [
                "docker", "run", "-d", "--rm", "--name", name,
                "-e", "POSTGRES_USER=tracker", "-e", "POSTGRES_PASSWORD=tracker",
                "-p", "127.0.0.1::5432", "--tmpfs", "/var/lib/postgresql",
                image,
                # Durability is irrelevant for throwaway test data; trade it for speed.
                "-c", "fsync=off", "-c", "synchronous_commit=off", "-c", "full_page_writes=off",
                "-c", "max_connections=500",
            ],
            check=False, capture_output=True, text=True,
        )  # fmt: skip
        if started.returncode != 0:
            pytest.skip(f"could not start a PostgreSQL container: {started.stderr.strip()}")
        port = (
            subprocess.run(
                ["docker", "port", name, "5432/tcp"], check=True, capture_output=True, text=True
            )
            .stdout.split()[0]
            .rpartition(":")[2]
        )
        url = f"postgresql://tracker:tracker@127.0.0.1:{port}/postgres"

        async def ready() -> bool:
            try:
                conn = await asyncpg.connect(url, timeout=2)
            except OSError, asyncpg.PostgresError, TimeoutError:
                return False
            await conn.close()
            return True

        deadline = time.monotonic() + 60
        while not asyncio.run(ready()):
            if time.monotonic() > deadline:
                subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)
                pytest.fail("PostgreSQL container did not become ready")
            time.sleep(0.25)
        return name, url

    def url(self, database: str) -> str:
        base, _, _ = self.admin_url.rpartition("/")
        return f"{base}/{database}"

    def _admin(self, *statements: str) -> None:
        async def run() -> None:
            conn = await asyncpg.connect(self.admin_url)
            try:
                for statement in statements:
                    await conn.execute(statement)
            finally:
                await conn.close()

        asyncio.run(run())

    def prepare_template(self) -> None:
        self._admin(f"DROP DATABASE IF EXISTS {self.template}", f"CREATE DATABASE {self.template}")
        self.created.append(self.template)
        asyncio.run(Migrations().apply(self.url(self.template)))

    def create(self, migrated: bool = True) -> str:
        """A new empty database (migrated: cloned from the migrated template); returns its URL."""
        name = f"tracker_test_{os.getpid()}_{next(self._counter)}"
        template = f" TEMPLATE {self.template}" if migrated else ""
        self._admin(f"CREATE DATABASE {name}{template}")
        self.created.append(name)
        return self.url(name)

    def drop_all(self) -> None:
        self._admin(*(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)" for name in self.created))


class TrackerServer:
    """A tracker served by uvicorn on an ephemeral port in a background thread."""

    # The account a fresh database is bootstrapped with (see ``bootstrap``).
    ADMIN_EMAIL = "admin@flock.test"

    def __init__(
        self,
        database_url: str,
        files_dir: Path,
        api_key: str | None = None,
        bootstrap: bool = True,
        **settings: Any,
    ) -> None:
        self.database_url = database_url
        self.files_dir = files_dir
        self.api_key = api_key
        self.store = Store(Settings(database_url=database_url, files_dir=files_dir, **settings))
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(
            create_app(self.store),
            log_level="warning",
            access_log=False,
            http="httptools",
        )
        self._server = uvicorn.Server(config)
        self.loop = uvloop.new_event_loop()
        self._thread = threading.Thread(
            target=lambda: self.loop.run_until_complete(self._server.serve(sockets=[sock])),
            daemon=True,
        )
        self._thread.start()
        deadline = time.monotonic() + 30
        while not self._server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                raise RuntimeError("tracker server failed to start")
            time.sleep(0.01)
        if bootstrap and self.api_key is None:
            self.api_key = self.call(self.bootstrap())

    async def bootstrap(self) -> str:
        """Sign up the super admin (a member of every org, or of a new ``flock`` org) unless
        the database has users already; returns a new API key of the super admin."""
        accounts = self.store.accounts
        if accounts.signup_open:
            admin = await accounts.signup(self.ADMIN_EMAIL, "Admin")
        else:
            admin = next(u for u in accounts.users.values() if u.is_superadmin)
        return (await accounts.create_api_key(admin, "tests"))[1]

    def call(self, coro: Coroutine) -> Any:
        """Run a coroutine on the server's event loop (where all store mutations happen)."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=600)

    def client(self, api_key: str | None = None, token: str | None = None) -> Client:
        """A client with ``api_key`` or ``token``, else the super admin's API key."""
        return Client(self.port, api_key or (None if token else self.api_key), token)

    def anonymous(self) -> Client:
        return Client(self.port)

    def add_user(
        self, email: str, role: str = "member", org: str = "flock", password: str = "password1"
    ) -> User:
        """Invite ``email`` into ``org`` as the super admin, and accept as a new account through
        the API; returns the user with an ``api`` client (on their new API key)."""
        admin = self.client()
        invite = admin.post(f"/api/v1/orgs/{org}/invites", {"email": email, "role": role})
        assert invite.status == 201, invite.body
        accepted = self.anonymous().post(
            f"/api/v1/invites/{invite.json()['token']}/accept",
            {"name": email.partition("@")[0], "password": password},
        )
        assert accepted.status == 201, accepted.body
        body = accepted.json()
        user_client = self.client(token=body["access_token"])
        key = user_client.post("/api/v1/me/api-keys", {"name": "tests"}).json()["key"]
        return User(body["user"]["id"], email, password, key, self.client(api_key=key), body)

    def stop(self) -> None:
        # As on SIGTERM in production: end dashboard streams, which never finish on their own.
        self.loop.call_soon_threadsafe(self.store.live.close)
        self._server.should_exit = True
        self._thread.join(timeout=60)


@dataclass
class User:
    """An account created through the API, with a client using its API key."""

    id: int
    email: str
    password: str
    api_key: str
    api: Client
    signup: dict  # the tokens and profile its sign-up returned


class Trackers:
    """Starts trackers on fresh databases (or restarts them on old ones) and stops them all."""

    def __init__(self, postgres: Postgres, base: Path) -> None:
        self.postgres = postgres
        self.base = base
        self.servers: list[TrackerServer] = []

    def __call__(
        self,
        reuse: TrackerServer | None = None,
        database_url: str | None = None,
        **settings: Any,
    ) -> TrackerServer:
        """A tracker on a fresh database (or ``database_url``), or restarted like ``reuse``."""
        if reuse is not None:
            settings.setdefault("api_key", reuse.api_key)
            server = TrackerServer(reuse.database_url, reuse.files_dir, **settings)
        else:
            files_dir = self.base / f"tracker-files-{len(self.servers)}"
            url = database_url or self.postgres.create()
            server = TrackerServer(url, files_dir, **settings)
        self.servers.append(server)
        return server

    def stop(self) -> None:
        for server in self.servers:
            server.stop()


class WandbClient:
    """Runs real ``wandb`` SDK scripts / CLI commands against a tracker in subprocesses."""

    # Prepended to every script: flush the file stream quickly so tests don't wait on the SDK's
    # default multi-second transmit interval.
    PREAMBLE = """
import wandb
SETTINGS = wandb.Settings(x_file_stream_transmit_interval=0.1)
"""

    def __init__(self, server: TrackerServer, workdir: Path, api_key: str | None = None) -> None:
        self.workdir = workdir
        workdir.mkdir(parents=True, exist_ok=True)
        home = workdir / "home"
        home.mkdir(exist_ok=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith("WANDB_")}
        env.update(
            HOME=str(home),
            WANDB_BASE_URL=server.url,
            WANDB_API_KEY=api_key or server.api_key or "",
            WANDB_DIR=str(workdir),
            WANDB_CONFIG_DIR=str(home / ".config" / "wandb"),
            WANDB_CACHE_DIR=str(home / ".cache" / "wandb"),
            WANDB_DATA_DIR=str(home / ".local" / "wandb"),
            WANDB_SILENT="true",
            WANDB_ERROR_REPORTING="false",
            WANDB_DISABLE_GIT="true",
        )
        self.env = env
        self._scripts = 0

    def _script(self, code: str) -> Path:
        self._scripts += 1
        path = self.workdir / f"client_{self._scripts}.py"
        path.write_text(self.PREAMBLE + textwrap.dedent(code))
        return path

    def run(
        self, code: str, *args: str, check: bool = True, timeout: float = 180, **env: str
    ) -> subprocess.CompletedProcess:
        """Run a wandb client script in a fresh interpreter and wait for it to exit."""
        return self._finish(
            subprocess.run(
                [sys.executable, str(self._script(code)), *args],
                env={**self.env, **env},
                cwd=self.workdir,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            ),
            check,
        )

    def cli(self, *args: str, check: bool = True, timeout: float = 180, **env: str):
        """Run the ``wandb`` command line tool."""
        return self._finish(
            subprocess.run(
                [sys.executable, "-m", "wandb", *args],
                env={**self.env, **env},
                cwd=self.workdir,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            ),
            check,
        )

    def popen(self, code: str, *args: str, **env: str) -> subprocess.Popen:
        """Start a wandb client script without waiting; its stdin is a pipe."""
        return subprocess.Popen(
            [sys.executable, str(self._script(code)), *args],
            env={**self.env, **env},
            cwd=self.workdir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    @staticmethod
    def _finish(proc: subprocess.CompletedProcess, check: bool) -> subprocess.CompletedProcess:
        if check and proc.returncode != 0:
            raise AssertionError(
                f"wandb process failed ({proc.returncode})\nstdout:\n{proc.stdout}\n"
                f"stderr:\n{proc.stderr}"
            )
        return proc


@pytest.fixture(scope="session")
def postgres() -> Iterator[Postgres]:
    """``TRACKER_TEST_DATABASE_URL`` (an admin URL) if set, else a PostgreSQL container."""
    container = None
    url = os.environ.get("TRACKER_TEST_DATABASE_URL")
    if not url:
        if shutil.which("docker") is None:
            pytest.skip("set TRACKER_TEST_DATABASE_URL or install Docker to run tracker tests")
        container, url = Postgres.start_container()
    server = Postgres(url)
    try:
        server.prepare_template()
        yield server
        if container is None:
            server.drop_all()
    finally:
        if container is not None:
            subprocess.run(["docker", "rm", "-f", container], check=False, capture_output=True)


@pytest.fixture
def make_tracker(postgres: Postgres, tmp_path: Path) -> Iterator[Trackers]:
    """Factory for trackers with custom settings, or restarted on an existing database."""
    trackers = Trackers(postgres, tmp_path)
    yield trackers
    trackers.stop()


@pytest.fixture(scope="module")
def make_module_tracker(
    postgres: Postgres, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Trackers]:
    """Like ``make_tracker`` but shared by a whole module (for expensive datasets)."""
    trackers = Trackers(postgres, tmp_path_factory.mktemp("tracker"))
    yield trackers
    trackers.stop()


@pytest.fixture
def tracker(make_tracker: Trackers) -> TrackerServer:
    return make_tracker()


@pytest.fixture
def api(tracker: TrackerServer) -> Iterator[Client]:
    client = tracker.client()
    yield client
    client.close()


@pytest.fixture
def wandb_client(tracker: TrackerServer, tmp_path: Path) -> WandbClient:
    pytest.importorskip("wandb")
    return WandbClient(tracker, tmp_path / "wandb-client")


@pytest.fixture
def make_wandb_client(tmp_path: Path):
    """Factory of wandb clients, each with its own working directory."""
    pytest.importorskip("wandb")
    count = itertools.count()

    def make(server: TrackerServer, api_key: str | None = None) -> WandbClient:
        return WandbClient(server, tmp_path / f"wandb-client-{next(count)}", api_key)

    return make


@pytest.fixture
def events(request: pytest.FixtureRequest) -> Iterator:
    """Opens dashboard event streams (on the ``api`` tracker unless a client is given)."""
    streams: list[EventStream] = []

    def open_stream(
        project: str, runs: str, system: bool = False, client: Client | None = None
    ) -> EventStream:
        client = client or request.getfixturevalue("api")
        path = f"/api/v1/projects/flock/{project}/stream?runs={runs}"
        stream = EventStream(client, path + ("&system=true" if system else ""))
        assert stream.resp.status == 200
        assert (stream.resp.getheader("content-type") or "").startswith("text/event-stream")
        streams.append(stream)
        return stream

    yield open_stream
    for stream in streams:
        stream.close()


@pytest.fixture
def wait_for():
    def wait(predicate, timeout: float = 30.0, interval: float = 0.05):
        deadline = time.monotonic() + timeout
        while True:
            value = predicate()
            if value:
                return value
            if time.monotonic() > deadline:
                raise AssertionError("timed out waiting for condition")
            time.sleep(interval)

    return wait


@pytest.fixture
def api_key(tracker: TrackerServer) -> str:
    """The ``tracker`` super admin's API key."""
    assert tracker.api_key is not None
    return tracker.api_key
