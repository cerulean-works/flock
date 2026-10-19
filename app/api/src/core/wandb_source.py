"""Reading an entity's projects and runs from a W&B server, over its GraphQL API.

The wandb SDK's public API would do, but it runs a separate Go process, is synchronous and
fetches lazily (a request per run per attribute); these few queries are what it sends.
"""

import asyncio
import datetime as dt
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
import orjson

from app.api.src.utils import loads_lenient

log = logging.getLogger(__name__)
# httpx logs every request at INFO, signed file URLs included.
logging.getLogger("httpx").setLevel(logging.WARNING)


class WandbError(Exception):
    """W&B could not be reached or refused a request."""


class WandbAuthError(WandbError):
    """W&B rejected the API key."""


class WandbNotFound(WandbError):
    """The entity (or project) doesn't exist, or the API key can't see it."""


@dataclass
class SourceProject:
    name: str
    description: str | None
    created_at: float | None
    run_count: int


@dataclass
class SourceFile:
    name: str
    size: int
    url: str | None


def parse_time(value: str | None) -> float | None:
    """W&B's ISO 8601 times, which are UTC and often say so only implicitly."""
    if not value:
        return None
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.timestamp()


def parse_json(value: Any) -> Any:
    """A JSONString field: W&B sends JSON text, which may hold NaN and Infinity."""
    if isinstance(value, str):
        return loads_lenient(value) if value else None
    return value


VIEWER = """
query FlockViewer($entity: String!) {
  viewer { username }
  entity(name: $entity) { name }
}
"""

MEMBERS = """
query FlockMembers($entity: String!) {
  entity(name: $entity) { members { username email } }
}
"""

PROJECTS = """
query FlockProjects($entity: String!, $cursor: String) {
  models(entityName: $entity, first: 100, after: $cursor) {
    pageInfo { endCursor hasNextPage }
    edges { node { name description createdAt runCount } }
  }
}
"""

RUNS = """
query FlockRuns($entity: String!, $project: String!, $cursor: String) {
  project(name: $project, entityName: $entity) {
    runs(first: 50, after: $cursor, order: "+created_at") {
      pageInfo { endCursor hasNextPage }
      edges {
        node {
          name displayName state config summaryMetrics tags: tagNames notes group jobType
          sweepName commit createdAt heartbeatAt historyLineCount historyKeys
          user { username }
        }
      }
    }
  }
}
"""

HISTORY = """
query FlockHistory($entity: String!, $project: String!, $run: String!, $minStep: Int64!,
                   $maxStep: Int64!, $samples: Int!) {
  project(name: $project, entityName: $entity) {
    run(name: $run) {
      history(minStep: $minStep, maxStep: $maxStep, samples: $samples)
    }
  }
}
"""

EVENTS = """
query FlockEvents($entity: String!, $project: String!, $run: String!, $samples: Int!) {
  project(name: $project, entityName: $entity) {
    run(name: $run) { events(samples: $samples) }
  }
}
"""

FILES = """
query FlockFiles($entity: String!, $project: String!, $run: String!, $names: [String!]!) {
  project(name: $project, entityName: $entity) {
    run(name: $run) {
      files(names: $names, first: 100) {
        edges { node { name sizeBytes directUrl } }
      }
    }
  }
}
"""


