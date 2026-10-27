"""The run table in SQL (D77): Mongo-style filters compiled to set operations over
``run_values``, sorts, the page's cells and a project's columns.

The semantics are ``ColumnTable``'s (``table.py``, kept as the test oracle): a clause is the set
of runs whose value matches, so a run without a value never matches ``$eq`` or a comparison,
and always matches ``$ne``, ``$nin`` and ``$not`` (they are "every run except"). Numbers never
compare with strings, NaN never compares, and lists match ``$eq``, ``$in`` and ``$contains`` by
their elements.
"""

import fnmatch
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, TypeGuard

import asyncpg
import orjson

from app.api.src.core import values

# A project's live runs, the set every negation starts from.
ALL = "SELECT pk FROM runs WHERE project_pk = $1 AND deleted_at IS NULL"
NONE = "SELECT NULL::bigint WHERE false"
ACTIVE_STATES = ("running", "preempting")


class FilterError(ValueError):
    pass


def canonical(name: str) -> str:
    """Map accepted aliases (``summary_metrics.x``, ``run.x``) onto canonical column names."""
    if name.startswith("summary_metrics."):
        return "summary." + name[len("summary_metrics.") :]
    if name.startswith("run."):
        return name[len("run.") :]
    return name


def _is_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _json(value: Any) -> str:
    return orjson.dumps(values._json_safe(value)).decode()


# Python regex syntax PostgreSQL either lacks or reads differently (\b is a backspace there,
# named groups and atomic groups don't exist, flags other than i mean something else). Such
# patterns are refused rather than answered differently (012, section 4).
_GROUPS = (":", "=", "!", "<=", "<!", "#")


def check_regex(pattern: str) -> None:
    """Raise ``FilterError`` unless ``pattern`` is valid Python and means the same to
    PostgreSQL's regular expressions (``~``)."""
    try:
        re.compile(pattern)
    except re.error as exc:
        raise FilterError(f"invalid regex: {exc}") from exc

    def refuse(what: str) -> FilterError:
        return FilterError(f"$regex doesn't support {what}, as PostgreSQL runs the pattern")

    i, n, in_class = 0, len(pattern), False
    while i < n:
        char = pattern[i]
        if char == "\\":
            escaped = pattern[i + 1 : i + 2]
            if not in_class and escaped in ("b", "B"):
                raise refuse(f"\\{escaped} (word boundaries)")
            if escaped in ("N", "G", "z", "h", "K", "R", "X"):
                raise refuse(f"\\{escaped}")
            i += 2
            continue
        if in_class:
            in_class = char != "]"
            i += 1
            continue
        if char == "[":
            in_class = True
            i += 1
            if pattern[i : i + 1] == "^":
                i += 1
            if pattern[i : i + 1] == "]":  # a literal ] first in a class
                i += 1
            continue
        if char == "(" and pattern[i + 1 : i + 2] == "?":
            rest = pattern[i + 2 :]
            if rest.startswith("i)") and i == 0:
                i += 4
                continue
            if not rest.startswith(_GROUPS):
                raise refuse("named or atomic groups, conditionals or flags other than (?i)")
        if char in "*+?}" and pattern[i + 1 : i + 2] == "+":
            raise refuse("possessive quantifiers")
        i += 1


