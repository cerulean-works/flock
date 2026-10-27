"""The run table's values (D77): a run's attributes, ``config.*`` and ``summary.*`` columns as
rows of ``run_values``, and the per-column counts ``project_keys`` keeps of them.

Numbers go in ``num`` (NaN and infinities included), strings in ``txt``, and everything else
(booleans, lists, wandb media and other objects) in ``obj`` as JSON, as ``ColumnTable`` kept
numbers in a float array and the rest as Python objects.
"""

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import orjson

from app.api.src.utils import flatten

if TYPE_CHECKING:
    import asyncpg

# Value types, as counted in ``project_keys`` (in its column order).
KINDS = ("nums", "strs", "bools", "lists", "objects")

# Run attributes stored alongside config.* and summary.* columns.
ATTRIBUTES = (
    "name",
    "display_name",
    "state",
    "created_at",
    "updated_at",
    "heartbeat_at",
    "tags",
    "group",
    "job_type",
    "notes",
    "description",
    "host",
    "program",
    "commit",
    "sweep",
    "exitcode",
    "owner",
    "gpu_type",
    "gpu_count",
    "gpu_hours",
)


def config_columns(config: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten a wandb config (``{"lr": {"value": 0.1}}``) into ``config.*`` columns."""
    out: dict[str, Any] = {}
    for key, item in config.items():
        if key == "_wandb":
            continue
        value = item["value"] if isinstance(item, dict) and "value" in item else item
        if isinstance(value, dict) and "_type" not in value and value:
            flatten(value, f"config.{key}.", out)
        else:
            out[f"config.{key}"] = value
    return out


def summary_columns(summary: Mapping[str, Any]) -> dict[str, Any]:
    return flatten({k: v for k, v in summary.items() if k != "_wandb"}, "summary.")


def gpu_hours(runtime: float, gpu_count: int | None) -> float | None:
    """GPU hours of a run alive ``runtime`` seconds; None while its GPU count is unknown."""
    return round(runtime * gpu_count / 3600, 6) if gpu_count is not None else None


def kind(value: Any) -> str:
    """Which ``KINDS`` a (non-None) value counts as."""
    if isinstance(value, bool):
        return "bools"
    if isinstance(value, (int, float)):
        return "nums"
    if isinstance(value, str):
        return "strs"
    if isinstance(value, (list, tuple)):
        return "lists"
    return "objects"


def _json_safe(value: Any) -> Any:
    """``value`` as jsonb holds it: non-finite numbers as strings, and no NUL characters."""
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(k).replace("\x00", ""): _json_safe(v) for k, v in value.items()}
    return value


def encode(value: Any) -> tuple[float | None, str | None, str | None]:
    """A (non-None) value as ``run_values``' ``(num, txt, obj)``, the last as JSON text."""
    found = kind(value)
    if found == "nums":
        try:
            return float(value), None, None
        except OverflowError:  # an integer beyond float64's range
            return math.copysign(math.inf, value), None, None
    if found == "strs":
        return None, value.replace("\x00", ""), None
    return None, None, orjson.dumps(_json_safe(value)).decode()


def decode(num: float | None, txt: str | None, obj: str | None, int_only: bool = False) -> Any:
    """A ``run_values`` row's value; integral numbers of ``int_only`` columns as ints."""
    if num is not None:
        return int(num) if int_only and num.is_integer() else num
    if txt is not None:
        return txt
    return orjson.loads(obj) if obj is not None else None


@dataclass
class Changes:
    """What a write does to one run's columns: the values it sets (None removes one), and how
    each column's ``project_keys`` counts move."""

    values: dict[str, Any] = field(default_factory=dict)
    # Per key: [runs, *KINDS] deltas.
    counts: dict[str, list[int]] = field(default_factory=dict)
    # Keys given numbers: whether every one of them was integral (a Python int).
    int_only: dict[str, bool] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.values)

    def set(self, key: str, old: Any, new: Any) -> None:
        """Change ``key`` from ``old`` to ``new`` (either None: absent)."""
        self.values[key] = new
        if old is not None and new is not None and kind(old) == kind(new):
            pass  # the counts stay
        else:
            counts = self.counts.setdefault(key, [0] * (len(KINDS) + 1))
            if old is not None:
                counts[0] -= 1
                counts[1 + KINDS.index(kind(old))] -= 1
            if new is not None:
                counts[0] += 1
                counts[1 + KINDS.index(kind(new))] += 1
        if new is not None and kind(new) == "nums":
            integral = isinstance(new, int)
            self.int_only[key] = self.int_only.get(key, True) and integral


