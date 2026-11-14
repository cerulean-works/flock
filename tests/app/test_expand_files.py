"""Expand files only expand (D150): a text check for statements that break the release before
them, which belong in a contract file."""

import re

import pytest

from app.api.src.db.migrate import Migration, Migrations

# Files before this were all applied everywhere before expand and contract existed (D146).
FIRST_CHECKED = 20

CHECKED = [m for m in Migrations().migrations() if not m.contract and m.number >= FIRST_CHECKED]

ALLOW = re.compile(r"--\s*flock:\s*allow-breaking\s+\S")
BREAKING = {
    "DROP TABLE": re.compile(r"\bDROP\s+TABLE\b"),
    "DROP COLUMN": re.compile(r"\bDROP\s+COLUMN\b"),
    "RENAME": re.compile(r"\bRENAME\b"),
    "ALTER COLUMN ... TYPE": re.compile(r"\bALTER\s+(COLUMN\s+)?\S+\s+(SET\s+DATA\s+)?TYPE\b"),
    "SET NOT NULL": re.compile(r"\bSET\s+NOT\s+NULL\b"),
}
# One `ADD [COLUMN]` clause of an ALTER TABLE, up to the next clause.
ADD_CLAUSE = re.compile(r"\bADD\s+(?!CONSTRAINT\b|PRIMARY\b|UNIQUE\b|CHECK\b|FOREIGN\b|EXCLUDE\b)")


def statements(sql: str) -> list[tuple[str, bool]]:
    """Each statement, without comments, and whether an allow-breaking line came right above
    it."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    found: list[tuple[str, bool]] = []
    current: list[str] = []
    allowed = above = False
    for line in sql.splitlines():
        if ALLOW.search(line):
            above = True
            continue
        code = line.split("--", 1)[0]
        for i, part in enumerate(code.split(";")):
            if i > 0:  # a statement ended
                found.append((" ".join(current), allowed))
                current, allowed = [], False
            if part.strip():
                if not " ".join(current).strip():
                    allowed, above = above, False
                current.append(part.strip())
        if code.strip():
            above = False
    if " ".join(current).strip():
        found.append((" ".join(current), allowed))
    return [(statement, allow) for statement, allow in found if statement.strip()]


def breaking(statement: str) -> list[str]:
    """The kinds of breaking change ``statement`` makes."""
    statement = re.sub(r"\s+", " ", statement).upper()
    kinds = [kind for kind, pattern in BREAKING.items() if pattern.search(statement)]
    if re.match(r"ALTER TABLE\b", statement):
        for clause in ADD_CLAUSE.split(statement)[1:]:
            clause = re.split(r",\s*(?=ADD\b|ALTER\b|DROP\b)", clause)[0]
            if "NOT NULL" in clause and "DEFAULT" not in clause:
                kinds.append("ADD COLUMN ... NOT NULL without DEFAULT")
    return kinds


def problems(sql: str) -> list[str]:
    return [
        f"{kind}: {statement}"
        for statement, allowed in statements(sql)
        if not allowed
        for kind in breaking(statement)
    ]


@pytest.mark.parametrize("migration", CHECKED, ids=[m.path.name for m in CHECKED])
def test_expand_files_only_expand(migration: Migration) -> None:
    found = problems(migration.sql())
    assert not found, (
        f"{migration.path.name} breaks the release before it, so these belong in a contract "
        "file (app/api/src/db/migrations/README.md):\n" + "\n".join(found)
    )


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE things;",
        "ALTER TABLE things DROP COLUMN old;",
        "alter table things\n  drop column old;",
        "ALTER TABLE things RENAME COLUMN old TO new;",
        "ALTER TABLE things RENAME TO stuff;",
        "ALTER TABLE things ALTER COLUMN n TYPE bigint;",
        "ALTER TABLE things ALTER n SET DATA TYPE bigint;",
        "ALTER TABLE things ALTER COLUMN n SET NOT NULL;",
        "ALTER TABLE things ADD COLUMN n int NOT NULL;",
        "ALTER TABLE things ADD COLUMN m int, ADD COLUMN n int NOT NULL;",
        "ALTER TABLE things ADD n int NOT NULL REFERENCES others;",
        "-- flock: allow-breaking never read\nCREATE TABLE a (id int);\nDROP TABLE b;",
    ],
)
def test_the_check_finds_breaking_statements(sql: str) -> None:
    assert problems(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE things (id int NOT NULL, name text NOT NULL);",
        "ALTER TABLE things ADD COLUMN n int;",
        "ALTER TABLE things ADD COLUMN n int NOT NULL DEFAULT 0;",
        "ALTER TABLE things ADD CONSTRAINT n_set CHECK (n IS NOT NULL) NOT VALID;",
        "ALTER TABLE things ALTER COLUMN n SET DEFAULT 0;",
        "-- DROP TABLE things;\nCREATE INDEX things_n ON things (n);",
        "/* DROP TABLE things; */ CREATE INDEX things_n ON things (n);",
        "-- flock: allow-breaking the column was never read\nALTER TABLE things DROP COLUMN old;",
        "-- flock: allow-breaking never read\n-- more about it\nALTER TABLE t\n  DROP COLUMN old;",
    ],
)
def test_the_check_allows_expanding_statements(sql: str) -> None:
    assert not problems(sql)
