import fnmatch
import re
from collections.abc import Iterable, Sequence
from typing import TypeGuard

import numpy as np

from app.api.src.core.query import FilterError


class Column:
    __slots__ = ("has_list", "int_only", "n_num", "n_obj", "name", "num", "obj", "present")

    def __init__(self, name: str, capacity: int) -> None:
        self.name = name
        self.num = np.full(capacity, np.nan)
        self.obj: np.ndarray | None = None
        self.present = np.zeros(capacity, dtype=bool)
        self.int_only = True
        self.n_num = 0
        self.n_obj = 0
        self.has_list = False

    def grow(self, capacity: int) -> None:
        num = np.full(capacity, np.nan)
        num[: len(self.num)] = self.num
        self.num = num
        present = np.zeros(capacity, dtype=bool)
        present[: len(self.present)] = self.present
        self.present = present
        if self.obj is not None:
            obj = np.full(capacity, None, dtype=object)
            obj[: len(self.obj)] = self.obj
            self.obj = obj

    def set(self, row: int, value: object) -> None:
        if value is None:
            self.clear(row)
            return
        was_present = self.present[row]
        if was_present:
            self.clear(row)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            self.num[row] = value
            self.n_num += 1
            if self.int_only and not isinstance(value, int):
                self.int_only = False
        else:
            if self.obj is None:
                self.obj = np.full(len(self.num), None, dtype=object)
            if isinstance(value, list):
                value = tuple(value)
                self.has_list = True
            self.obj[row] = value
            self.n_obj += 1
        self.present[row] = True

    def clear(self, row: int) -> None:
        if not self.present[row]:
            return
        self.present[row] = False
        if self.obj is not None and self.obj[row] is not None:
            self.obj[row] = None
            self.n_obj -= 1
        else:
            self.num[row] = np.nan
            self.n_num -= 1

    def get(self, row: int) -> object:
        if not self.present[row]:
            return None
        if self.obj is not None and self.obj[row] is not None:
            value = self.obj[row]
            return list(value) if isinstance(value, tuple) else value  # lists are stored as tuples
        value = float(self.num[row])
        return int(value) if self.int_only and value.is_integer() else value

    def values(self, rows: np.ndarray) -> object:
        """Values for ``rows``; a numpy array when the column is purely numeric."""
        if self.obj is None:
            vals = self.num[rows]
            if self.int_only:
                present = self.present[rows]
                if present.all():
                    return vals.astype(np.int64)
                return [int(v) if p else None for v, p in zip(vals.tolist(), present.tolist())]
            return vals
        return [self.get(int(r)) for r in rows]

    @property
    def kind(self) -> str:
        if self.n_obj == 0:
            return "number"
        sample = (
            next((v for v in self.obj if v is not None), None) if self.obj is not None else None
        )
        obj_kind = (
            "string"
            if isinstance(sample, str)
            else "bool"
            if isinstance(sample, bool)
            else "list"
            if isinstance(sample, tuple)
            else "object"
        )
        return obj_kind if self.n_num == 0 else "mixed"


