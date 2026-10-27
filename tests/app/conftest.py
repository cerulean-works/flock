import asyncio
import base64
import dataclasses
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

from app.api.app import create_app
from app.api.src.core.store import Store
from app.api.src.db.migrate import Migrations
from app.api.src.s3_proxy.app import create_app as create_proxy_app
from app.api.src.settings import Settings
from tests.conftest import S3Server


class Response:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.body = body
        self.headers = headers or {}  # lowercased names

    def json(self) -> Any:
        return orjson.loads(self.body)


class Client:
    """Minimal keep-alive HTTP client (stdlib only) for talking to Flock in tests.

    It authenticates with a wandb key (``api_key``, sent as the wandb SDK does) and/or a
    ``token`` (a CLI token or a browser's access token). With both, it sends each where it
    belongs (D70): the token to the REST API (``/api/…``), the wandb key to the wandb protocol
    (everything else). With one, it sends that one everywhere.
    """

    def __init__(self, port: int, api_key: str | None = None, token: str | None = None) -> None:
        self.port = port
        self.headers: dict[str, str] = {}  # for the REST API
        self.wandb_headers: dict[str, str] = {}  # for the wandb protocol
        if api_key:  # the wandb SDK's scheme: basic auth with user "api"
            basic = base64.b64encode(f"api:{api_key}".encode()).decode()
            self.wandb_headers["Authorization"] = f"Basic {basic}"
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        self.headers = self.headers or self.wandb_headers
        self.wandb_headers = self.wandb_headers or self.headers
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
        sent = self.headers if path.startswith("/api/") else self.wandb_headers
        hdrs = {**sent, **(headers or {})}
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
        self, project: str | None, name: str, config=None, tags=None, display_name=None
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

    def upload(self, project: str, run: str, name: str, data: bytes, entity: str = "flock"):
        """Upload a run file as the SDK does: a signed URL from CreateRunFiles, then a PUT."""
        query = """
        mutation CreateRunFiles($entity: String!, $project: String!, $run: String!,
                                $files: [String!]!) {
          createRunFiles(input: {entityName: $entity, projectName: $project, runName: $run,
                                 files: $files}) {
            files { name uploadUrl }
          }
        }"""
        variables = {"entity": entity, "project": project, "run": run, "files": [name]}
        body = self.graphql(query, variables).json()
        assert "errors" not in body, body
        url = body["data"]["createRunFiles"]["files"][0]["uploadUrl"]
        resp = self.request("PUT", url.split(str(self.port), 1)[1], body=data)
        assert resp.status == 200, resp.body

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
        num = np.frombuffer(body, "<f8", n_num, pos).reshape(len(header["numeric"]), len(runs))
        pos += n_num * 8
        meta = np.frombuffer(body, "<f8", len(runs) * len(metrics) * 6, pos).reshape(-1, 6)
        pos += meta.size * 8
        xs = np.frombuffer(body, "<f8", header["x_len"], pos)
        pos += header["x_len"] * 8
        ys = np.frombuffer(body, "<f4", header["y_len"], pos)
        pos += header["y_len"] * 4 + header["y_len"] % 2 * 4
        # Per run, its x-axis mappings: knots (x, y) of each axis against _step, then of each
        # system axis against _runtime.
        axes = header["axes"] + header["system_axes"]
        axis_meta = np.frombuffer(body, "<f8", len(runs) * len(axes) * 2, pos).reshape(-1, 2)
        pos += axis_meta.size * 8
        axis_values = np.frombuffer(body, "<f8", header["axes_len"], pos)
        assert pos + header["axes_len"] * 8 == len(body)

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
        mappings: dict[tuple[str, str], tuple[list[float], list[float]]] = {}
        for r, run in enumerate(runs):
            for a, key in enumerate(axes):
                off, count = (int(v) for v in axis_meta[r * len(axes) + a])
                if count:
                    stream = "system:" if a >= len(header["axes"]) else ""
                    mappings[(run["id"], stream + key)] = (
                        axis_values[off : off + count].tolist(),
                        axis_values[off + count : off + 2 * count].tolist(),
                    )
        return {**header, "cells": cells, "curves": curves, "mappings": mappings}