class _Compiler:
    """Builds one filter's SQL: a set of run pks, with its parameters ($1 is the project)."""

    def __init__(self, project_pk: int) -> None:
        self.args: list[Any] = [project_pk]

    def arg(self, value: Any, cast: str = "") -> str:
        self.args.append(value)
        return f"${len(self.args)}{cast}"

    # ------------------------------------------------------------------ sets

    @staticmethod
    def _and(parts: Sequence[str]) -> str:
        if not parts:
            return ALL
        return parts[0] if len(parts) == 1 else " INTERSECT ".join(f"({p})" for p in parts)

    @staticmethod
    def _or(parts: Sequence[str]) -> str:
        if not parts:
            return NONE
        return parts[0] if len(parts) == 1 else " UNION ".join(f"({p})" for p in parts)

    @staticmethod
    def _except(part: str) -> str:
        return f"({ALL}) EXCEPT ({part})"

    def _leaf(self, key: str, condition: str) -> str:
        return (
            "SELECT run_pk FROM run_values "
            f"WHERE project_pk = $1 AND key = {self.arg(key)} AND {condition}"
        )

    def _listed(self, key: str, items: Iterable[Any]) -> str | None:
        """Runs whose list value holds one of ``items`` (as Python's ``in`` would find it)."""
        scalars = [item for item in items if not isinstance(item, (list, dict))]
        nested = [item for item in items if isinstance(item, (list, dict))]
        parts = []
        if scalars:
            # A scalar is contained in an array only as one of its own elements.
            arrays = self.arg([_json([item]) for item in scalars], "::jsonb[]")
            parts.append(self._leaf(key, f"jsonb_typeof(obj) = 'array' AND obj @> ANY({arrays})"))
        if nested:
            # Containers are compared whole (jsonb containment would match them in part).
            items_arg = self.arg([_json(item) for item in nested], "::jsonb[]")
            parts.append(
                self._leaf(
                    key,
                    "jsonb_typeof(obj) = 'array' AND EXISTS (SELECT FROM jsonb_array_elements(obj)"
                    f" e WHERE e = ANY({items_arg}))",
                )
            )
        return self._or(parts) if parts else None

    # ------------------------------------------------------------------ the language

    def compile(self, expr: object) -> str:
        if expr is None:
            return ALL
        if not isinstance(expr, dict):
            raise FilterError(f"filter must be an object, got {type(expr).__name__}")
        parts = []
        for key, value in expr.items():
            if key in ("$and", "$or", "$nor"):
                if not isinstance(value, list):
                    raise FilterError(f"{key} expects a list")
                subs = [self.compile(sub) for sub in value]
                if key == "$and":
                    parts.append(self._and(subs))
                elif key == "$or":
                    parts.append(self._or(subs))
                else:
                    parts.append(self._except(self._or(subs)))
            elif key == "$not":
                parts.append(self._except(self.compile(value)))
            elif key.startswith("$"):
                raise FilterError(f"unknown logical operator {key}")
            else:
                parts.append(self._field(canonical(key), value))
        return self._and(parts)

    def _field(self, name: str, cond: object) -> str:
        if isinstance(cond, dict) and cond and all(k.startswith("$") for k in cond):
            return self._and([self._op(name, op, operand) for op, operand in cond.items()])
        return self._op(name, "$eq", cond)

    def _op(self, name: str, op: str, operand: object) -> str:
        if op == "$exists":
            present = self._leaf(name, "true")
            return present if operand else self._except(present)
        if op == "$ne":
            return self._except(self._op(name, "$eq", operand))
        if op == "$nin":
            return self._except(self._op(name, "$in", operand))
        if op == "$not":
            if isinstance(operand, dict):
                ops = [self._op(name, sub_op, sub) for sub_op, sub in operand.items()]
                return self._except(self._and(ops))
            return self._except(self._op(name, "$eq", operand))
        if op == "$eq":
            return self._eq(name, operand)
        if op == "$in":
            return self._in(name, operand)
        if op in ("$gt", "$gte", "$lt", "$lte"):
            sql = {"$gt": ">", "$gte": ">=", "$lt": "<", "$lte": "<="}[op]
            if _is_number(operand):
                if math.isnan(operand):
                    return NONE  # NaN compares with nothing
                # PostgreSQL orders NaN above every number; numpy compares it with nothing.
                bound = self.arg(float(operand), "::float8")
                return self._leaf(name, f"num {sql} {bound} AND num <> 'NaN'")
            if isinstance(operand, str):
                return self._leaf(name, f"txt {sql} {self.arg(operand)}")
            raise FilterError(f"{op} expects a number or string")
        if op == "$regex":
            if not isinstance(operand, str):
                raise FilterError("$regex expects a string")
            check_regex(operand)
            # Character classes follow the database's locale, as Python's follow Unicode.
            return self._leaf(name, f'txt COLLATE "default" ~ {self.arg(operand)}')
        if op == "$contains":
            if isinstance(operand, str):
                substring = self._leaf(name, f"strpos(txt, {self.arg(operand)}) > 0")
                listed = self._listed(name, [operand])
                return self._or([substring, listed] if listed else [substring])
            return self._listed(name, [operand]) or NONE
        raise FilterError(f"unknown operator {op}")

    def _eq(self, name: str, operand: object) -> str:
        if operand is None:
            return self._except(self._leaf(name, "true"))
        if _is_number(operand):
            if math.isnan(operand):
                return NONE
            number = self._leaf(name, f"num = {self.arg(float(operand), '::float8')}")
            return self._or([number, self._listed(name, [operand]) or NONE])
        if isinstance(operand, str):
            text = self._leaf(name, f"txt = {self.arg(operand)}")
            return self._or([text, self._listed(name, [operand]) or NONE])
        if isinstance(operand, list):
            return self._leaf(name, f"obj = {self.arg(_json(operand), '::jsonb')}")
        whole = self._leaf(name, f"obj = {self.arg(_json(operand), '::jsonb')}")
        return self._or([whole, self._listed(name, [operand]) or NONE])

    def _in(self, name: str, operand: object) -> str:
        if not isinstance(operand, list):
            raise FilterError("$in expects a list")
        numbers = [float(v) for v in operand if _is_number(v) and not math.isnan(v)]
        others = [v for v in operand if not _is_number(v) and v is not None]
        hashable = [v for v in others if not isinstance(v, (list, dict))]
        parts = []
        if numbers:
            parts.append(self._leaf(name, f"num = ANY({self.arg(numbers, '::float8[]')})"))
        if None in operand:
            parts.append(self._except(self._leaf(name, "true")))
        strings = [v for v in hashable if isinstance(v, str)]
        if strings:
            parts.append(self._leaf(name, f"txt = ANY({self.arg(strings, '::text[]')})"))
        flags = [v for v in hashable if isinstance(v, bool)]
        if flags:
            parts.append(
                self._leaf(name, f"obj = ANY({self.arg([_json(v) for v in flags], '::jsonb[]')})")
            )
        listed = self._listed(name, [*hashable, *numbers])
        if listed:
            parts.append(listed)
        return self._or(parts)


