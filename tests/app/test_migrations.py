import asyncio
import json
import re
import shutil

import asyncpg
import pytest

from app.api.src.core.history import Series
from app.api.src.core.store import Store
from app.api.src.db.database import SchemaError
from app.api.src.db.migrate import Migrations
from app.api.src.settings import Settings


def test_migration_files_are_numbered_in_sequence():
    names = [path.name for path in Migrations().files()]
    assert names, "no migrations found"
    assert all(re.fullmatch(r"\d{4}_[a-z0-9_]+\.sql", name) for name in names), names
    numbers = [int(name[:4]) for name in names]
    assert numbers == list(range(1, len(names) + 1))


def test_migrate_applies_everything_once(postgres):
    url = postgres.create(migrated=False)
    migrations = Migrations()
    expected = [path.stem for path in migrations.files()]
    assert asyncio.run(migrations.apply(url)) == expected
    assert asyncio.run(migrations.apply(url)) == []

    async def recorded() -> list[str]:
        conn = await asyncpg.connect(url)
        try:
            rows = await conn.fetch("SELECT version FROM schema_migrations ORDER BY 1")
            return [r[0] for r in rows]
        finally:
            await conn.close()

    assert asyncio.run(recorded()) == expected


def test_server_migrates_on_startup(postgres, make_flock):
    url = postgres.create(migrated=False)
    api = make_flock(database_url=url).client()  # the default: migrate_on_startup=True
    api.create_run("p", "r1")
    assert api.only_run("p")["state"] == "running"


def test_server_refuses_unmigrated_database_when_told_not_to_migrate(postgres, tmp_path):
    settings = Settings(
        database_url=postgres.create(migrated=False), files_dir=tmp_path, migrate_on_startup=False
    )
    with pytest.raises(SchemaError, match="FLOCK_MIGRATE_ON_STARTUP"):
        asyncio.run(Store(settings).open())


@pytest.mark.parametrize(("raw", "expected"), [("false", False), ("0", False), ("TRUE", True)])
def test_boolean_settings_parse_words(raw, expected):
    environ = {"FLOCK_MIGRATE_ON_STARTUP": raw}
    assert Settings.load(environ=environ).migrate_on_startup is expected


def test_only_one_server_per_database(flock, tmp_path):
    second = Store(Settings(database_url=flock.database_url, files_dir=tmp_path))
    with pytest.raises(SchemaError, match="already using this database"):
        asyncio.run(second.open())


def test_0002_upgrades_existing_data(postgres, make_flock, tmp_path):
    """A database written before system metrics existed keeps its history and gains them."""
    url = postgres.create(migrated=False)
    before = tmp_path / "before"
    before.mkdir()
    shutil.copy(Migrations().directory / "0001_initial.sql", before)
    assert asyncio.run(Migrations(before).apply(url)) == ["0001_initial"]

    # What the previous server wrote: a finished run, its history compacted, its system
    # samples only as raw lines.
    loss = Series()
    loss.extend([0, 1], [1.0, 0.5])
    step = Series()
    step.extend([0, 1], [0.0, 1.0])
    samples = [{"_runtime": 1.0 + i, "system.cpu": 10.0 * i} for i in range(3)]

    async def seed() -> None:
        conn = await asyncpg.connect(url)
        try:
            pk = await conn.fetchval(
                "INSERT INTO runs (entity, project, name, state, created_at, updated_at, "
                "heartbeat_at, history_line_count, events_line_count, compacted_line_count) "
                "VALUES ('flock', 'p', 'r1', 'finished', now(), now(), now(), 2, 3, 2) "
                "RETURNING pk"
            )
            await conn.executemany(
                "INSERT INTO history (run_pk, line, data) VALUES ($1, $2, $3)",
                [(pk, i, json.dumps({"_step": i, "loss": [1.0, 0.5][i]})) for i in range(2)],
            )
            await conn.executemany(
                "INSERT INTO history_columns (run_pk, key, n, data) VALUES ($1, $2, 2, $3)",
                [(pk, "loss", loss.encode()), (pk, "_step", step.encode())],
            )
            await conn.executemany(
                "INSERT INTO events (run_pk, line, data) VALUES ($1, $2, $3)",
                [(pk, i, json.dumps(sample)) for i, sample in enumerate(samples)],
            )
        finally:
            await conn.close()

    asyncio.run(seed())
    assert asyncio.run(Migrations().apply(url)) == [
        "0002_system_metrics",
        "0003_projects",
        "0004_orgs_and_users",
        "0005_gpus_and_budgets",
        "0006_avatars",
        "0007_org_imports",
        "0008_org_display_names",
        "0009_budget_management",
        "0010_cli_sessions",
        "0011_s3",
        "0012_reserve_storage",
    ]

    server = make_flock(database_url=url)  # rebuilds and compacts system metrics on startup
    run = server.store.get_run("flock", "p", "r1")
    assert run.compacted == {"history": 2, "system": 3}
    api = server.client()
    assert api.run_history("p", "r1", keys="loss")["loss"] == {"x": [0, 1], "y": [1.0, 0.5]}
    system = api.run_history("p", "r1", stream="system", keys="system.cpu")["system.cpu"]
    assert system == {"x": [1.0, 2.0, 3.0], "y": [0.0, 10.0, 20.0]}

    async def streams() -> list[tuple[str, str]]:
        conn = await asyncpg.connect(url)
        try:
            rows = await conn.fetch("SELECT stream, key FROM history_columns")
            return sorted((r[0], r[1]) for r in rows)  # Python order; collations differ
        finally:
            await conn.close()

    assert asyncio.run(streams()) == [
        ("history", "_step"),
        ("history", "loss"),
        ("system", "_runtime"),
        ("system", "system.cpu"),
    ]


