import base64
import gzip
import hashlib
import hmac
import re
from typing import Any, ClassVar
from urllib.parse import quote

import orjson
from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from src.api.deps import APIError, JSONResponse, StoreDep, UserDep, visible_org
from src.core.accounts import User
from src.core.errors import Discarded, Forbidden, NotFound
from src.core.store import Org, Store
from src.utils import encode_id, loads_lenient

router = APIRouter(include_in_schema=False)


class GraphQLError(Exception):
    pass


class GraphQL:
    """Answers the GraphQL operations the wandb SDK sends, dispatched by operation name.

    There is no general GraphQL engine: each operation returns the fields the SDK selects, and
    unknown operations get a GraphQL error rather than a guessed answer.
    """

    OPERATION_NAME = re.compile(r"^\s*(?:query|mutation)\s+(\w+)")
    # Operation name -> method answering it.
    OPERATIONS: ClassVar[dict[str, str]] = {
        "Viewer": "viewer",
        "UpsertBucket": "upsert_bucket",
        "RunResumeStatus": "run_resume_status",
        "CreateRunFiles": "create_run_files",
        "RunState": "run_state",
    }
    # Operations with a fixed answer: feature probes, and lookups of wandb's own organization
    # model (orgs here are plain entities).
    STATIC: ClassVar[dict[str, dict[str, Any]]] = {
        "ServerFeaturesQuery": {
            "serverInfo": {"features": [], "cliVersionInfo": None, "latestLocalVersionInfo": None}
        },
        "XXHashDigestsEnabled": {"entity": {"xxhashDigestsEnabled": False}},
        "MarkRunFilesUploaded": {"markRunFilesUploaded": {"success": True}},
        "ClientIDMapping": {"clientIDMapping": None},
        "OrganizationCoreWeaveOrganizationID": {"entity": None},
        "FetchOrgEntityFromEntity": {"entity": None},
        "OrgFeatureFlags": {"organization": None},
    }
    STATIC["ServerInfo"] = STATIC["ServerFeaturesQuery"]
    # Root fields identifying operations sent without (or under a different) name.
    ROOT_FIELDS = (
        ("upsertBucket", "UpsertBucket"),
        ("createRunFiles", "CreateRunFiles"),
        ("markRunFilesUploaded", "MarkRunFilesUploaded"),
        ("viewer", "Viewer"),
        ("serverInfo", "ServerFeaturesQuery"),
    )

    def __init__(self, store: Store, user: User, request: Request) -> None:
        self.store = store
        self.user = user
        self.request = request

    @staticmethod
    def sign(store: Store, entity: str, project: str, run: str, name: str) -> str:
        """Signature authorizing the upload of one run file (see ``create_run_files``)."""
        message = f"{entity}/{project}/{run}/{name}".encode()
        return hmac.new(store.secret, message, hashlib.sha256).hexdigest()

    async def execute(self, payload: dict[str, Any]) -> dict:
        query = payload.get("query") or ""
        name = payload.get("operationName")
        if not name:
            match = self.OPERATION_NAME.match(query)
            name = match.group(1) if match else None
        if name not in self.OPERATIONS and name not in self.STATIC:
            name = next((op for f, op in self.ROOT_FIELDS if re.search(rf"\b{f}\b", query)), name)
        if name in self.STATIC:
            return {"data": self.STATIC[name]}
        if name not in self.OPERATIONS:
            return {
                "data": None,
                "errors": [{"message": f"operation {name!r} is not supported by this server"}],
            }
        try:
            data = await getattr(self, self.OPERATIONS[name])(payload.get("variables") or {})
        except GraphQLError as exc:
            return {"data": None, "errors": [{"message": str(exc)}]}
        return {"data": data}

    def org(self, name: str | None) -> tuple[Org, str]:
        """The org a request names (by default the user's default org) and the user's role."""
        accounts = self.store.accounts
        org = self.store.orgs.get(name) if name else accounts.default_org(self.user)
        role = accounts.role(self.user, org) if org is not None else None
        if org is None or role is None:
            raise GraphQLError(
                f"organization {name} not found" if name else "you are not in any organization"
            )
        return org, role

    async def viewer(self, variables: dict) -> dict:
        user = self.user
        default = self.store.accounts.default_org(user)
        orgs = self.store.orgs_by_pk
        return {
            "viewer": {
                "id": encode_id("User", str(user.pk)),
                "entity": default.name if default else None,
                "username": user.email,
                "email": user.email,
                "flags": "{}",
                "admin": user.is_superadmin,
                "teams": {"edges": [{"node": {"name": orgs[pk].name}} for pk in user.roles]},
            }
        }

    async def upsert_bucket(self, variables: dict) -> dict:
        entity = variables.get("entity")
        project = variables.get("project")
        name = variables.get("name")
        if variables.get("id") and not name:
            # A wandb run ID is base64("Run:v1:<name>:<project>:<entity>").
            try:
                kind, _, name, project, entity = (
                    base64.b64decode(variables["id"]).decode().split(":", 4)
                )
            except ValueError:
                kind = None
            if kind != "Run":
                raise GraphQLError(f"invalid run id {variables['id']!r}")
        org, role = self.org(entity)
        entity = org.name
        fields: dict[str, Any] = {
            "display_name": variables.get("displayName"),
            "notes": variables.get("notes"),
            "description": variables.get("description"),
            "group": variables.get("groupName"),
            "job_type": variables.get("jobType"),
            "host": variables.get("host"),
            "program": variables.get("program"),
            "commit": variables.get("commit"),
            "sweep": variables.get("sweep"),
            "state": variables.get("state"),
            "tags": variables.get("tags"),
        }
        for var, key in (("config", "config"), ("summaryMetrics", "summary")):
            raw = variables.get(var)
            if raw is None:
                continue
            try:
                value = loads_lenient(raw) if isinstance(raw, (str, bytes)) else raw
            except ValueError as exc:
                raise GraphQLError(f"{var} is not valid JSON") from exc
            if not isinstance(value, dict):
                raise GraphQLError(f"{var} must be a JSON object")
            fields[key] = value
        try:
            run, inserted = await self.store.upsert_run(
                entity, project, name, fields, owner=self.user.pk, admin=role == "admin"
            )
        except (NotFound, Forbidden) as exc:  # the org went away meanwhile, or not their run
            raise GraphQLError(str(exc)) from exc
        except Discarded as exc:
            # The run was deleted while this client was logging: answer as if it existed, so
            # the client finishes normally (its writes are dropped).
            project, name = exc.project, exc.name
            return {
                "upsertBucket": {
                    "bucket": {
                        "id": encode_id("Run", "v1", name, project, entity),
                        "name": name,
                        "displayName": fields["display_name"],
                        "description": fields["description"],
                        "config": orjson.dumps(fields.get("config") or {}).decode(),
                        "sweepName": fields["sweep"],
                        "historyLineCount": 0,
                        "project": {
                            "id": encode_id("Project", "v1", project, entity),
                            "name": project,
                            "entity": {"id": encode_id("Entity", entity), "name": entity},
                        },
                    },
                    "inserted": False,
                }
            }
        config = fields.get("config")
        if config is None:
            config = await self.store.read_config(run)
        return {
            "upsertBucket": {
                "bucket": {
                    "id": run.graphql_id,
                    "name": run.name,
                    "displayName": run.display_name,
                    "description": run.description,
                    "config": orjson.dumps(config).decode(),
                    "sweepName": run.sweep,
                    "historyLineCount": run.history_line_count,
                    "project": {
                        "id": encode_id("Project", "v1", run.project.name, run.entity),
                        "name": run.project.name,
                        "entity": {"id": encode_id("Entity", run.entity), "name": run.entity},
                    },
                },
                "inserted": inserted,
            }
        }

    async def run_resume_status(self, variables: dict) -> dict:
        store = self.store
        entity = self.org(variables.get("entity"))[0].name
        project_name = variables.get("project") or store.settings.default_project
        project = store.project(entity, project_name)
        if project is None:
            return {"model": None}
        run = project.by_name.get(variables.get("name") or "")
        bucket = None
        if run is not None:
            config = await store.read_config(run)
            wandb_config = config.get("_wandb", {})
            if isinstance(wandb_config, dict) and "value" in wandb_config:
                wandb_config = wandb_config["value"]
            telemetry = wandb_config.get("t") if isinstance(wandb_config, dict) else None
            bucket = {
                "id": run.graphql_id,
                "name": run.name,
                "displayName": run.display_name,
                "summaryMetrics": orjson.dumps(await store.read_summary(run)).decode(),
                "logLineCount": run.log_line_count,
                "historyLineCount": run.history_line_count,
                "eventsLineCount": run.events_line_count,
                "historyTail": orjson.dumps(await store.tail_lines("history", run, 1)).decode(),
                "eventsTail": orjson.dumps(await store.tail_lines("events", run, 1)).decode(),
                "config": orjson.dumps(config).decode(),
                "tags": run.tags,
                "notes": run.notes,
                "wandbConfig": orjson.dumps({"t": telemetry} if telemetry else {}).decode(),
            }
        return {
            "model": {
                "id": encode_id("Project", "v1", project_name, entity),
                "name": project_name,
                "entity": {"id": encode_id("Entity", entity), "name": entity},
                "bucket": bucket,
            }
        }

    async def create_run_files(self, variables: dict) -> dict:
        store = self.store
        org, role = self.org(variables.get("entity"))
        entity = org.name
        project = variables.get("project") or store.settings.default_project
        run_name = variables.get("run") or ""
        run = store.get_run(entity, project, run_name)
        if run is not None and role != "admin" and run.owner != self.user.pk:
            raise GraphQLError(f"run {run.path} belongs to another user")
        # The client of a run deleted while it was running gets URLs whose uploads are dropped.
        discarded = run is None and store.discarding(entity, project, run_name)
        if run is None and not discarded:
            raise GraphQLError("run not found")
        base = (store.settings.public_url or str(self.request.base_url)).rstrip("/")
        prefix = "/".join(quote(part, safe="") for part in (entity, project, run_name))
        files = []
        for name in variables.get("files") or []:
            if run is not None and store.file_path(run, name) is None:
                raise GraphQLError(f"invalid file name {name!r}")
            signature = self.sign(store, entity, project, run_name, name)
            files.append(
                {
                    "name": name,
                    "uploadUrl": f"{base}/storage/{prefix}/{quote(name)}?sig={signature}",
                }
            )
        run_id = encode_id("Run", "v1", run_name, project, entity)
        return {"createRunFiles": {"runID": run_id, "uploadHeaders": [], "files": files}}

    async def run_state(self, variables: dict) -> dict:
        run = self.store.get_run(
            self.org(variables.get("entity"))[0].name,
            variables.get("project") or self.store.settings.default_project,
            variables.get("run") or "",
        )
        if run is None:
            return {"project": None}
        return {"project": {"run": {"id": run.graphql_id, "state": run.state}}}