def _sort_terms(alias: str, desc: bool) -> list[str]:
    """ORDER BY terms for one sort key joined as ``alias``: missing values (and NaN) last
    whichever the direction, then numbers and booleans (as 0/1) before strings before other
    values, each in their order."""
    direction = "DESC" if desc else "ASC"
    flag = f"jsonb_typeof({alias}.obj) = 'boolean'"
    return [
        f"({alias}.run_pk IS NULL OR {alias}.num IS NOT DISTINCT FROM 'NaN')",
        (
            f"CASE WHEN {alias}.num IS NOT NULL OR {flag} THEN 0 "
            f"WHEN {alias}.txt IS NOT NULL THEN 1 ELSE 2 END {direction}"
        ),
        f"coalesce({alias}.num, CASE WHEN {flag} THEN ({alias}.obj = 'true')::int END) {direction}",
        f"{alias}.txt {direction}",
        f"{alias}.obj::text {direction}",
    ]


@dataclass(frozen=True)
class Selection:
    """Which runs of a project, in which order: a filter, sort keys (``(column, descending)``)
    and whether only active runs."""

    filter: object = None
    sort: tuple[tuple[str, bool], ...] = ()
    running: bool = False

    def sql(self, project_pk: int, columns: str) -> tuple[str, list[Any]]:
        """``SELECT columns`` of the selected runs (aliased ``r``) in order, with ``count``
        the number of matches; the caller appends ``LIMIT``/``OFFSET``."""
        compiler = _Compiler(project_pk)
        where = ["r.project_pk = $1", "r.deleted_at IS NULL"]
        if self.running:
            where.append(f"r.state IN {ACTIVE_STATES}")
        if self.filter is not None:
            where.append(f"r.pk IN ({compiler.compile(self.filter)})")
        joins, order = [], []
        for i, (key, desc) in enumerate(self.sort):
            alias = f"s{i}"
            key_arg = compiler.arg(canonical(key))
            joins.append(
                f"LEFT JOIN run_values {alias} ON {alias}.run_pk = r.pk AND {alias}.key = {key_arg}"
            )
            order += _sort_terms(alias, desc)
        # Ties keep the order runs were created in; with no sort, newest first.
        order += ["r.created_at", "r.pk"] if self.sort else ["r.created_at DESC", "r.pk DESC"]
        sql = (
            f"SELECT {columns} FROM runs r {' '.join(joins)} WHERE {' AND '.join(where)} "
            f"ORDER BY {', '.join(order)}"
        )
        return sql, compiler.args

    async def runs(
        self,
        conn: asyncpg.Pool | asyncpg.Connection,
        project_pk: int,
        offset: int = 0,
        limit: int | None = None,
        columns: str = "r.pk, r.name",
    ) -> tuple[list[asyncpg.Record], int]:
        """The selected runs' ``columns`` (of ``runs r``), from ``offset`` and at most
        ``limit``, and how many match in all (each record's last column)."""
        sql, args = self.sql(project_pk, f"{columns}, count(*) OVER ()")
        sql += f" OFFSET {int(offset)}"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        try:
            records = await conn.fetch(sql, *args)
        except asyncpg.InvalidRegularExpressionError as exc:
            raise FilterError(f"invalid regex: {exc}") from exc
        if records:
            return records, records[0][-1]
        if offset == 0:
            return [], 0
        # The page is past the end: count the matches by themselves.
        total = len((await self.runs(conn, project_pk))[0])
        return [], total