def test_0003_creates_projects_from_runs(postgres, make_flock, tmp_path):
    """Runs written before projects were records get a project each, from their names."""
    url = postgres.create(migrated=False)
    before = tmp_path / "before"
    before.mkdir()
    for name in ("0001_initial.sql", "0002_system_metrics.sql"):
        shutil.copy(Migrations().directory / name, before)
    asyncio.run(Migrations(before).apply(url))

    async def seed() -> None:
        conn = await asyncpg.connect(url)
        try:
            await conn.executemany(
                "INSERT INTO runs (entity, project, name, state, created_at, updated_at, "
                "heartbeat_at) VALUES ($1, $2, $3, 'finished', "
                "to_timestamp($4), to_timestamp($4), to_timestamp($4))",
                [
                    ("flock", "p", "r1", 100.0),
                    ("flock", "p", "r2", 50.0),
                    ("flock", "q", "r1", 75.0),
                    ("lab", "p", "r1", 10.0),
                ],
            )
        finally:
            await conn.close()

    asyncio.run(seed())
    shutil.copy(Migrations().directory / "0003_projects.sql", before)
    assert asyncio.run(Migrations(before).apply(url)) == ["0003_projects"]

    async def schema() -> tuple[list, list]:
        conn = await asyncpg.connect(url)
        try:
            projects = await conn.fetch(
                "SELECT entity, name, extract(epoch FROM created_at)::float8 FROM projects"
            )
            constraints = await conn.fetch(
                "SELECT conname FROM pg_constraint WHERE conrelid = 'runs'::regclass "
                "AND contype = 'u'"
            )
            return sorted(tuple(p) for p in projects), [c[0] for c in constraints]
        finally:
            await conn.close()

    projects, unique = asyncio.run(schema())
    assert projects == [("flock", "p", 50.0), ("flock", "q", 75.0), ("lab", "p", 10.0)]
    assert unique == ["runs_project_pk_name_key"]
    assert asyncio.run(Migrations().apply(url)) == [
        "0004_orgs_and_users",
        "0005_gpus_and_budgets",
        "0006_avatars",
        "0007_org_imports",
        "0008_org_display_names",
        "0009_budget_management",
        "0010_cli_sessions",
        "0011_s3",
        "0012_reserve_storage",
    ]

    api = make_flock(database_url=url).client()
    assert api.runs("p")["ids"] == ["r1", "r2"]  # newest first
    assert api.runs("p", entity="lab")["ids"] == ["r1"]
    assert api.get("/api/v1/projects/flock/q").json()["run_count"] == 1