# ---------------------------------------------------------------------- endpoints


@router.post("/graphql")
async def graphql(request: Request, store: StoreDep, user: UserDep) -> Response:
    body = await request.body()
    try:
        if request.headers.get("content-encoding", "").lower() == "gzip":  # the SDK may gzip
            body = gzip.decompress(body)
        payload = orjson.loads(body)
    except (orjson.JSONDecodeError, OSError) as exc:  # OSError: corrupt gzip
        raise APIError(400, "invalid request body") from exc
    graphql = GraphQL(store, user, request)
    if isinstance(payload, list):
        return JSONResponse([await graphql.execute(p) for p in payload])
    return JSONResponse(await graphql.execute(payload))


@router.post("/files/{entity}/{project}/{run}/file_stream")
async def file_stream(
    entity: str, project: str, run: str, request: Request, store: StoreDep, user: UserDep
) -> Response:
    body = await request.body()
    try:
        if request.headers.get("content-encoding", "").lower() == "gzip":  # the SDK gzips these
            body = gzip.decompress(body)
        payload = orjson.loads(body)
    except (orjson.JSONDecodeError, OSError) as exc:  # OSError: corrupt gzip
        raise APIError(400, "invalid request body") from exc
    org = visible_org(entity, store, user)
    admin = store.accounts.role(user, org) == "admin"
    try:
        await store.file_stream(entity, project, run, payload, owner=user.pk, admin=admin)
    except NotFound as exc:
        raise APIError(404, str(exc)) from exc
    return JSONResponse({"exitcode": None, "limits": {}})


@router.put("/storage/{entity}/{project}/{run}/{name:path}")
async def upload(
    entity: str,
    project: str,
    run: str,
    name: str,
    request: Request,
    store: StoreDep,
    sig: str = Query(""),
) -> Response:
    if not hmac.compare_digest(sig, GraphQL.sign(store, entity, project, run, name)):
        raise APIError(403, "invalid upload signature")
    target = store.get_run(entity, project, run)
    try:
        if target is None:
            raise NotFound("run not found")
        await store.save_file(target, name, await request.body())
    except NotFound as exc:
        if store.discarding(entity, project, run):
            return Response(status_code=200)  # from the client of a deleted run: dropped
        raise APIError(404, str(exc)) from exc
    except ValueError as exc:
        raise APIError(400, str(exc)) from exc
    return Response(status_code=200)


@router.post("/sdk/otel/{path:path}")
async def discard_telemetry(path: str, request: Request) -> Response:
    """wandb-core exports its own OpenTelemetry metrics/logs to the server; drop them."""
    await request.body()
    return Response(status_code=200, media_type="application/x-protobuf")
