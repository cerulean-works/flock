"""Expand and contract migrations (020): expand files on startup, contract files once every
running server knows them and the contract delay has passed, and the runner's lock timeout."""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import TYPE_CHECKING

import asyncpg
import pytest

from app.api.src.core.store import Store
from app.api.src.db import migrate
from app.api.src.db.database import Database, SchemaError
from app.api.src.db.instances import Instance
from app.api.src.db.migrate import Migrations
from app.api.src.s3_proxy.app import Proxy
from app.api.src.settings import Settings

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path
    from typing import Any

INSTANCES = Migrations().directory / "0020_instances.sql"
# Two releases of a toy schema: A adds `things.new` next to `things.old`, and B drops `old`.
RELEASE_A = {
    "0002_things.sql": "CREATE TABLE things (old text, new text);",
}
RELEASE_B = {
    **RELEASE_A,
    "0003_drop_old.contract.sql": "ALTER TABLE things DROP COLUMN old;",
    "0004_more.sql": "CREATE TABLE more_things (id int);",
}


def release(tmp_path: Path, name: str, files: dict[str, str], **options: Any) -> Migrations:
    """A release's migrations: ``flock_instances`` as 0001, then ``files``."""
    directory = tmp_path / name
    directory.mkdir()
    shutil.copy(INSTANCES, directory / "0001_instances.sql")
    for file, sql in files.items():
        (directory / file).write_text(sql)
    return Migrations(directory, **options)


def run(url: str, scenario: Callable[[asyncpg.Pool], Awaitable[Any]]) -> Any:
    async def main() -> Any:
        pool = await asyncpg.create_pool(url, min_size=1, max_size=4)
        try:
            return await scenario(pool)
        finally:
            await pool.close()

    return asyncio.run(main())


async def versions(pool: asyncpg.Pool) -> list[str]:
    return [r[0] for r in await pool.fetch("SELECT version FROM schema_migrations ORDER BY 1")]


async def columns(pool: asyncpg.Pool, table: str) -> list[str]:
    rows = await pool.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name = $1 ORDER BY 1",
        table,
    )
    return [r[0] for r in rows]


def test_startup_applies_expand_files_and_contracts_wait(postgres: Any, tmp_path: Path) -> None:
    url = postgres.create(migrated=False)
    b = release(tmp_path, "b", RELEASE_B)
    assert b.last == 4
    assert [m.contract for m in b.migrations()] == [False, False, True, False]
    assert asyncio.run(b.apply(url)) == ["0001_instances", "0002_things", "0004_more"]

    async def scenario(pool: asyncpg.Pool) -> None:
        async with pool.acquire() as conn:
            assert await b.pending(conn) == []  # a contract isn't pending, it waits
        assert await columns(pool, "things") == ["new", "old"]
        # Nothing else runs, but the contract delay hasn't passed.
        assert await b.apply_contracts(url, delay=1800) == []
        assert await b.apply_contracts(url, delay=1800, force=True) == ["0003_drop_old.contract"]
        assert await columns(pool, "things") == ["new"]
        assert "0003_drop_old.contract" in await versions(pool)
        assert await b.apply_contracts(url, delay=0) == []

    run(url, scenario)


def test_contract_waits_for_old_instances_then_the_delay(postgres: Any, tmp_path: Path) -> None:
    url = postgres.create(migrated=False)
    a = release(tmp_path, "a", RELEASE_A)
    b = release(tmp_path, "b", RELEASE_B)
    asyncio.run(b.apply(url))

    async def backdate(pool: asyncpg.Pool) -> None:
        await pool.execute("UPDATE flock_contract_waits SET since = since - interval '1 hour'")

    async def scenario(pool: asyncpg.Pool) -> None:
        old, new = Instance(pool, "api", a), Instance(pool, "s3", b)
        await old.start()
        await new.start()
        rows = await pool.fetch("SELECT service, knows FROM flock_instances ORDER BY knows")
        assert [tuple(r) for r in rows] == [("api", "0002"), ("s3", "0004")]

        # The old release runs: however long ago, the contract waits.
        assert await b.apply_contracts(url, delay=1800) == []
        assert await pool.fetchval("SELECT count(*) FROM flock_contract_waits") == 0

        # It's gone: the delay starts now.
        await old.close()
        assert await b.apply_contracts(url, delay=1800) == []
        assert await pool.fetchval("SELECT count(*) FROM flock_contract_waits") == 1

        # An old one comes and goes between two checks (a rollback): the delay starts again.
        await backdate(pool)
        again = Instance(pool, "api", a)
        await again.start()
        await again.close()
        assert await b.apply_contracts(url, delay=1800) == []

        await backdate(pool)
        assert await b.apply_contracts(url, delay=1800) == ["0003_drop_old.contract"]
        assert await columns(pool, "things") == ["new"]
        assert await pool.fetchval("SELECT count(*) FROM flock_contract_waits") == 0
        await new.close()

    run(url, scenario)


