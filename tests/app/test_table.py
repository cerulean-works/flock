"""``ColumnTable``, kept as the oracle for the run table's PostgreSQL queries (012, D77): every
case runs against both."""

import asyncio
import datetime as dt
import random

import asyncpg
import numpy as np
import pytest

from app.api.src.core import query, values
from app.api.src.core.query import FilterError, Selection, check_regex
from app.api.src.core.table import ColumnTable


def random_rows(n: int, seed: int = 0) -> list[dict]:
    """Run-table rows mixing ints, floats, strings, bools, lists and missing values."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        row: dict = {"name": f"r{i}"}
        if rng.random() < 0.8:
            row["summary.loss"] = rng.random()
        if rng.random() < 0.7:
            row["config.bs"] = rng.choice([16, 32, 64])
        if rng.random() < 0.6:
            row["config.opt"] = rng.choice(["adam", "sgd"])
        if rng.random() < 0.5:
            row["tags"] = rng.sample(["a", "b", "c"], 2)
        if rng.random() < 0.3:
            row["mixed"] = rng.choice([1.5, "text", True])
        rows.append(row)
    rows[3]["only.in.row3"] = 1.0  # a column that disappears with its only row
    return rows


def build(rows: list[dict]) -> ColumnTable:
    table = ColumnTable(capacity=4)  # small, so building grows the columns
    for row in rows:
        table.set_many(table.add_row(), row)
    return table


FILTERS = [
    {"summary.loss": {"$lt": 0.5}},
    {"config.bs": {"$in": [16, 64]}, "config.opt": "adam"},
    {"tags": "a"},
    {"mixed": {"$exists": True}},
    {"config.opt": {"$ne": "sgd"}},
    {"$or": [{"mixed": "text"}, {"mixed": True}]},
]


# More cases for PostgreSQL, whose compiler handles each operator separately.
MORE_FILTERS = [
    {"summary.loss": {"$gte": 0.2, "$lte": 0.7}},
    {"summary.loss": {"$not": {"$lt": 0.5}}},
    {"config.opt": {"$regex": "^ad"}},
    {"config.opt": {"$regex": "(?i)SG"}},
    {"config.opt": {"$gt": "b"}},
    {"config.opt": None},
    {"config.bs": {"$nin": [32]}},
    {"config.bs": {"$gt": 16}},
    {"tags": {"$in": ["c"]}},
    {"tags": ["a", "b"]},
    {"mixed": {"$exists": False}},
    {"mixed": {"$gt": 1}},
    {"mixed": {"$ne": True}},
    {"name": {"$regex": "1$"}},
    {"name": {"$in": ["r1", "r7", "nope"]}},
    {"missing.column": 1},
    {"missing.column": None},
    {"$nor": [{"config.bs": 16}, {"config.opt": "sgd"}]},
    {"$and": [{"summary.loss": {"$gt": 0.1}}, {"$or": [{"tags": "b"}, {"config.bs": 64}]}]},
    {},
]

SORTS = [
    [("summary.loss", True)],
    [("config.opt", False), ("summary.loss", False)],
    [("mixed", False)],
    [("mixed", True), ("name", True)],
    [("tags", False)],
    [("missing.column", True)],
]


def assert_same(table: ColumnTable, expected: ColumnTable) -> None:
    assert table.n == expected.n
    assert table.column_names() == expected.column_names()
    every = np.arange(expected.n)
    for name in expected.column_names():
        column, reference = table.columns[name], expected.columns[name]
        assert (column.n_num, column.n_obj, column.kind) == (
            reference.n_num,
            reference.n_obj,
            reference.kind,
        ), name
        cells = range(expected.n)
        assert [column.get(r) for r in cells] == [reference.get(r) for r in cells], name
    for expr in FILTERS:
        assert np.array_equal(table.filter(expr), expected.filter(expr)), expr
    for keys in ([("summary.loss", True)], [("config.opt", False), ("summary.loss", False)]):
        assert table.sort(every, keys).tolist() == expected.sort(every, keys).tolist()


@pytest.mark.parametrize("order", ["kept", "shuffled"])
def test_take_matches_a_table_built_from_the_kept_rows(order):
    rows = random_rows(200)
    keep = [i for i in range(len(rows)) if i % 3 and i != 3]
    if order == "shuffled":
        random.Random(1).shuffle(keep)
    table = build(rows)
    table.take(np.array(keep))
    assert "only.in.row3" not in table.columns
    assert_same(table, build([rows[i] for i in keep]))
    row = table.add_row()  # still growable afterwards
    table.set(row, "summary.loss", 0.25)
    assert table.columns["summary.loss"].get(row) == 0.25


def test_take_everything_away():
    table = build(random_rows(10))
    table.take(np.array([], dtype=np.int64))
    assert table.n == 0
    assert table.columns == {}


def test_append_from_copies_rows_between_tables():
    rows = random_rows(120, seed=2)
    source, target = build(rows[:100]), build(rows[100:])
    moved = [5, 17, 3, 60]
    new = target.append_from(source, np.array(moved))
    assert new.tolist() == [20, 21, 22, 23]
    assert_same(target, build(rows[100:] + [rows[i] for i in moved]))


def test_postgres_answers_like_the_column_table(postgres):
    """The filter compiler, sorts and column counts over ``run_values`` agree with
    ``ColumnTable`` on the same rows (the in-memory table the run table used to be)."""
    rows = random_rows(150, seed=3)
    del rows[3]["only.in.row3"]  # a removed value leaves a count of 0 behind
    table = build(rows)
    url = postgres.create()

    async def check() -> None:
        conn = await asyncpg.connect(url)
        try:
            org = await conn.fetchval(
                "INSERT INTO orgs (name, display_name, created_at, updated_at) "
                "VALUES ('flock', 'Flock', now(), now()) RETURNING pk"
            )
            project = await conn.fetchval(
                "INSERT INTO projects (org_pk, name, created_at, updated_at) "
                "VALUES ($1, 't', now(), now()) RETURNING pk",
                org,
            )
            start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
            pks = await conn.fetch(
                "INSERT INTO runs (project_pk, name, state, created_at, updated_at, "
                "heartbeat_at) SELECT $1, name, 'finished', at, at, at "
                "FROM unnest($2::text[], $3::timestamptz[]) AS t(name, at) RETURNING pk",
                project,
                [row["name"] for row in rows],
                [start + dt.timedelta(seconds=i) for i in range(len(rows))],
            )
            run_pks = [record[0] for record in pks]
            with_row3 = dict(rows[3], **{"only.in.row3": 1.0})
            await values.write(conn, project, [(run_pks[3], values.diff({}, with_row3))])
            await values.write(
                conn,
                project,
                [(pk, values.diff(with_row3 if i == 3 else {}, row)) for i, (pk, row) in
                 enumerate(zip(run_pks, rows, strict=True))],
            )  # fmt: skip
            row_of = {pk: i for i, pk in enumerate(run_pks)}

            columns = {column.name: column for column in await query.columns(conn, project)}
            assert list(columns) == table.column_names()
            for name, column in columns.items():
                reference = table.columns[name]
                assert (column.runs, column.objs) == (
                    reference.n_num + reference.n_obj,
                    reference.n_obj,
                ), name
                assert column.kind == reference.kind, name
            cells = await query.cells(conn, project, run_pks, list(columns))
            for name in columns:
                expected = [table.columns[name].get(r) for r in range(table.n)]
                assert cells[name] == [list(v) if isinstance(v, tuple) else v for v in expected], (
                    name
                )

            every = np.arange(table.n)
            for expr in [*FILTERS, *MORE_FILTERS]:
                found, total = await Selection(expr).runs(conn, project)
                expected = np.flatnonzero(table.filter(expr)).tolist()
                assert sorted(row_of[record[0]] for record in found) == expected, expr
                assert total == len(expected), expr
            for keys in SORTS:
                found, _ = await Selection(sort=tuple(keys)).runs(conn, project)
                assert [row_of[record[0]] for record in found] == table.sort(
                    every, keys
                ).tolist(), keys
            found, total = await Selection(FILTERS[0], (("summary.loss", False),)).runs(
                conn, project, offset=5, limit=10
            )
            expected = table.sort(np.flatnonzero(table.filter(FILTERS[0])), SORTS[1][1:])
            assert [row_of[record[0]] for record in found] == expected[5:15].tolist()
            assert total == len(expected)
        finally:
            await conn.close()

    asyncio.run(check())


@pytest.mark.parametrize(
    "pattern",
    [
        r"\bloss",
        r"loss\B",
        r"(?P<name>a)",
        r"(?P=name)",
        r"(?>a)",
        r"a*+",
        r"a{2}?+",
        r"(?s)a.b",
        r"a(?i)b",
        r"(?(1)a|b)",
        r"\N{DIGIT ONE}",
        "(",
    ],
)
def test_regex_postgres_reads_differently_is_refused(pattern):
    with pytest.raises(FilterError):
        check_regex(pattern)


@pytest.mark.parametrize(
    "pattern", [r"^adam", r"(?i)ADAM", r"[\b]x", r"a{2,3}?", r"(?:ab)+$", r"\d+\.\d", r"(?=a)a"]
)
def test_regex_both_read_alike_is_accepted(pattern):
    check_regex(pattern)
