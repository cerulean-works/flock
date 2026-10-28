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
from collections.abc import Iterable, Mapping, Sequence
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

    def __init__(self, project_pk: int, kinds: Mapping[str, frozenset[str]] | None = None) -> None:
        self.args: list[Any] = [project_pk]
        # The value kinds (``values.KINDS``) each filtered column holds, if known: clauses on
        # kinds a column doesn't hold match nothing, and aren't run.
        self.kinds = kinds

    def _holds(self, key: str, kinds: Sequence[str]) -> bool:
        if self.kinds is None or not kinds:
            return True
        return not self.kinds.get(key, frozenset()).isdisjoint(kinds)

    def arg(self, value: Any, cast: str = "") -> str:
        self.args.append(value)
        return f"${len(self.args)}{cast}"

    # ------------------------------------------------------------------ sets

    @staticmethod
    def _and(parts: Sequence[str]) -> str:
        if NONE in parts:
            return NONE
        parts = [part for part in parts if part != ALL]
        if not parts:
            return ALL
        return parts[0] if len(parts) == 1 else " INTERSECT ".join(f"({p})" for p in parts)

    @staticmethod
    def _or(parts: Sequence[str]) -> str:
        if ALL in parts:
            return ALL
        parts = [part for part in parts if part != NONE]
        if not parts:
            return NONE
        return parts[0] if len(parts) == 1 else " UNION ".join(f"({p})" for p in parts)

    @staticmethod
    def _except(part: str) -> str:
        if part == NONE:
            return ALL
        if part == ALL:
            return NONE
        return f"({ALL}) EXCEPT ({part})"

    def _leaf(
        self, key: str, condition: str, *kinds: str, value: Any = None, cast: str = ""
    ) -> str:
        """Runs whose value of ``key`` meets ``condition``, which only values of ``kinds`` can
        (any value if none are given). ``value``, if given, is the parameter ``{}`` stands for
        in ``condition``, allocated only if the clause is run."""
        if not self._holds(key, kinds):
            return NONE
        condition = self._bind(condition, value, cast)
        return (
            "SELECT run_pk FROM run_values "
            f"WHERE project_pk = $1 AND key = {self.arg(key)} AND {condition}"
        )

    def _text_leaf(self, key: str, condition: str, value: Any = None, cast: str = "") -> str:
        """Runs whose string matches ``condition`` (as ``_leaf``): short strings from their
        index, long ones (not indexed with their value, see 0013) from the table."""
        if not self._holds(key, ["strs"]):
            return NONE
        return self._split_leaf(key, "text(key, txt)", self._bind(condition, value, cast))

    def _list_leaf(self, key: str, condition: str, value: Any = None, cast: str = "") -> str:
        """Like ``_text_leaf``, for lists."""
        if not self._holds(key, ["lists"]):
            return NONE
        return self._split_leaf(key, "list(key, obj)", self._bind(condition, value, cast))

    def _bind(self, condition: str, value: Any, cast: str) -> str:
        return condition if value is None else condition.format(self.arg(value, cast))

    def _split_leaf(self, key: str, kind: str, condition: str) -> str:
        key_arg = self.arg(key)
        leaf = f"SELECT run_pk FROM run_values WHERE project_pk = $1 AND key = {key_arg} AND "
        return (
            f"{leaf}run_values_short_{kind} AND {condition} "
            f"UNION ALL {leaf}run_values_long_{kind} AND {condition}"
        )

    def _listed(self, key: str, items: Iterable[Any]) -> str | None:
        """Runs whose list value holds one of ``items`` (as Python's ``in`` would find it)."""
        scalars = [item for item in items if not isinstance(item, (list, dict))]
        nested = [item for item in items if isinstance(item, (list, dict))]
        parts = []
        if scalars:
            # A scalar is contained in an array only as one of its own elements.
            arrays = [_json([item]) for item in scalars]
            parts.append(self._list_leaf(key, "obj @> ANY({})", arrays, "::jsonb[]"))
        if nested:
            # Containers are compared whole (jsonb containment would match them in part).
            items_json = [_json(item) for item in nested]
            parts.append(
                self._list_leaf(
                    key,
                    "EXISTS (SELECT FROM jsonb_array_elements(obj) e WHERE e = ANY({}))",
                    items_json,
                    "::jsonb[]",
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
            present = self._leaf(name, "true", *values.KINDS)
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
                condition = f"num {sql} {{}} AND num <> 'NaN'"
                return self._leaf(name, condition, "nums", value=float(operand), cast="::float8")
            if isinstance(operand, str):
                return self._text_leaf(name, f"txt {sql} {{}}", operand)
            raise FilterError(f"{op} expects a number or string")
        if op == "$regex":
            if not isinstance(operand, str):
                raise FilterError("$regex expects a string")
            check_regex(operand)
            # Character classes follow the database's locale, as Python's follow Unicode.
            return self._text_leaf(name, 'txt COLLATE "default" ~ {}', operand)
        if op == "$contains":
            if isinstance(operand, str):
                substring = self._text_leaf(name, "strpos(txt, {}) > 0", operand)
                listed = self._listed(name, [operand])
                return self._or([substring, listed] if listed else [substring])
            return self._listed(name, [operand]) or NONE
        raise FilterError(f"unknown operator {op}")

    def _eq(self, name: str, operand: object) -> str:
        if operand is None:
            return self._except(self._leaf(name, "true", *values.KINDS))
        if _is_number(operand):
            if math.isnan(operand):
                return NONE
            number = self._leaf(name, "num = {}", "nums", value=float(operand), cast="::float8")
            return self._or([number, self._listed(name, [operand]) or NONE])
        if isinstance(operand, str):
            text = self._text_leaf(name, "txt = {}", operand)
            return self._or([text, self._listed(name, [operand]) or NONE])
        if isinstance(operand, list):
            return self._leaf(name, "obj = {}", "lists", value=_json(operand), cast="::jsonb")
        whole = self._leaf(
            name, "obj = {}", "bools", "objects", value=_json(operand), cast="::jsonb"
        )
        return self._or([whole, self._listed(name, [operand]) or NONE])

    def _in(self, name: str, operand: object) -> str:
        if not isinstance(operand, list):
            raise FilterError("$in expects a list")
        numbers = [float(v) for v in operand if _is_number(v) and not math.isnan(v)]
        others = [v for v in operand if not _is_number(v) and v is not None]
        hashable = [v for v in others if not isinstance(v, (list, dict))]
        parts = []
        if numbers:
            parts.append(
                self._leaf(name, "num = ANY({})", "nums", value=numbers, cast="::float8[]")
            )
        if None in operand:
            parts.append(self._except(self._leaf(name, "true", *values.KINDS)))
        strings = [v for v in hashable if isinstance(v, str)]
        if strings:
            parts.append(self._text_leaf(name, "txt = ANY({})", strings, "::text[]"))
        flags = [v for v in hashable if isinstance(v, bool)]
        if flags:
            flags_json = [_json(v) for v in flags]
            parts.append(
                self._leaf(name, "obj = ANY({})", "bools", value=flags_json, cast="::jsonb[]")
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


def _numeric_sort_terms(alias: str, desc: bool) -> list[str]:
    """``_sort_terms`` for a column holding only numbers (joined on ``num IS NOT NULL``), in
    the same order. Its join can then read the key's numbers from their index alone."""
    return [
        f"({alias}.num IS NULL OR {alias}.num = 'NaN')",
        f"{alias}.num {'DESC' if desc else 'ASC'}",
    ]


def _text_sort_terms(alias: str, desc: bool) -> list[str]:
    """``_sort_terms`` for a column holding only strings, in the same order."""
    return [f"{alias}.txt IS NULL", f"{alias}.txt {'DESC' if desc else 'ASC'}"]


# The value kinds that the keys $2 of project $1 hold (counting changes not folded yet).
KEY_KINDS = f"""
    SELECT key, {", ".join(f"sum({kind}) > 0" for kind in values.KINDS)} FROM (
        SELECT key, {", ".join(values.KINDS)} FROM project_keys
        WHERE project_pk = $1 AND key = ANY($2)
        UNION ALL
        SELECT key, {", ".join(values.KINDS)} FROM project_key_changes
        WHERE project_pk = $1 AND key = ANY($2)
    ) counted
    GROUP BY key
"""


async def key_kinds(
    conn: asyncpg.Pool | asyncpg.Connection, project_pk: int, keys: Iterable[str]
) -> dict[str, frozenset[str]]:
    """Which ``values.KINDS`` each of ``keys`` holds in the project (none if no run has it)."""
    keys = list(keys)
    found = dict.fromkeys(keys, frozenset[str]())
    for key, *held in await conn.fetch(KEY_KINDS, project_pk, keys) if keys else ():
        found[key] = frozenset(kind for kind, yes in zip(values.KINDS, held, strict=True) if yes)
    return found


def filter_keys(expr: object) -> set[str]:
    """The columns a filter's clauses are on (of a well-formed filter; ``sql`` checks it)."""
    found: set[str] = set()
    if not isinstance(expr, dict):
        return found
    for key, value in expr.items():
        if key in ("$and", "$or", "$nor") and isinstance(value, list):
            for sub in value:
                found |= filter_keys(sub)
        elif key == "$not":
            found |= filter_keys(value)
        elif not key.startswith("$"):
            found.add(canonical(key))
    return found


@dataclass(frozen=True)
class Selection:
    """Which runs of a project, in which order: a filter, sort keys (``(column, descending)``)
    and whether only active runs."""

    filter: object = None
    sort: tuple[tuple[str, bool], ...] = ()
    running: bool = False

    def sql(
        self,
        project_pk: int,
        columns: str,
        offset: int = 0,
        limit: int | None = None,
        kinds: Mapping[str, frozenset[str]] | None = None,
    ) -> tuple[str, list[Any]]:
        """``SELECT columns`` of the selected runs (aliased ``r``) from ``offset`` and at most
        ``limit``, in order, and the number of matches (``p.total``). With the ``kinds`` of
        values the filtered and sorted columns hold (``key_kinds``), clauses on kinds a column
        doesn't hold are left out, and columns of numbers or of strings sort faster.

        The order is found from run pks alone, and the page's columns read for its runs only,
        so sorting thousands of runs doesn't carry their whole rows along."""
        compiler = _Compiler(project_pk, kinds)
        kinds = kinds or {}
        where = ["r.project_pk = $1", "r.deleted_at IS NULL"]
        if self.running:
            where.append(f"r.state IN {ACTIVE_STATES}")
        if self.filter is not None and (matching := compiler.compile(self.filter)) != ALL:
            where.append(f"r.pk IN ({matching})")
        joins, order = [], []
        for i, (key, desc) in enumerate(self.sort):
            alias = f"s{i}"
            key = canonical(key)
            key_arg = compiler.arg(key)
            # The project too, so the join can read the key's index (project_pk, key, ...).
            join = (
                f"LEFT JOIN run_values {alias} ON {alias}.project_pk = $1 "
                f"AND {alias}.run_pk = r.pk AND {alias}.key = {key_arg}"
            )
            if kinds.get(key) == {"nums"}:
                joins.append(f"{join} AND {alias}.num IS NOT NULL")
                order += _numeric_sort_terms(alias, desc)
            elif kinds.get(key) == {"strs"}:
                joins.append(join)
                order += _text_sort_terms(alias, desc)
            else:
                joins.append(join)
                order += _sort_terms(alias, desc)
        # Ties keep the order runs were created in; with no sort, newest first.
        order += ["r.created_at", "r.pk"] if self.sort else ["r.created_at DESC", "r.pk DESC"]
        page = f" OFFSET {int(offset)}" + (f" LIMIT {int(limit)}" if limit is not None else "")
        joined, ordered = " ".join(joins), ", ".join(order)
        sql = (
            f"SELECT {columns}, p.total FROM ("
            f"SELECT r.pk, count(*) OVER () AS total FROM runs r {joined} "
            f"WHERE {' AND '.join(where)} ORDER BY {ordered}{page}"
            f") p JOIN runs r ON r.pk = p.pk {joined} ORDER BY {ordered}"
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
        # Read apart from the runs: a value of another kind written in between is left out of
        # this answer, as if it came a moment later.
        keys = {canonical(key) for key, _ in self.sort} | filter_keys(self.filter)
        kinds = await key_kinds(conn, project_pk, keys)
        sql, args = self.sql(project_pk, columns, offset, limit, kinds)
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


# ``values.COLUMNS`` of the keys $3 only.
_CELL_COLUMNS = values.COLUMNS.replace(
    "WHERE project_pk = $1", "WHERE project_pk = $1 AND key = ANY($3)"
)


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
        f"FROM run_values v LEFT JOIN ({_CELL_COLUMNS}) k ON k.key = v.key "
        "WHERE v.run_pk = ANY($2) AND v.key = ANY($3)",
        project_pk,
        list(run_pks),
        list(keys),
    )
    index = {pk: i for i, pk in enumerate(run_pks)}
    for run_pk, key, num, txt, obj, int_only in records:
        out[key][index[run_pk]] = values.decode(num, txt, obj, int_only)
    return out