def test_an_instance_unseen_for_a_minute_counts_as_gone(postgres: Any, tmp_path: Path) -> None:
    url = postgres.create(migrated=False)
    a = release(tmp_path, "a", RELEASE_A)
    b = release(tmp_path, "b", RELEASE_B)
    asyncio.run(b.apply(url))

    async def scenario(pool: asyncpg.Pool) -> None:
        killed = Instance(pool, "api", a)
        await killed.register()  # and never closed
        assert await b.apply_contracts(url, delay=0) == []
        await pool.execute("UPDATE flock_instances SET seen_at = now() - interval '61 seconds'")
        assert await b.apply_contracts(url, delay=0) == ["0003_drop_old.contract"]
        assert await pool.fetchval("SELECT count(*) FROM flock_instances") == 0

    run(url, scenario)


def test_instances_heartbeat_and_leave(postgres: Any, tmp_path: Path) -> None:
    url = postgres.create(migrated=False)
    a = release(tmp_path, "a", RELEASE_A)
    asyncio.run(a.apply(url))

    async def scenario(pool: asyncpg.Pool) -> None:
        instance = Instance(pool, "api", a, heartbeat=0.05)
        await instance.start()
        row = await pool.fetchrow("SELECT * FROM flock_instances")
        assert (row["id"], row["service"], row["knows"]) == (instance.id, "api", "0002")
        await asyncio.sleep(0.3)
        seen = await pool.fetchval("SELECT seen_at FROM flock_instances")
        assert seen > row["seen_at"]

        # Pruned as gone after missing its heartbeats (paused): it comes back.
        await pool.execute("DELETE FROM flock_instances")
        await instance.beat()
        assert await pool.fetchval("SELECT id FROM flock_instances") == instance.id

        await instance.close()
        assert await pool.fetchval("SELECT count(*) FROM flock_instances") == 0

    run(url, scenario)


