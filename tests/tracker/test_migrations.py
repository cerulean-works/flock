import asyncio
import json
import re
import shutil

import asyncpg
import pytest

from src.core.history import Series
from src.core.store import Store
from src.db.database import SchemaError
from src.db.migrate import Migrations
from src.settings import Settings


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


def test_server_migrates_on_startup(postgres, make_tracker):
    url = postgres.create(migrated=False)
    api = make_tracker(database_url=url).client()  # the default: migrate_on_startup=True
    api.create_run("p", "r1")
    assert api.only_run("p")["state"] == "running"


def test_server_refuses_unmigrated_database_when_told_not_to_migrate(postgres, tmp_path):
    settings = Settings(
        database_url=postgres.create(migrated=False), files_dir=tmp_path, migrate_on_startup=False
    )
    with pytest.raises(SchemaError, match="TRACKER_MIGRATE_ON_STARTUP"):
        asyncio.run(Store(settings).open())


@pytest.mark.parametrize(("raw", "expected"), [("false", False), ("0", False), ("TRUE", True)])
def test_boolean_settings_parse_words(raw, expected):
    environ = {"TRACKER_MIGRATE_ON_STARTUP": raw}
    assert Settings.load(environ=environ).migrate_on_startup is expected


def test_only_one_server_per_database(tracker, tmp_path):
    second = Store(Settings(database_url=tracker.database_url, files_dir=tmp_path))
    with pytest.raises(SchemaError, match="already using this database"):
        asyncio.run(second.open())


def test_0002_upgrades_existing_data(postgres, make_tracker, tmp_path):
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
    assert asyncio.run(Migrations().apply(url)) == ["0002_system_metrics"]

    server = make_tracker(database_url=url)  # rebuilds and compacts system metrics on startup
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