def diff(
    old: Mapping[str, Any],
    new: Mapping[str, Any],
    keys: Iterable[str] | None = None,
    into: Changes | None = None,
) -> Changes:
    """The changes from ``old`` columns to ``new`` ones, over ``keys`` (default: both's keys),
    added to ``into`` if given. A missing or None value is an absent one; equal values (``==``)
    aren't rewritten."""
    changes = into if into is not None else Changes()
    for key in keys if keys is not None else dict.fromkeys([*old, *new]):
        before, after = old.get(key), new.get(key)
        if before is None and after is None:
            continue
        if before is not None and after is not None and before == after:
            continue
        changes.set(key, before, after)
    return changes


async def write(
    conn: asyncpg.Connection, project_pk: int, runs: Sequence[tuple[int, Changes]]
) -> None:
    """Apply each run's changes to ``run_values`` and ``project_keys``, in one statement.

    ``project_keys`` rows are upserted in key order, so concurrent writers to one project lock
    them in the same order and never deadlock. (Writers lock their runs' rows first, and runs
    in pk order, so the order of everything they lock is the same.) A count that drops to 0 leaves its row, which
    reads ignore and the purge removes; a column that comes back starts its ``int_only``
    afresh, as a new column did in ``ColumnTable``.
    """
    removed: tuple[list[int], list[str]] = ([], [])
    written: tuple[list[Any], ...] = ([], [], [], [], [])
    counts: dict[str, list[int]] = {}
    int_only: dict[str, bool] = {}
    for run_pk, changes in runs:
        for key, value in changes.values.items():
            if value is None:
                removed[0].append(run_pk)
                removed[1].append(key)
                continue
            num, txt, obj = encode(value)
            for column, item in zip(written, (run_pk, key, num, txt, obj), strict=True):
                column.append(item)
        for key, delta in changes.counts.items():
            total = counts.setdefault(key, [0] * (len(KINDS) + 1))
            for i, d in enumerate(delta):
                total[i] += d
        for key, integral in changes.int_only.items():
            int_only[key] = int_only.get(key, True) and integral
    if not removed[0] and not written[0]:
        return
    keys = sorted(counts.keys() | int_only.keys())
    deltas = [counts.get(key, [0] * (len(KINDS) + 1)) for key in keys]
    await conn.execute(
        """
        WITH removed AS (
            DELETE FROM run_values v
            USING unnest($2::bigint[], $3::text[]) AS d(run_pk, key)
            WHERE v.run_pk = d.run_pk AND v.key = d.key
        ), written AS (
            INSERT INTO run_values (project_pk, run_pk, key, num, txt, obj)
            SELECT $1, run_pk, key, num, txt, obj::jsonb
            FROM unnest($4::bigint[], $5::text[], $6::float8[], $7::text[], $8::text[])
                AS t(run_pk, key, num, txt, obj)
            ON CONFLICT (run_pk, key)
            DO UPDATE SET num = excluded.num, txt = excluded.txt, obj = excluded.obj
        )
        INSERT INTO project_keys AS k
            (project_pk, key, int_only, runs, nums, strs, bools, lists, objects)
        SELECT $1, key, int_only, runs, nums, strs, bools, lists, objects
        FROM unnest($9::text[], $10::bool[], $11::int[], $12::int[], $13::int[], $14::int[],
                    $15::int[], $16::int[])
            AS t(key, int_only, runs, nums, strs, bools, lists, objects)
        -- Rows whose counts stay, and whose int_only can't change, aren't touched (and so not
        -- locked): most writes only change values of columns every run has.
        WHERE runs <> 0 OR nums <> 0 OR strs <> 0 OR bools <> 0 OR lists <> 0 OR objects <> 0
            OR (NOT int_only AND EXISTS (
                SELECT FROM project_keys p WHERE p.project_pk = $1 AND p.key = t.key AND p.int_only
            ))
        ON CONFLICT (project_pk, key) DO UPDATE SET
            int_only = CASE WHEN k.runs <= 0 THEN excluded.int_only
                            ELSE k.int_only AND excluded.int_only END,
            runs = k.runs + excluded.runs, nums = k.nums + excluded.nums,
            strs = k.strs + excluded.strs, bools = k.bools + excluded.bools,
            lists = k.lists + excluded.lists, objects = k.objects + excluded.objects
        """,
        project_pk,
        *removed,
        *written,
        keys,
        [int_only.get(key, True) for key in keys],
        *([delta[i] for delta in deltas] for i in range(len(KINDS) + 1)),
    )


