"""The data half of migration 0013 (D77, D78): what SQL alone can't fill in.

The migration lists every run in ``backfill``. For each, this fills ``run_values``,
``project_keys``, ``run_metrics`` and ``runs.metric_defs`` from the run's row, its config and
its summary (which need Python's lenient JSON for ``NaN``), and rebuilds the series chunks of
streams that weren't compacted from their raw lines. A batch of runs is one transaction that
also deletes their ``backfill`` rows, so an interrupted backfill resumes where it stopped.

``python -m app.api.src.db.migrate`` (the migration Job) runs it after the migrations, and so
does a server that applies migrations on startup; ``app/api/scripts/backfill.py`` runs it alone.
"""

import logging
import time
from collections.abc import Callable
from typing import Any

import asyncpg
import orjson

from app.api.src.core import values
from app.api.src.core.history import History, write_chunks
from app.api.src.core.metric_defs import parse_metric_defs
from app.api.src.utils import loads_lenient, parse_rows

log = logging.getLogger(__name__)

BATCH = 200
# Raw line tables of the series streams.
STREAMS = {"history": "history", "system": "events"}

RUNS = """
    SELECT r.pk, r.project_pk, b.streams, r.name, r.display_name, r.state,
           extract(epoch FROM r.created_at)::float8, extract(epoch FROM r.updated_at)::float8,
           extract(epoch FROM r.heartbeat_at)::float8, r.tags, r.group_name, r.job_type,
           r.notes, r.description, r.host, r.program, r.commit_hash, r.sweep, r.exitcode,
           r.owner_pk, coalesce(r.gpu_type_override, r.gpu_type),
           coalesce(r.gpu_count_override, r.gpu_count),
           (SELECT coalesce(sum(seconds), 0) FROM gpu_usage u WHERE u.run_pk = r.pk),
           r.config, r.summary
    FROM backfill b JOIN runs r ON r.pk = b.run_pk
    WHERE b.run_pk > $1
    ORDER BY b.run_pk
    LIMIT $2
"""


def columns(record: asyncpg.Record) -> dict[str, Any]:
    """A run's columns (attributes, ``config.*`` and ``summary.*``) from a ``RUNS`` record."""
    (
        _, _, _, name, display_name, state, created_at, updated_at, heartbeat_at, tags, group,
        job_type, notes, description, host, program, commit, sweep, exitcode, owner, gpu_type,
        gpu_count, runtime, config, summary,
    ) = record  # fmt: skip
    out: dict[str, Any] = {
        "name": name,
        "display_name": display_name,
        "state": state,
        "created_at": created_at,
        "updated_at": updated_at,
        "heartbeat_at": heartbeat_at,
        "tags": list(tags),
        "group": group,
        "job_type": job_type,
        "notes": notes,
        "description": description,
        "host": host,
        "program": program,
        "commit": commit,
        "sweep": sweep,
        "exitcode": exitcode,
        "owner": owner,
        "gpu_type": gpu_type,
        "gpu_count": gpu_count,
        "gpu_hours": values.gpu_hours(runtime, gpu_count),
    }
    for raw, flat in ((config, values.config_columns), (summary, values.summary_columns)):
        parsed = _parse(raw)
        if parsed:
            out.update(flat(parsed))
    return out


def _parse(raw: str | None) -> dict[str, Any]:
    try:
        parsed = loads_lenient(raw) if raw else None
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def rebuild_chunks(conn: asyncpg.Connection, run_pk: int, stream: str) -> None:
    """Replace a stream's chunks with one per metric, parsed from its raw lines."""
    records = await conn.fetch(
        f"SELECT line, data FROM {STREAMS[stream]} WHERE run_pk = $1 ORDER BY line", run_pk
    )
    hist = History()
    start, batch, expected = None, [], 0
    for line, data in records:  # contiguous runs of lines, so line numbers hold over gaps
        if start is None or line != expected:
            if batch and start is not None:
                hist.append_rows(start, parse_rows(batch))
            start, batch = line, []
        batch.append(data)
        expected = line + 1
    if batch and start is not None:
        hist.append_rows(start, parse_rows(batch))
    await write_chunks(conn, run_pk, stream, 0, hist.series, replace=True)


async def pending(conn: asyncpg.Connection) -> bool:
    """Whether runs still wait for the backfill."""
    if not await conn.fetchval("SELECT to_regclass('backfill') IS NOT NULL"):
        return False
    return await conn.fetchval("SELECT EXISTS (SELECT FROM backfill)")


async def backfill(dsn: str, report: Callable[[str], None] = log.info, batch: int = BATCH) -> int:
    """Backfill every run listed in ``backfill``, ``batch`` runs per transaction; returns how
    many there were."""
    conn = await asyncpg.connect(dsn)
    try:
        if not await pending(conn):
            return 0
        total = await conn.fetchval("SELECT count(*) FROM backfill")
        report(f"backfilling the run table and series of {total} runs")
        started, done, after = time.perf_counter(), 0, 0
        while True:
            async with conn.transaction():
                records = await conn.fetch(RUNS, after, batch)
                if not records:
                    break
                by_project: dict[int, list[tuple[int, values.Changes]]] = {}
                for record in records:
                    pk, project_pk, streams = record[0], record[1], record[2]
                    changes = values.diff({}, columns(record))
                    by_project.setdefault(project_pk, []).append((pk, changes))
                    defs = parse_metric_defs(_parse(record["config"]))
                    await conn.execute(
                        "UPDATE runs SET metric_defs = $2::jsonb WHERE pk = $1",
                        pk,
                        orjson.dumps(defs).decode(),
                    )
                    for stream in streams:
                        await rebuild_chunks(conn, pk, stream)
                for project_pk, runs in sorted(by_project.items()):
                    await values.write(conn, project_pk, runs)
                pks = [record[0] for record in records]
                await conn.execute("DELETE FROM backfill WHERE run_pk = ANY($1)", pks)
            after = pks[-1]
            done += len(records)
            report(f"  {done}/{total} runs in {time.perf_counter() - started:.0f}s")
        return done
    finally:
        await conn.close()