def test_0004_creates_orgs_from_entities(postgres, make_flock, tmp_path):
    """Entities of existing projects become orgs; the first account then administers them all."""
    url = postgres.create(migrated=False)
    before = tmp_path / "before"
    before.mkdir()
    for path in Migrations().files()[:3]:
        shutil.copy(path, before)
    asyncio.run(Migrations(before).apply(url))

    async def seed() -> None:
        conn = await asyncpg.connect(url)
        try:
            for entity, project, run in (("flock", "p", "r1"), ("lab", "p", "r1")):
                pk = await conn.fetchval(
                    "INSERT INTO projects (entity, name, created_at, updated_at) "
                    "VALUES ($1, $2, now(), now()) RETURNING pk",
                    entity,
                    project,
                )
                await conn.execute(
                    "INSERT INTO runs (project_pk, name, state, created_at, updated_at, "
                    "heartbeat_at) VALUES ($1, $2, 'finished', now(), now(), now())",
                    pk,
                    run,
                )
        finally:
            await conn.close()

    asyncio.run(seed())
    assert asyncio.run(Migrations().apply(url)) == [
        "0004_orgs_and_users",
        "0005_gpus_and_budgets",
        "0006_avatars",
        "0007_org_imports",
        "0008_org_display_names",
        "0009_budget_management",
        "0010_cli_sessions",
        "0011_s3",
        "0012_reserve_storage",
    ]

    async def rows() -> list:
        conn = await asyncpg.connect(url)
        try:
            return await conn.fetch(
                "SELECT o.name, p.name, r.owner_pk FROM runs r "
                "JOIN projects p ON p.pk = r.project_pk JOIN orgs o ON o.pk = p.org_pk"
            )
        finally:
            await conn.close()

    assert sorted(tuple(r) for r in asyncio.run(rows())) == [
        ("flock", "p", None),
        ("lab", "p", None),
    ]
    api = make_flock(database_url=url).client()
    assert api.get("/api/v1/projects/lab/p/runs/r1").json()["owner"] is None
    orgs = api.get("/api/v1/orgs").json()["orgs"]
    assert [(o["name"], o["role"], o["member_count"]) for o in orgs] == [
        ("flock", "admin", 1),
        ("lab", "admin", 1),
    ]


def test_0005_fills_the_usage_ledger_and_reads_uploaded_gpus(postgres, make_flock, tmp_path):
    """Runs logged before 0005 are billed from creation to their last heartbeat, split by
    month, and the GPUs in their already uploaded metadata are read on startup."""
    url = postgres.create(migrated=False)
    before = tmp_path / "before"
    before.mkdir()
    for path in Migrations().files()[:4]:
        shutil.copy(path, before)
    asyncio.run(Migrations(before).apply(url))

    async def seed() -> int:
        conn = await asyncpg.connect(url)
        try:
            org = await conn.fetchval(
                "INSERT INTO orgs (name, created_at, updated_at) VALUES ('flock', now(), now()) "
                "RETURNING pk"
            )
            project = await conn.fetchval(
                "INSERT INTO projects (org_pk, name, created_at, updated_at) "
                "VALUES ($1, 'p', now(), now()) RETURNING pk",
                org,
            )
            # Alive from 23:00 on September 30th to 01:00 on October 1st (UTC).
            run = await conn.fetchval(
                "INSERT INTO runs (project_pk, name, state, created_at, updated_at, heartbeat_at) "
                "VALUES ($1, 'r1', 'finished', '2026-09-30 23:00+00', now(), "
                "'2026-10-01 01:00+00') RETURNING pk",
                project,
            )
            await conn.execute(
                "INSERT INTO run_files (run_pk, name, size, updated_at) "
                "VALUES ($1, 'wandb-metadata.json', 10, now())",
                run,
            )
            return run
        finally:
            await conn.close()

    run_pk = asyncio.run(seed())
    assert asyncio.run(Migrations().apply(url)) == [
        "0005_gpus_and_budgets",
        "0006_avatars",
        "0007_org_imports",
        "0008_org_display_names",
        "0009_budget_management",
        "0010_cli_sessions",
        "0011_s3",
        "0012_reserve_storage",
    ]
    # Flock's first files directory (see ``Flocks``) already holds the upload.
    metadata = tmp_path / "flock-files-0" / str(run_pk) / "wandb-metadata.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps({"gpu": "NVIDIA H100 80GB HBM3", "gpu_count": 2}))

    api = make_flock(database_url=url).client()
    run = api.get("/api/v1/projects/flock/p/runs/r1").json()
    assert (run["gpu_type"], run["gpu_count"], run["runtime"]) == ("H100", 2, 7200.0)
    api.patch("/api/v1/orgs/flock/billing", {"rates": {"H100": 10}})
    for month in ("2026-09", "2026-10"):
        budget = api.get("/api/v1/orgs/flock/budget", month=month).json()
        assert (budget["gpu_hours"], budget["spent"]) == (2.0, 20.0)  # an hour, 2 GPUs, each