class EventStream:
    """Reads a dashboard's Server-Sent Events from Flock in the test thread."""

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
        self.template = f"flock_template_{os.getpid()}"
        self._counter = itertools.count()
        self.created: list[str] = []

    @staticmethod
    def start_container(image: str = "postgres:18.4") -> tuple[str, str]:
        """Start a throwaway PostgreSQL container; returns (container name, admin URL)."""
        image = os.environ.get("FLOCK_TEST_POSTGRES_IMAGE", image)
        name = f"flock-test-{os.getpid()}"
        started = subprocess.run(
            [
                "docker", "run", "-d", "--rm", "--name", name,
                "-e", "POSTGRES_USER=flock", "-e", "POSTGRES_PASSWORD=flock",
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
        url = f"postgresql://flock:flock@127.0.0.1:{port}/postgres"

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
        name = f"flock_test_{os.getpid()}_{next(self._counter)}"
        template = f" TEMPLATE {self.template}" if migrated else ""
        self._admin(f"CREATE DATABASE {name}{template}")
        self.created.append(name)
        return self.url(name)

    def drop_all(self) -> None:
        self._admin(*(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)" for name in self.created))


class FlockServer:
    """A flock served by uvicorn on an ephemeral port in a background thread."""

    # The account a fresh database is bootstrapped with (see ``bootstrap``).
    ADMIN_EMAIL = "admin@flock.test"

    def __init__(
        self,
        database_url: str,
        files_dir: Path,
        api_key: str | None = None,
        token: str | None = None,
        bootstrap: bool = True,
        **settings: Any,
    ) -> None:
        self.database_url = database_url
        self.files_dir = files_dir
        self.api_key = api_key  # the super admin's wandb key, for the wandb SDK
        self.token = token  # and CLI token, for the REST API
        settings.setdefault("gravatar", False)  # tests needing it use a fake Gravatar
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
                raise RuntimeError("flock server failed to start")
            time.sleep(0.01)
        if bootstrap and (self.api_key is None or self.token is None):
            self.call(self.bootstrap())

    async def bootstrap(self) -> None:
        """Complete setup with an explicit ``flock`` org on fresh databases, and give the super
        admin a wandb key (``api_key``) and a CLI token (``token``), as ``flock login`` would,
        unless they were given."""
        accounts = self.store.accounts
        if accounts.signup_open:
            admin = await accounts.signup(self.ADMIN_EMAIL, "Admin")
            if not self.store.orgs:
                org = await self.store.create_org("flock")
                await accounts.add_member(admin, org, "admin")
        else:
            admin = next(u for u in accounts.users.values() if u.is_superadmin)
        if self.api_key is None:
            self.api_key = (await accounts.create_api_key(admin, "tests"))[1]
        if self.token is None:
            self.token = (await accounts.start_cli_session(admin, "tests"))[1]

    def call(self, coro: Coroutine) -> Any:
        """Run a coroutine on the server's event loop (where all store mutations happen)."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=600)

    def client(self, api_key: str | None = None, token: str | None = None) -> Client:
        """A client with the wandb key ``api_key`` and/or ``token`` (see ``Client``), else the
        super admin's wandb key and CLI token."""
        if api_key is None and token is None:
            return Client(self.port, self.api_key, self.token)
        return Client(self.port, api_key, token)

    def anonymous(self) -> Client:
        return Client(self.port)

    def add_user(
        self, email: str, role: str = "member", org: str = "flock", password: str = "password1"
    ) -> User:
        """Invite ``email`` into ``org`` as the super admin, and accept as a new account through
        the API; returns the user with a new wandb key and CLI token, and an ``api`` client on
        both."""
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
        key = user_client.post("/api/v1/me/wandb-keys", {"name": "tests"}).json()["key"]
        user = self.store.accounts.users[body["user"]["id"]]
        token = self.call(self.store.accounts.start_cli_session(user, "tests"))[1]
        api = self.client(api_key=key, token=token)
        return User(body["user"]["id"], email, password, key, api, body, token)

    def stop(self) -> None:
        # As on SIGTERM in production: end dashboard streams, which never finish on their own.
        self.loop.call_soon_threadsafe(self.store.live.close)
        self._server.should_exit = True
        self._thread.join(timeout=60)


def _listen() -> socket.socket:
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    return sock


class ProxyServer:
    """The S3 proxy served by uvicorn on ``sock`` in a background thread."""

    def __init__(self, settings: Settings, sock: socket.socket | None = None) -> None:
        sock = sock or _listen()
        self.port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(
            create_proxy_app(settings),
            log_level="warning",
            access_log=False,
            http="httptools",
            lifespan="on",
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
                raise RuntimeError("S3 proxy failed to start")
            time.sleep(0.01)

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=30)


class S3Flock(FlockServer):
    """A flock with S3 on: the proxy in front of ``s3`` (the test S3 server), on the flock's
    database, its buckets named with a prefix of their own (see ``S3Server.bucket_prefix``)."""

    def __init__(
        self,
        database_url: str,
        files_dir: Path,
        s3: S3Server,
        bucket_prefix: str | None = None,
        **settings: Any,
    ) -> None:
        self.s3 = s3
        sock = _listen()
        proxy_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        self.bucket_prefix = bucket_prefix if bucket_prefix is not None else s3.bucket_prefix()
        configured: dict[str, Any] = {
            "s3_upstream_endpoint": s3.endpoint,
            "s3_upstream_region": s3.region,
            "s3_upstream_access_key_id": s3.access_key_id,
            "s3_upstream_secret_access_key": s3.secret_access_key,
            "s3_bucket_prefix": self.bucket_prefix,
            "s3_public_url": proxy_url,
        }
        super().__init__(database_url, files_dir, **{**configured, **settings})
        # The flock has applied the migrations the proxy reads.
        self.proxy = ProxyServer(dataclasses.replace(self.store.settings, pool_size=8), sock)

    def credentials(self, client: Client | None = None, **body: Any) -> dict:
        """An ephemeral S3 key of ``client``'s user (the super admin by default)."""
        got = (client or self.client()).post("/api/v1/s3/credentials", body)
        assert got.status == 200, got.body
        return got.json()

    def stop(self) -> None:
        self.proxy.stop()
        super().stop()


@dataclass
class User:
    """An account created through the API, with a client using its wandb key and CLI token."""

    id: int
    email: str
    password: str
    api_key: str  # a wandb key
    api: Client
    signup: dict  # the tokens and profile its sign-up returned
    token: str  # a CLI token


class Flocks:
    """Starts flocks on fresh databases (or restarts them on old ones) and stops them all."""

    def __init__(self, postgres: Postgres, base: Path) -> None:
        self.postgres = postgres
        self.base = base
        self.servers: list[FlockServer] = []

    def __call__(
        self,
        reuse: FlockServer | None = None,
        database_url: str | None = None,
        **settings: Any,
    ) -> FlockServer:
        """A flock on a fresh database (or ``database_url``), or restarted like ``reuse``."""
        if reuse is not None:
            settings.setdefault("api_key", reuse.api_key)
            settings.setdefault("token", reuse.token)
            server = FlockServer(reuse.database_url, reuse.files_dir, **settings)
        else:
            files_dir = self.base / f"flock-files-{len(self.servers)}"
            url = database_url or self.postgres.create()
            server = FlockServer(url, files_dir, **settings)
        self.servers.append(server)
        return server

    def s3(self, s3: S3Server, reuse: S3Flock | None = None, **settings: Any) -> S3Flock:
        """A flock with S3 on over ``s3`` (see ``S3Flock``), fresh or restarted like ``reuse``
        (with its bucket prefix)."""
        if reuse is not None:
            settings.setdefault("api_key", reuse.api_key)
            settings.setdefault("token", reuse.token)
            settings.setdefault("bucket_prefix", reuse.bucket_prefix)
            server = S3Flock(reuse.database_url, reuse.files_dir, s3, **settings)
        else:
            files_dir = self.base / f"flock-files-{len(self.servers)}"
            server = S3Flock(self.postgres.create(), files_dir, s3, **settings)
        self.servers.append(server)
        return server

    def stop(self) -> None:
        for server in self.servers:
            server.stop()


class WandbClient:
    """Runs real ``wandb`` SDK scripts / CLI commands against a flock in subprocesses."""

    # Prepended to every script: flush the file stream quickly so tests don't wait on the SDK's
    # default multi-second transmit interval.
    PREAMBLE = """
import wandb
SETTINGS = wandb.Settings(x_file_stream_transmit_interval=0.1)
"""

    def __init__(self, server: FlockServer, workdir: Path, api_key: str | None = None) -> None:
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


def serve_postgres() -> Iterator[Postgres]:
    """``FLOCK_TEST_DATABASE_URL`` (an admin URL) if set, else a PostgreSQL container. The
    session's ``postgres`` fixture (in ``tests/conftest.py``, shared with the CLI tests)."""
    container = None
    url = os.environ.get("FLOCK_TEST_DATABASE_URL")
    if not url:
        if shutil.which("docker") is None:
            pytest.skip("set FLOCK_TEST_DATABASE_URL or install Docker to run flock tests")
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
def make_flock(postgres: Postgres, tmp_path: Path) -> Iterator[Flocks]:
    """Factory for flocks with custom settings, or restarted on an existing database."""
    flocks = Flocks(postgres, tmp_path)
    yield flocks
    flocks.stop()


@pytest.fixture(scope="module")
def make_module_flock(
    postgres: Postgres, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Flocks]:
    """Like ``make_flock`` but shared by a whole module (for expensive datasets)."""
    flocks = Flocks(postgres, tmp_path_factory.mktemp("flock"))
    yield flocks
    flocks.stop()


@pytest.fixture
def flock(make_flock: Flocks) -> FlockServer:
    return make_flock()


@pytest.fixture
def api(flock: FlockServer) -> Iterator[Client]:
    client = flock.client()
    yield client
    client.close()


@pytest.fixture
def wandb_client(flock: FlockServer, tmp_path: Path) -> WandbClient:
    pytest.importorskip("wandb")
    return WandbClient(flock, tmp_path / "wandb-client")


@pytest.fixture
def make_wandb_client(tmp_path: Path):
    """Factory of wandb clients, each with its own working directory."""
    pytest.importorskip("wandb")
    count = itertools.count()

    def make(server: FlockServer, api_key: str | None = None) -> WandbClient:
        return WandbClient(server, tmp_path / f"wandb-client-{next(count)}", api_key)

    return make


@pytest.fixture
def events(request: pytest.FixtureRequest) -> Iterator:
    """Opens dashboard event streams (on the ``api`` flock unless a client is given)."""
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
def api_key(flock: FlockServer) -> str:
    """The ``flock`` super admin's wandb key."""
    assert flock.api_key is not None
    return flock.api_key