class ColumnTable:
    def __init__(self, capacity: int = 64) -> None:
        self.n = 0
        self.capacity = capacity
        self.columns: dict[str, Column] = {}
        self._names_cache: list[str] | None = None

    def add_row(self) -> int:
        if self.n == self.capacity:
            self.capacity *= 2
            for column in self.columns.values():
                column.grow(self.capacity)
        self.n += 1
        return self.n - 1

    def column(self, name: str) -> Column:
        column = self.columns.get(name)
        if column is None:
            column = self.columns[name] = Column(name, self.capacity)
            self._names_cache = None
        return column

    def set(self, row: int, name: str, value: object) -> None:
        if value is None:
            self.clear(row, name)
            return
        self.column(name).set(row, value)

    def clear(self, row: int, name: str) -> None:
        column = self.columns.get(name)
        if column is not None:
            column.clear(row)
            if column.n_num == 0 and column.n_obj == 0:
                del self.columns[name]  # no run has this key any more
                self._names_cache = None

    def set_many(self, row: int, values: dict[str, object]) -> None:
        columns = self.columns
        for name, value in values.items():
            if value is None:
                self.clear(row, name)
                continue
            column = columns.get(name)
            if column is None:
                column = self.column(name)
            column.set(row, value)

    def bulk_set(self, name: str, rows: list[int], values: list[object]) -> None:
        """Assign many cells of one (new or empty) column at once; used when loading from disk."""
        column = self.column(name)
        numeric_rows: list[int] = []
        numeric_vals: list[float] = []
        for row, value in zip(rows, values):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if column.present[row]:
                    column.clear(row)
                numeric_rows.append(row)
                numeric_vals.append(value)
                if column.int_only and not isinstance(value, int):
                    column.int_only = False
            else:
                column.set(row, value)
        if numeric_rows:
            idx = np.fromiter(numeric_rows, dtype=np.int64, count=len(numeric_rows))
            column.num[idx] = numeric_vals
            column.present[idx] = True
            column.n_num += len(numeric_rows)

    def append_from(self, other: ColumnTable, rows: np.ndarray) -> np.ndarray:
        """Append copies of ``other``'s ``rows`` (every column); returns their new row numbers."""
        start = self.n
        for _ in range(len(rows)):
            self.add_row()
        new = np.arange(start, self.n)
        for name, src in other.columns.items():
            present = src.present[rows]
            count = int(np.count_nonzero(present))
            if not count:
                continue
            dst = self.column(name)
            dst.num[new] = src.num[rows]
            dst.present[new] = present
            n_obj = 0
            if src.obj is not None:
                obj = src.obj[rows]
                if dst.obj is None:
                    dst.obj = np.full(len(dst.num), None, dtype=object)
                dst.obj[new] = obj
                n_obj = int(self._scan(obj, lambda v: True).sum())
            dst.n_obj += n_obj
            dst.n_num += count - n_obj
            dst.int_only = dst.int_only and src.int_only
            dst.has_list = dst.has_list or src.has_list
        return new

    def take(self, index: np.ndarray) -> None:
        """Keep only the rows in ``index``, in that order (row ``index[i]`` becomes row ``i``).

        Columns left without values are dropped. Costs O(rows × columns): meant for deleting and
        moving runs, not for the hot path.
        """
        index = np.asarray(index, dtype=np.int64)
        n = len(index)
        capacity = max(64, 1 << max(n - 1, 0).bit_length())
        for name in list(self.columns):
            column = self.columns[name]
            present = column.present[index]
            if not present.any():
                del self.columns[name]
                continue
            num = column.num[index]
            column.num = np.full(capacity, np.nan)
            column.num[:n] = num
            column.present = np.zeros(capacity, dtype=bool)
            column.present[:n] = present
            if column.obj is not None:
                obj = column.obj[index]
                column.obj = np.full(capacity, None, dtype=object)
                column.obj[:n] = obj
                column.n_obj = int(self._scan(obj, lambda v: True).sum())
            column.n_num = int(np.count_nonzero(present)) - column.n_obj
        self.n = n
        self.capacity = capacity
        self._names_cache = None

    @staticmethod
    def canonical(name: str) -> str:
        """Map accepted aliases (``summary_metrics.x``, ``run.x``) onto canonical column names."""
        if name.startswith("summary_metrics."):
            return "summary." + name[len("summary_metrics.") :]
        if name.startswith("run."):
            return name[len("run.") :]
        return name

    @staticmethod
    def _is_number(value: object) -> TypeGuard[int | float]:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    def column_names(self) -> list[str]:
        if self._names_cache is None:
            self._names_cache = sorted(self.columns)
        return self._names_cache

    def row_column_names(self, row: int, prefix: str) -> list[str]:
        return [
            name
            for name, column in self.columns.items()
            if name.startswith(prefix) and column.present[row]
        ]

    def select_columns(self, patterns: Sequence[str]) -> list[str]:
        """Expand column names / glob patterns (``summary.*loss*``) into existing column names."""
        out: list[str] = []
        seen: set[str] = set()
        names = None
        for pattern in patterns:
            pattern = self.canonical(pattern)
            if any(ch in pattern for ch in "*?["):
                if names is None:
                    names = self.column_names()
                matches = fnmatch.filter(names, pattern)
            else:
                matches = [pattern]
            for match in matches:
                if match not in seen:
                    seen.add(match)
                    out.append(match)
        return out

    # ------------------------------------------------------------------ filtering

    def filter(self, expr: object, mask: np.ndarray | None = None) -> np.ndarray:
        """Evaluate a Mongo-style filter, returning a boolean mask over the table's rows."""
        result = self._eval(expr)
        if mask is not None:
            result &= mask
        return result

    def _all(self) -> np.ndarray:
        return np.ones(self.n, dtype=bool)

    def _none(self) -> np.ndarray:
        return np.zeros(self.n, dtype=bool)

    def _eval(self, expr: object) -> np.ndarray:
        if expr is None:
            return self._all()
        if not isinstance(expr, dict):
            raise FilterError(f"filter must be an object, got {type(expr).__name__}")
        result: np.ndarray | None = None
        for key, value in expr.items():
            if key in ("$and", "$or", "$nor"):
                if not isinstance(value, list):
                    raise FilterError(f"{key} expects a list")
                parts = [self._eval(sub) for sub in value]
                if key == "$and":
                    mask = self._all()
                    for part in parts:
                        mask &= part
                else:
                    mask = self._none()
                    for part in parts:
                        mask |= part
                    if key == "$nor":
                        mask = ~mask
            elif key == "$not":
                mask = ~self._eval(value)
            elif key.startswith("$"):
                raise FilterError(f"unknown logical operator {key}")
            else:
                mask = self._field(self.canonical(key), value)
            result = mask if result is None else result & mask
        return self._all() if result is None else result

    def _field(self, name: str, cond: object) -> np.ndarray:
        column = self.columns.get(name)
        if isinstance(cond, dict) and cond and all(k.startswith("$") for k in cond):
            mask = self._all()
            for op, operand in cond.items():
                mask &= self._op(column, op, operand)
            return mask
        return self._op(column, "$eq", cond)

    def _op(self, column: Column | None, op: str, operand: object) -> np.ndarray:
        n = self.n
        if op == "$exists":
            present = column.present[:n].copy() if column is not None else self._none()
            return present if operand else ~present
        if op == "$ne":
            return ~self._op(column, "$eq", operand)
        if op == "$nin":
            return ~self._op(column, "$in", operand)
        if op == "$not":
            if isinstance(operand, dict):
                mask = self._all()
                for sub_op, sub_operand in operand.items():
                    mask &= self._op(column, sub_op, sub_operand)
                return ~mask
            return ~self._op(column, "$eq", operand)
        if column is None:
            if op == "$eq" and operand is None:
                return self._all()
            if op in ("$eq", "$in", "$gt", "$gte", "$lt", "$lte", "$regex", "$contains"):
                return self._none()
            raise FilterError(f"unknown operator {op}")

        num = column.num[:n]
        obj = column.obj[:n] if column.obj is not None else None

        if op == "$eq":
            if operand is None:
                return ~column.present[:n]
            if self._is_number(operand):
                mask = num == float(operand)
                if column.has_list and obj is not None:
                    mask |= self._scan(obj, lambda v: isinstance(v, tuple) and operand in v)
                return mask
            if obj is None:
                return self._none()
            if isinstance(operand, list):
                target = tuple(operand)
                return self._scan(obj, lambda v: v == target)
            if column.has_list:
                return self._scan(
                    obj, lambda v: v == operand or (isinstance(v, tuple) and operand in v)
                )
            if isinstance(operand, bool):
                return self._scan(obj, lambda v: v is operand)
            return np.asarray(obj == operand, dtype=bool)

        if op == "$in":
            if not isinstance(operand, list):
                raise FilterError("$in expects a list")
            numbers = [float(v) for v in operand if self._is_number(v)]
            others = [v for v in operand if not self._is_number(v) and v is not None]
            mask = np.isin(num, numbers) if numbers else self._none()
            if None in operand:
                mask |= ~column.present[:n]
            if others and obj is not None:
                hashable = {v for v in others if not isinstance(v, (list, dict))}
                mask |= self._scan(
                    obj,
                    lambda v: (
                        any(e in hashable for e in v)
                        if isinstance(v, tuple)
                        else (v in hashable if not isinstance(v, dict) else False)
                    ),
                )
            if numbers and column.has_list and obj is not None:
                wanted = set(numbers)
                mask |= self._scan(
                    obj,
                    lambda v: (
                        isinstance(v, tuple)
                        and any(self._is_number(e) and float(e) in wanted for e in v)
                    ),
                )
            return mask

        if op in ("$gt", "$gte", "$lt", "$lte"):
            if self._is_number(operand):
                value = float(operand)
                with np.errstate(invalid="ignore"):
                    if op == "$gt":
                        return num > value
                    if op == "$gte":
                        return num >= value
                    if op == "$lt":
                        return num < value
                    return num <= value
            if isinstance(operand, str):
                if obj is None:
                    return self._none()
                compare = {
                    "$gt": lambda v: isinstance(v, str) and v > operand,
                    "$gte": lambda v: isinstance(v, str) and v >= operand,
                    "$lt": lambda v: isinstance(v, str) and v < operand,
                    "$lte": lambda v: isinstance(v, str) and v <= operand,
                }[op]
                return self._scan(obj, compare)
            raise FilterError(f"{op} expects a number or string")

        if op == "$regex":
            if not isinstance(operand, str):
                raise FilterError("$regex expects a string")
            if obj is None:
                return self._none()
            try:
                pattern = re.compile(operand)
            except re.error as exc:
                raise FilterError(f"invalid regex: {exc}") from exc
            search = pattern.search
            return self._scan(obj, lambda v: isinstance(v, str) and search(v) is not None)

        if op == "$contains":
            if obj is None:
                return self._none()
            if isinstance(operand, str):
                return self._scan(
                    obj,
                    lambda v: (
                        (isinstance(v, str) and operand in v)
                        or (isinstance(v, tuple) and operand in v)
                    ),
                )
            return self._scan(obj, lambda v: isinstance(v, tuple) and operand in v)

        raise FilterError(f"unknown operator {op}")

    @staticmethod
    def _scan(obj: np.ndarray, predicate) -> np.ndarray:
        return np.fromiter(
            (v is not None and predicate(v) for v in obj), dtype=bool, count=len(obj)
        )

    # ------------------------------------------------------------------ sorting

    def sort(self, rows: np.ndarray, keys: Iterable[tuple[str, bool]]) -> np.ndarray:
        """Order ``rows`` by ``(column, descending)`` keys; missing values always sort last."""
        keys = list(keys)
        if not keys:
            return rows
        if len(keys) == 1:
            name, desc = keys[0]
            column = self.columns.get(self.canonical(name))
            if column is None:
                return rows
            if column.n_obj == 0:
                vals = column.num[rows]
                order = np.argsort(-vals if desc else vals, kind="stable")
                return rows[order]
        # General path: Python sort over (numbers < strings < other < missing) tuples.
        result = [int(r) for r in rows]
        for name, desc in reversed(keys):
            column = self.columns.get(self.canonical(name))
            if column is None:
                continue
            present = column.present
            num = column.num
            obj = column.obj
            missing = [r for r in result if not present[r]]
            have = [r for r in result if present[r]]

            def key_fn(r: int, num=num, obj=obj) -> tuple:
                if obj is not None and obj[r] is not None:
                    v = obj[r]
                    if isinstance(v, str):
                        return (1, 0.0, v)
                    if isinstance(v, bool):
                        return (0, float(v), "")
                    return (2, 0.0, repr(v))
                return (0, float(num[r]), "")

            have.sort(key=key_fn, reverse=desc)
            result = have + missing
        return np.asarray(result, dtype=np.int64)