async def shift(
    conn: asyncpg.Connection, run_pks: Sequence[int], source: int | None, target: int | None
) -> None:
    """Move the column counts of runs' values from project ``source`` to project ``target``
    (call with the runs locked, before moving or deleting their values). Either may be None:
    a purge only takes counts out, and a copy only adds them.

    Each project's rows are locked in key order, as ``write`` locks them. A moved column keeps
    its ``int_only`` only if both projects' columns had it, as ``ColumnTable.append_from``
    did.
    """
    moved = await conn.fetch(
        """
        SELECT key, count(*)::int, count(num)::int, count(txt)::int,
               (count(*) FILTER (WHERE jsonb_typeof(obj) = 'boolean'))::int,
               (count(*) FILTER (WHERE jsonb_typeof(obj) = 'array'))::int,
               (count(*) FILTER (WHERE jsonb_typeof(obj) NOT IN ('boolean', 'array')))::int
        FROM run_values WHERE run_pk = ANY($1)
        GROUP BY key ORDER BY key
        """,
        list(run_pks),
    )
    if not moved:
        return
    keys = [row[0] for row in moved]
    counts = [[row[i] for row in moved] for i in range(1, len(KINDS) + 2)]
    int_only = dict.fromkeys(keys, True)
    if source is not None:
        # Locking happens above the sort, so the rows are locked in key order.
        await conn.execute(
            "SELECT FROM project_keys WHERE project_pk = $1 AND key = ANY($2) "
            "ORDER BY key FOR UPDATE",
            source,
            keys,
        )
        for key, flag in await conn.fetch(
            """
            UPDATE project_keys k
            SET runs = k.runs - t.runs, nums = k.nums - t.nums, strs = k.strs - t.strs,
                bools = k.bools - t.bools, lists = k.lists - t.lists,
                objects = k.objects - t.objects
            FROM unnest($2::text[], $3::int[], $4::int[], $5::int[], $6::int[], $7::int[],
                        $8::int[]) AS t(key, runs, nums, strs, bools, lists, objects)
            WHERE k.project_pk = $1 AND k.key = t.key
            RETURNING k.key, k.int_only
            """,
            source,
            keys,
            *counts,
        ):
            int_only[key] = flag
    if target is not None:
        await conn.execute(
            """
            INSERT INTO project_keys AS k
                (project_pk, key, int_only, runs, nums, strs, bools, lists, objects)
            SELECT $1, * FROM unnest($2::text[], $3::bool[], $4::int[], $5::int[], $6::int[],
                                     $7::int[], $8::int[], $9::int[])
            ON CONFLICT (project_pk, key) DO UPDATE SET
                int_only = CASE WHEN k.runs <= 0 THEN excluded.int_only
                                ELSE k.int_only AND excluded.int_only END,
                runs = k.runs + excluded.runs, nums = k.nums + excluded.nums,
                strs = k.strs + excluded.strs, bools = k.bools + excluded.bools,
                lists = k.lists + excluded.lists, objects = k.objects + excluded.objects
            """,
            target,
            keys,
            [int_only[key] for key in keys],
            *counts,
        )