def test_migrate_contract_command(
    postgres: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = postgres.create(migrated=False)
    a = release(tmp_path, "a", RELEASE_A)
    b = release(tmp_path, "b", RELEASE_B)
    asyncio.run(b.apply(url))
    monkeypatch.setenv("FLOCK_DATABASE_URL", url)
    monkeypatch.setenv("FLOCK_CONTRACT_DELAY", "0")

    async def scenario(pool: asyncpg.Pool) -> None:
        old = Instance(pool, "api", a)
        await old.start()
        await migrate.main(["--contract"], migrations=b)
        assert "0003_drop_old.contract" not in await versions(pool)
        await migrate.main(["--contract", "--force"], migrations=b)
        assert "0003_drop_old.contract" in await versions(pool)
        await old.close()

    run(url, scenario)
    with pytest.raises(SystemExit):
        asyncio.run(migrate.main(["--force"], migrations=b))


def test_old_release_refuses_a_database_contracted_past_it(postgres: Any, tmp_path: Path) -> None:
    url = postgres.create(migrated=False)
    a = release(tmp_path, "a", RELEASE_A)
    b = release(tmp_path, "b", RELEASE_B)
    asyncio.run(b.apply(url))

    # Expand files it doesn't know don't stop it.
    asyncio.run(Database(url, migrations=a).open())

    asyncio.run(b.apply_contracts(url, delay=0, force=True))
    message = (
        "database has contract migrations this release doesn't know (0003_drop_old): "
        "deploy a release from 0003 or later"
    )
    with pytest.raises(SchemaError) as refused:
        asyncio.run(Database(url, migrations=a).open())
    assert str(refused.value) == message
    with pytest.raises(SchemaError):
        asyncio.run(a.apply(url))
    with pytest.raises(SchemaError):
        run(url, lambda pool: Instance(pool, "s3", a).start())
    asyncio.run(Database(url, migrations=b).open())


def test_server_refuses_a_contract_newer_than_its_release(postgres: Any, tmp_path: Path) -> None:
    url = postgres.create()
    last = Migrations().last

    async def record(version: str) -> None:
        conn = await asyncpg.connect(url)
        try:
            await conn.execute("INSERT INTO schema_migrations (version) VALUES ($1)", version)
        finally:
            await conn.close()

    async def start() -> None:
        store = Store(Settings(database_url=url, files_dir=tmp_path))
        await store.open()
        await store.close()

    asyncio.run(record(f"{last + 1:04d}_newer_expand"))
    asyncio.run(start())
    asyncio.run(record(f"{last + 2:04d}_newer.contract"))
    with pytest.raises(SchemaError, match=f"deploy a release from {last + 2:04d} or later"):
        asyncio.run(start())


def test_the_api_registers_and_applies_due_contracts(
    postgres: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = postgres.create()
    directory = tmp_path / "release"
    shutil.copytree(Migrations().directory, directory)
    n = Migrations().last + 1
    (directory / f"{n:04d}_scratch.sql").write_text("CREATE TABLE scratch (old text, new text);")
    (directory / f"{n + 1:04d}_scratch_old.contract.sql").write_text(
        "ALTER TABLE scratch DROP COLUMN old;"
    )
    monkeypatch.setattr(Store, "CONTRACT_INTERVAL", 0.05)
    settings = Settings(database_url=url, files_dir=tmp_path / "files", contract_delay=0)

    async def scenario(pool: asyncpg.Pool) -> None:
        store = Store(settings, Migrations(directory))
        await store.open()
        try:
            row = await pool.fetchrow("SELECT service, knows FROM flock_instances")
            assert tuple(row) == ("api", f"{n + 1:04d}")
            for _ in range(200):
                if f"{n + 1:04d}_scratch_old.contract" in await versions(pool):
                    break
                await asyncio.sleep(0.05)
            assert await columns(pool, "scratch") == ["new"]
        finally:
            await store.close()
        assert await pool.fetchval("SELECT count(*) FROM flock_instances") == 0

    run(url, scenario)


def test_the_s3_proxy_registers(postgres: Any, flock: Any) -> None:
    settings = Settings(
        database_url=flock.database_url,
        s3_upstream_endpoint="http://127.0.0.1:9",
        s3_upstream_access_key_id="key",
        s3_upstream_secret_access_key="secret",
    )

    async def scenario(pool: asyncpg.Pool) -> None:
        proxy = Proxy(settings)
        await proxy.start()
        try:
            row = await pool.fetchrow(
                "SELECT service, knows FROM flock_instances WHERE service = 's3'"
            )
            assert tuple(row) == ("s3", f"{Migrations().last:04d}")
        finally:
            await proxy.close()
        assert await pool.fetchval("SELECT count(*) FROM flock_instances WHERE service = 's3'") == 0

    run(flock.database_url, scenario)


# ------------------------------------------------------------------ the runner (D149)

TABLE = {"0002_t.sql": "CREATE TABLE t (a int);"}


async def hold(url: str, release: asyncio.Event) -> None:
    """Hold a lock on ``t`` that any ``ALTER TABLE t`` waits for, until ``release``."""
    conn = await asyncpg.connect(url)
    try:
        async with conn.transaction():
            await conn.execute("LOCK TABLE t IN ACCESS SHARE MODE")
            await release.wait()
    finally:
        await conn.close()


def test_a_migration_waiting_for_a_lock_is_tried_again(
    postgres: Any, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    url = postgres.create(migrated=False)
    asyncio.run(release(tmp_path, "a", TABLE).apply(url))
    files = {**TABLE, "0003_alter.sql": "ALTER TABLE t ADD COLUMN b int;"}
    b = release(tmp_path, "b", files, lock_timeout=0.05, retry_delay=0.05, attempts=100)

    async def scenario(pool: asyncpg.Pool) -> None:
        released = asyncio.Event()
        holder = asyncio.create_task(hold(url, released))
        await asyncio.sleep(0.1)
        applying = asyncio.create_task(b.apply(url))
        await asyncio.sleep(0.5)
        assert not applying.done()
        released.set()
        await holder
        assert await applying == ["0003_alter"]
        assert await columns(pool, "t") == ["a", "b"]

    with caplog.at_level(logging.WARNING, logger="app.api.src.db.migrate"):
        run(url, scenario)
    assert "timed out waiting for a lock" in caplog.text


def test_a_migration_gives_up_after_its_attempts(postgres: Any, tmp_path: Path) -> None:
    url = postgres.create(migrated=False)
    asyncio.run(release(tmp_path, "a", TABLE).apply(url))
    files = {**TABLE, "0003_alter.sql": "ALTER TABLE t ADD COLUMN b int;"}
    b = release(tmp_path, "b", files, lock_timeout=0.05, retry_delay=0.01, attempts=3)

    async def scenario(pool: asyncpg.Pool) -> None:
        released = asyncio.Event()
        holder = asyncio.create_task(hold(url, released))
        await asyncio.sleep(0.1)
        try:
            with pytest.raises(asyncpg.LockNotAvailableError):
                await b.apply(url)
        finally:
            released.set()
            await holder
        assert "0003_alter" not in await versions(pool)
        assert await columns(pool, "t") == ["a"]

    run(url, scenario)


def test_a_no_transaction_migration(postgres: Any, tmp_path: Path) -> None:
    url = postgres.create(migrated=False)
    files = {
        **TABLE,
        "0003_index.sql": "-- flock: no-transaction\n"
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS t_a ON t (a);\n",
    }
    migrations = release(tmp_path, "a", files)
    assert [m.transaction for m in migrations.migrations()] == [True, True, False]
    assert asyncio.run(migrations.apply(url)) == ["0001_instances", "0002_t", "0003_index"]

    async def scenario(pool: asyncpg.Pool) -> None:
        assert await pool.fetchval("SELECT to_regclass('t_a') IS NOT NULL")
        assert "0003_index" in await versions(pool)

    run(url, scenario)