@dataclass(frozen=True)
class Column:
    """One of a project's run table columns: how many runs have a value, of each type."""

    name: str
    int_only: bool
    runs: int
    nums: int
    strs: int
    bools: int
    lists: int
    objects: int

    @property
    def objs(self) -> int:
        return self.strs + self.bools + self.lists + self.objects

    @property
    def kind(self) -> str:
        """``number``, ``string``, ``bool``, ``list``, ``object`` or ``mixed`` (``Column.kind``)."""
        if self.objs == 0:
            return "number"
        if self.nums:
            return "mixed"
        found = [
            kind
            for kind, count in (
                ("string", self.strs),
                ("bool", self.bools),
                ("list", self.lists),
                ("object", self.objects),
            )
            if count
        ]
        return found[0] if len(found) == 1 else "mixed"


async def columns(conn: asyncpg.Pool | asyncpg.Connection, project_pk: int) -> list[Column]:
    """A project's columns, sorted by name (code point order, as Python sorts)."""
    records = await conn.fetch(
        f"SELECT * FROM ({values.COLUMNS}) c WHERE runs > 0 ORDER BY key", project_pk
    )
    return [Column(*record) for record in records]


def select_columns(names: Sequence[str], patterns: Iterable[str]) -> list[str]:
    """Expand column names / glob patterns (``summary.*loss*``) into column names: globs match
    the project's ``names``, plain names are kept even if no run has them."""
    out: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        pattern = canonical(pattern)
        if any(ch in pattern for ch in "*?["):
            matches = fnmatch.filter(names, pattern)
        else:
            matches = [pattern]
        for match in matches:
            if match not in seen:
                seen.add(match)
                out.append(match)
    return out


async def cells(
    conn: asyncpg.Pool | asyncpg.Connection,
    project_pk: int,
    run_pks: Sequence[int],
    keys: Sequence[str],
) -> dict[str, list[Any]]:
    """The values of ``keys`` for ``run_pks``, one list per key aligned with ``run_pks`` (None
    where a run has no value). Integral numbers of ``int_only`` columns are ints."""
    out = {key: [None] * len(run_pks) for key in keys}
    if not run_pks or not keys:
        return out
    records = await conn.fetch(
        "SELECT v.run_pk, v.key, v.num, v.txt, v.obj::text, coalesce(k.int_only, false) "
        f"FROM run_values v LEFT JOIN ({values.COLUMNS}) k ON k.key = v.key "
        "WHERE v.run_pk = ANY($2) AND v.key = ANY($3)",
        project_pk,
        list(run_pks),
        list(keys),
    )
    index = {pk: i for i, pk in enumerate(run_pks)}
    for run_pk, key, num, txt, obj, int_only in records:
        out[key][index[run_pk]] = values.decode(num, txt, obj, int_only)
    return out
