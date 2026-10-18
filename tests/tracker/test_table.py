import random

import numpy as np
import pytest

from src.core.table import ColumnTable


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