class WandbSource:
    """A W&B server, read with one user's API key."""

    # Steps per history request (the wandb SDK's scan_history page size).
    HISTORY_PAGE = 1000
    # System metric samples asked for when a run has no wandb-events.jsonl file.
    EVENT_SAMPLES = 100_000
    RETRIES = 5

    def __init__(self, base_url: str, api_key: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._auth = httpx.BasicAuth("api", api_key)
        self._origin = urlsplit(self.base_url)[:2]
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ transport

    async def _request(
        self, method: str, url: str, retries: int = RETRIES, **kwargs: Any
    ) -> httpx.Response:
        """Send a request, retrying rate limits, server errors and dropped connections."""
        delay = 1.0
        for attempt in range(retries):
            last = attempt == retries - 1
            try:
                response = await self._client.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                if last:
                    raise WandbError(f"could not reach W&B at {self.base_url}: {exc}") from exc
            else:
                if response.status_code in (401, 403):
                    raise WandbAuthError("W&B rejected the API key")
                if response.status_code != 429 and response.status_code < 500:
                    return response
                if last:
                    raise WandbError(f"W&B answered {response.status_code}")
                retry_after = response.headers.get("retry-after", "")
                if retry_after.isdigit():
                    delay = max(delay, float(retry_after))
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)
        raise AssertionError("unreachable")

    async def query(self, query: str, retries: int = RETRIES, **variables: Any) -> dict[str, Any]:
        response = await self._request(
            "POST", f"{self.base_url}/graphql", retries,
            json={"query": query, "variables": variables}, auth=self._auth,
        )  # fmt: skip
        try:
            body = response.json()
        except ValueError as exc:
            raise WandbError(f"W&B answered {response.status_code} with no JSON") from exc
        errors = body.get("errors") if isinstance(body, dict) else None
        if errors:
            message = "; ".join(str(e.get("message", e)) for e in errors)
            raise WandbError(f"W&B refused the query: {message}")
        if response.status_code >= 400 or not isinstance(body, dict):
            raise WandbError(f"W&B answered {response.status_code}")
        return body.get("data") or {}

    async def download(self, url: str, max_bytes: int) -> bytes | None:
        """A file's content; None if it is larger than ``max_bytes``. The API key is only
        sent to the W&B server itself, not to the storage its signed URLs point at."""
        same_origin = urlsplit(url)[:2] == self._origin
        auth = self._auth if same_origin else httpx.USE_CLIENT_DEFAULT
        delay = 1.0
        for attempt in range(self.RETRIES):
            last = attempt == self.RETRIES - 1
            try:
                async with self._client.stream("GET", url, auth=auth) as response:
                    status = response.status_code
                    if status != 429 and status < 500:
                        if status >= 400:
                            raise WandbError(f"could not download a run file ({status})")
                        chunks, size = [], 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > max_bytes:
                                return None
                            chunks.append(chunk)
                        return b"".join(chunks)
                    if last:
                        raise WandbError(f"W&B file storage answered {status}")
            except httpx.TransportError as exc:
                if last:
                    raise WandbError(f"could not download a run file: {exc}") from exc
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)
        raise AssertionError("unreachable")

    # ------------------------------------------------------------------ reads

    async def check(self, entity: str) -> dict[str, str]:
        """Check the key works and can see ``entity``; returns its members' emails by
        username (empty when W&B won't tell)."""
        # Someone is waiting on this one: fail fast rather than retry.
        data = await self.query(VIEWER, retries=1, entity=entity)
        if not data.get("viewer"):
            raise WandbAuthError("W&B rejected the API key")
        if not data.get("entity"):
            raise WandbNotFound(f"W&B entity {entity} not found")
        try:
            members = await self.query(MEMBERS, retries=1, entity=entity)
        except WandbError:  # only shown to some (an entity's admins, say)
            return {}
        found = (members.get("entity") or {}).get("members") or []
        return {
            m["username"]: m["email"].lower() for m in found if m.get("username") and m.get("email")
        }

    async def projects(self, entity: str) -> list[SourceProject]:
        projects, cursor = [], None
        while True:
            data = (await self.query(PROJECTS, entity=entity, cursor=cursor)).get("models")
            if data is None:
                raise WandbNotFound(f"W&B entity {entity} not found")
            for edge in data.get("edges") or []:
                node = edge["node"]
                projects.append(
                    SourceProject(
                        name=node["name"],
                        description=node.get("description") or None,
                        created_at=parse_time(node.get("createdAt")),
                        run_count=int(node.get("runCount") or 0),
                    )
                )
            page = data.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return projects
            cursor = page.get("endCursor")

    async def runs(self, entity: str, project: str) -> AsyncGenerator[list[dict[str, Any]]]:
        """The project's runs, oldest first, a page at a time (as raw GraphQL nodes)."""
        cursor = None
        while True:
            data = await self.query(RUNS, entity=entity, project=project, cursor=cursor)
            found = data.get("project")
            if found is None:
                raise WandbNotFound(f"W&B project {entity}/{project} not found")
            runs = found.get("runs") or {}
            yield [edge["node"] for edge in runs.get("edges") or []]
            page = runs.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return
            cursor = page.get("endCursor")

    async def _run_field(self, query: str, entity: str, project: str, run: str, **variables):
        data = await self.query(query, entity=entity, project=project, run=run, **variables)
        found = (data.get("project") or {}).get("run")
        if found is None:
            raise WandbNotFound(f"W&B run {entity}/{project}/{run} not found")
        return found

    async def history(self, entity: str, project: str, run: str, last_step: int) -> list[str]:
        """Every history row of a run up to ``last_step``, as JSON lines in step order."""
        lines: list[str] = []
        step = 0
        while step <= last_step:
            stop = step + self.HISTORY_PAGE
            found = await self._run_field(
                HISTORY, entity, project, run, minStep=step, maxStep=stop,
                samples=self.HISTORY_PAGE,
            )  # fmt: skip
            lines += [_line(row) for row in found.get("history") or []]
            step = stop
        return lines

    async def events(self, entity: str, project: str, run: str) -> list[str]:
        found = await self._run_field(EVENTS, entity, project, run, samples=self.EVENT_SAMPLES)
        return [_line(row) for row in found.get("events") or []]

    async def files(
        self, entity: str, project: str, run: str, names: list[str]
    ) -> dict[str, SourceFile]:
        found = await self._run_field(FILES, entity, project, run, names=names)
        files = {}
        for edge in (found.get("files") or {}).get("edges") or []:
            node = edge.get("node") or {}
            if node.get("name") in names:
                files[node["name"]] = SourceFile(
                    node["name"], int(node.get("sizeBytes") or 0), node.get("directUrl")
                )
        return files


def _line(row: Any) -> str:
    """A history or events row as a JSON line (W&B sends JSON text; some servers objects)."""
    return row if isinstance(row, str) else orjson.dumps(row).decode()