def test_0008_shows_existing_orgs_by_their_name(postgres, make_flock, tmp_path):
    """Organizations from before display names are shown as they were named, and keep that
    name as their ID."""
    url = postgres.create(migrated=False)
    before = tmp_path / "before"
    before.mkdir()
    for path in Migrations().files()[:7]:
        shutil.copy(path, before)
    asyncio.run(Migrations(before).apply(url))

    async def seed() -> None:
        conn = await asyncpg.connect(url)
        try:
            await conn.executemany(
                "INSERT INTO orgs (name, created_at, updated_at) VALUES ($1, now(), now())",
                [("flock",), ("Cerulean Labs",)],
            )
        finally:
            await conn.close()

    asyncio.run(seed())
    assert asyncio.run(Migrations().apply(url)) == [
        "0008_org_display_names",
        "0009_budget_management",
        "0010_cli_sessions",
        "0011_s3",
        "0012_reserve_storage",
    ]

    async def nullable() -> str:
        conn = await asyncpg.connect(url)
        try:
            return await conn.fetchval(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'orgs' AND column_name = 'display_name'"
            )
        finally:
            await conn.close()

    assert asyncio.run(nullable()) == "NO"
    api = make_flock(database_url=url).client()
    orgs = api.get("/api/v1/orgs").json()["orgs"]
    assert [(o["name"], o["display_name"]) for o in orgs] == [
        ("Cerulean Labs", "Cerulean Labs"),
        ("flock", "flock"),
    ]
    renamed = api.patch("/api/v1/orgs/Cerulean%20Labs", {"display_name": "Cerulean"}).json()
    assert (renamed["name"], renamed["display_name"]) == ("Cerulean Labs", "Cerulean")


def test_0009_keeps_the_spend_of_projects_deleted_before_it(postgres, make_flock, tmp_path):
    """Spend of projects deleted before 0009 is one deleted project per org, without a name,
    which admins can leave out of the budget like any other."""
    url = postgres.create(migrated=False)
    before = tmp_path / "before"
    before.mkdir()
    for path in Migrations().files()[:8]:
        shutil.copy(path, before)
    asyncio.run(Migrations(before).apply(url))

    async def seed() -> None:
        conn = await asyncpg.connect(url)
        try:
            org = await conn.fetchval(
                "INSERT INTO orgs (name, display_name, created_at, updated_at) "
                "VALUES ('flock', 'flock', now(), now()) RETURNING pk"
            )
            await conn.execute(
                "INSERT INTO gpu_rates (org_pk, gpu_type, hourly_cost) VALUES ($1, 'H100', 10)",
                org,
            )
            await conn.executemany(
                "INSERT INTO gpu_usage (org_pk, run_name, month, seconds, gpu_type, gpu_count) "
                "VALUES ($1, $2, date_trunc('month', now() AT TIME ZONE 'UTC'), 3600, 'H100', 1)",
                [(org, "r1"), (org, "r2")],
            )
        finally:
            await conn.close()

    asyncio.run(seed())
    assert asyncio.run(Migrations().apply(url)) == [
        "0009_budget_management",
        "0010_cli_sessions",
        "0011_s3",
        "0012_reserve_storage",
    ]
    api = make_flock(database_url=url).client()
    (deleted,) = api.get("/api/v1/orgs/flock/budget/deleted-projects").json()["deleted_projects"]
    assert (deleted["name"], deleted["deleted_at"], deleted["runs"]) == (None, None, 2)
    assert (deleted["cost"], deleted["excluded"]) == (20.0, False)
    assert api.get("/api/v1/orgs/flock/budget").json()["spent"] == 20.0
    path = f"/api/v1/orgs/flock/budget/deleted-projects/{deleted['id']}"
    assert api.patch(path, {"excluded": True}).status == 200
    assert api.get("/api/v1/orgs/flock/budget").json()["spent"] == 0


def test_0012_refuses_an_org_named_storage(postgres, tmp_path):
    """The Storage tab takes `/storage/`: an org of that name stops the migration, with a
    message saying what to do, and the migration runs once it's renamed."""
    url = postgres.create(migrated=False)
    before = tmp_path / "before"
    before.mkdir()
    for path in Migrations().files()[:11]:
        shutil.copy(path, before)
    asyncio.run(Migrations(before).apply(url))

    async def execute(query: str) -> None:
        conn = await asyncpg.connect(url)
        try:
            await conn.execute(query)
        finally:
            await conn.close()

    asyncio.run(
        execute(
            "INSERT INTO orgs (name, display_name, created_at, updated_at) "
            "VALUES ('Storage', 'Storage', now(), now())"
        )
    )
    with pytest.raises(asyncpg.RaiseError, match='named "storage"'):
        asyncio.run(Migrations().apply(url))
    asyncio.run(execute("UPDATE orgs SET name = 'storage-team' WHERE name = 'Storage'"))
    assert asyncio.run(Migrations().apply(url)) == ["0012_reserve_storage"]
