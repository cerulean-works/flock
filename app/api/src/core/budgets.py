"""GPU spend: the usage ledger (how long each run was alive per UTC month, with its GPUs),
per-org GPU prices and monthly budgets, and the budget panel computed from them.

Everything is read from PostgreSQL when asked for (D76): reports are aggregates over
``gpu_usage``, so every replica answers the same.
"""

import datetime as dt
import hashlib
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.api.src.core import gpus

if TYPE_CHECKING:
    import asyncpg

    from app.api.src.core.store import Org, Run, Store


# One row per ledger entry of an org's month: (project name, None once deleted), run pk, alive
# (the run still exists), seconds, GPU type and count, GPU hours, hourly cost (None: unpriced),
# excluded (its deleted project's spend is left out of the budget), deleted project pk.
LEDGER = """
    SELECT CASE WHEN p.deleted_at IS NULL THEN p.name END, u.run_pk,
           r.pk IS NOT NULL AND r.deleted_at IS NULL, u.seconds, u.gpu_type, u.gpu_count,
           u.seconds / 3600 * coalesce(u.gpu_count, 0), rt.hourly_cost,
           coalesce(d.excluded_at IS NOT NULL, false), u.deleted_project_pk
    FROM gpu_usage u
    LEFT JOIN projects p ON p.pk = u.project_pk
    LEFT JOIN runs r ON r.pk = u.run_pk
    LEFT JOIN gpu_rates rt ON rt.org_pk = u.org_pk AND lower(rt.gpu_type) = lower(u.gpu_type)
    LEFT JOIN deleted_projects d ON d.pk = u.deleted_project_pk
    WHERE u.org_pk = $1 AND u.month = $2
"""

# The ledger entries above, grouped so a month of thousands of runs is a few rows: totals by
# project, GPU type and whether they count, and the runs whose GPUs aren't known.
GROUPED = f"""
    SELECT project, lower(gpu_type), min(gpu_type), rate, excluded, deleted,
           sum(hours), (count(*) FILTER (WHERE hours > 0))::int,
           array_agg(run_pk) FILTER (WHERE gpu_count IS NULL AND alive AND seconds > 0),
           coalesce(sum(seconds) FILTER (WHERE gpu_count IS NULL AND alive AND seconds > 0), 0)
    FROM ({LEDGER}) AS u(project, run_pk, alive, seconds, gpu_type, gpu_count, hours, rate,
                         excluded, deleted)
    GROUP BY project, lower(gpu_type), rate, excluded, deleted
"""

# The month's runs that cost the most (those still existing, in projects that count).
TOP_RUNS = f"""
    SELECT u.run_pk, u.hours, u.hours * coalesce(u.rate, 0) AS cost, u.gpu_type, u.gpu_count,
           r.name, r.display_name, r.state, r.owner_pk, p.name, o.name
    FROM ({LEDGER}) AS u(project, run_pk, alive, seconds, gpu_type, gpu_count, hours, rate,
                         excluded, deleted)
    JOIN runs r ON r.pk = u.run_pk
    JOIN projects p ON p.pk = r.project_pk
    JOIN orgs o ON o.pk = p.org_pk
    WHERE u.alive AND NOT u.excluded AND u.hours > 0
    ORDER BY cost DESC, u.hours DESC, u.run_pk DESC
    LIMIT $3
"""


class Budgets:
    """Ledger reports, prices and budgets of every org (``store.budgets``)."""

    TOP_RUNS = 5
    # Warnings about fewer hours than this aren't raised: they would read "0 h".
    WARN_HOURS = 0.05
    WARNINGS = ("unpriced", "unknown_gpu")

    def __init__(self, store: Store) -> None:
        self.store = store

    @staticmethod
    async def _lock(conn: asyncpg.Connection, org: Org) -> None:
        """Serialize changes to ``org``'s prices, budgets and warnings (D80)."""
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('budgets:' || $1::bigint, 0))", org.pk
        )

    # ------------------------------------------------------------------ the ledger

    def accrual(self, since: float, now: float) -> list[tuple[dt.date, float]]:
        """Seconds to add for a heartbeat at ``now`` after one at ``since``: the time between,
        unless the run was silent longer than ``crash_timeout`` (a crash, or preempted and
        resumed)."""
        if not 0 < now - since <= self.store.settings.crash_timeout:
            return []
        return gpus.split_by_month(since, now)

    async def run_cost(self, run: Run) -> float:
        cost = await self.store.db.fetchval(
            "SELECT coalesce(sum(u.seconds / 3600 * coalesce(u.gpu_count, 0) "
            "* coalesce(rt.hourly_cost, 0)), 0) FROM gpu_usage u "
            "LEFT JOIN gpu_rates rt ON rt.org_pk = u.org_pk "
            "AND lower(rt.gpu_type) = lower(u.gpu_type) WHERE u.run_pk = $1",
            run.pk,
        )
        return _money(cost)

    # ------------------------------------------------------------------ prices and budgets

    async def update(
        self,
        org: Org,
        fields: dict[str, Any],
        rates: dict[str, float | None],
    ) -> None:
        """Change an org's currency or monthly budget (the keys present in ``fields``; the
        budget applies from the current month on) and merge ``rates`` (None removes one)."""
        month = gpus.month_of(time.time())
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            await self._lock(conn, org)
            if "currency" in fields:
                await conn.execute(
                    "UPDATE orgs SET currency = $2 WHERE pk = $1", org.pk, fields["currency"]
                )
            if "monthly_budget" in fields:
                await conn.execute(
                    "INSERT INTO org_budgets (org_pk, month, amount) VALUES ($1, $2, $3) "
                    "ON CONFLICT (org_pk, month) DO UPDATE SET amount = excluded.amount",
                    org.pk,
                    month,
                    fields["monthly_budget"],
                )
            removed = [gpu_type for gpu_type, cost in rates.items() if cost is None]
            if removed:
                await conn.execute(
                    "DELETE FROM gpu_rates WHERE org_pk = $1 "
                    "AND lower(gpu_type) = ANY(SELECT lower(t) FROM unnest($2::text[]) t)",
                    org.pk,
                    removed,
                )
            priced = {gpu_type: cost for gpu_type, cost in rates.items() if cost is not None}
            if priced:
                await conn.execute(
                    "INSERT INTO gpu_rates (org_pk, gpu_type, hourly_cost) "
                    "SELECT $1, t, c FROM unnest($2::text[], $3::float8[]) AS u(t, c) "
                    "ON CONFLICT (org_pk, lower(gpu_type)) "
                    "DO UPDATE SET gpu_type = excluded.gpu_type, "
                    "hourly_cost = excluded.hourly_cost",
                    org.pk,
                    list(priced),
                    list(priced.values()),
                )
        if "currency" in fields:
            org.currency = fields["currency"]

    async def _budgets(self, org: Org) -> list[tuple[dt.date, float | None]]:
        """The org's monthly budgets, by the month they apply from, in month order."""
        records = await self.store.db.fetch(
            "SELECT month, amount FROM org_budgets WHERE org_pk = $1 ORDER BY month", org.pk
        )
        return [(month, amount) for month, amount in records]

    @staticmethod
    def budget_for(budgets: list[tuple[dt.date, float | None]], month: dt.date) -> float | None:
        amount = None
        for start, value in budgets:  # sorted by month
            if start > month:
                break
            amount = value
        return amount

    # ------------------------------------------------------------------ reports

    async def billing(self, org: Org) -> dict[str, Any]:
        """An org's prices and budgets, with every GPU type its runs used."""
        db = self.store.db
        names: dict[str, str] = {}
        hours: dict[str, float] = defaultdict(float)
        runs: dict[str, int] = defaultdict(int)
        # Runs with no GPU time don't count; entries whose run is gone count one each.
        for key, gpu_type, total, count in await db.fetch(
            "SELECT lower(gpu_type), min(gpu_type), sum(seconds / 3600 * gpu_count), "
            "(count(DISTINCT run_pk) + count(*) FILTER (WHERE run_pk IS NULL))::int "
            "FROM gpu_usage WHERE org_pk = $1 AND gpu_type IS NOT NULL AND gpu_type <> '' "
            "AND seconds * gpu_count > 0 GROUP BY lower(gpu_type)",
            org.pk,
        ):
            names[key], hours[key], runs[key] = gpu_type, total, count
        # GPUs of runs that haven't accrued any time yet are listed, to be priced before.
        for (gpu_type,) in await db.fetch(
            "SELECT DISTINCT coalesce(r.gpu_type_override, r.gpu_type) FROM runs r "
            "JOIN projects p ON p.pk = r.project_pk AND p.org_pk = $1 AND p.deleted_at IS NULL "
            "WHERE r.deleted_at IS NULL AND coalesce(r.gpu_type_override, r.gpu_type) <> '' "
            "AND coalesce(r.gpu_count_override, r.gpu_count) > 0",
            org.pk,
        ):
            names.setdefault(gpu_type.casefold(), gpu_type)
        rates = {
            gpu_type.casefold(): (gpu_type, cost)
            for gpu_type, cost in await db.fetch(
                "SELECT gpu_type, hourly_cost FROM gpu_rates WHERE org_pk = $1", org.pk
            )
        }
        for key, (gpu_type, _) in rates.items():
            names.setdefault(key, gpu_type)
        budgets = await self._budgets(org)
        currency = await db.fetchval("SELECT currency FROM orgs WHERE pk = $1", org.pk)
        month = gpus.month_of(time.time())
        return {
            "currency": currency,
            "monthly_budget": self.budget_for(budgets, month),
            "budgets": [
                {"month": gpus.format_month(start), "amount": amount} for start, amount in budgets
            ],
            "rates": [
                {"gpu_type": gpu_type, "hourly_cost": cost}
                for gpu_type, cost in sorted(rates.values(), key=lambda r: r[0].casefold())
            ],
            "gpu_types": [
                {
                    "gpu_type": names[key],
                    "gpu_hours": round(hours[key], 3),
                    "runs": runs[key],
                    "hourly_cost": rates[key][1] if key in rates else None,
                }
                for key in sorted(names, key=lambda k: (-hours[k], k))
            ],
        }

    async def budget(self, org: Org, month: dt.date) -> dict[str, Any]:
        """Spend of ``org`` in ``month`` against its budget, by project, with its top runs and
        its warnings. Entries without GPU hours count towards nothing, and the spend of deleted
        projects an admin excluded is reported apart."""
        return (await self._report(org, month))[0]

    async def _report(self, org: Org, month: dt.date) -> tuple[dict[str, Any], dict[str, set[str]]]:
        """The budget, and what each of its warnings is about (see ``_warnings``)."""
        db = self.store.db
        spent = gpu_hours = excluded_cost = excluded_hours = 0.0
        excluded: set[int] = set()
        projects: dict[str | None, list] = defaultdict(lambda: [0.0, 0.0, 0])
        unpriced: dict[str, list] = {}
        unknown: set[int] = set()
        unknown_seconds = 0.0
        for record in await db.fetch(GROUPED, org.pk, month):
            project, key, gpu_type, rate, is_excluded, deleted, hours, counted = record[:8]
            cost = hours * (rate or 0.0)
            if is_excluded:
                if hours > 0:
                    excluded.add(deleted)
                    excluded_cost += cost
                    excluded_hours += hours
                continue
            # Only runs that still exist: the GPUs of deleted ones can't be corrected.
            unknown.update(record[8] or ())
            unknown_seconds += record[9]
            if hours <= 0:
                continue
            if rate is None:
                entry = unpriced.setdefault(key or "", [gpu_type, 0.0])
                entry[1] += hours
            spent += cost
            gpu_hours += hours
            totals = projects[project]
            totals[0] += cost
            totals[1] += hours
            totals[2] += counted
        budget = self.budget_for(await self._budgets(org), month)
        unknown_hours = unknown_seconds / 3600
        warnings, items = await self._warnings(org, month, unpriced, unknown, unknown_hours)
        top = await db.fetch(TOP_RUNS, org.pk, month, self.TOP_RUNS)
        owners = await self.store.accounts.briefs(record[8] for record in top)
        currency = await db.fetchval("SELECT currency FROM orgs WHERE pk = $1", org.pk)
        report = {
            "org": org.name,
            "month": gpus.format_month(month),
            "currency": currency,
            "budget": budget,
            "spent": _money(spent),
            "remaining": _money(budget - spent) if budget is not None else None,
            "gpu_hours": round(gpu_hours, 3),
            "projects": [
                {
                    "project": project,
                    "cost": _money(cost),
                    "gpu_hours": round(hours, 3),
                    "runs": count,
                }
                for project, (cost, hours, count) in sorted(
                    projects.items(),
                    key=lambda item: (-item[1][0], -item[1][1], item[0] is None),
                )
            ],
            "excluded": {
                "projects": len(excluded),
                "cost": _money(excluded_cost),
                "gpu_hours": round(excluded_hours, 3),
            },
            "top_runs": [self._top_run(record, owners) for record in top],
            "unpriced": [
                {"gpu_type": gpu_type, "gpu_hours": round(hours, 3)}
                for gpu_type, hours in sorted(unpriced.values(), key=lambda u: -u[1])
            ],
            "unknown_gpu": {"runs": len(unknown), "hours": round(unknown_hours, 3)},
            "warnings": warnings,
        }
        return report, items

    @staticmethod
    def _top_run(record: asyncpg.Record, owners: dict[int, dict[str, Any]]) -> dict[str, Any]:
        _, hours, cost, gpu_type, gpu_count, name, display_name, state, owner, *where = record
        project, entity = where
        return {
            "entity": entity,
            "project": project,
            "id": name,
            "display_name": display_name,
            "state": state,
            "owner": owners.get(owner) if owner is not None else None,
            "gpu_type": gpu_type,
            "gpu_count": gpu_count,
            "gpu_hours": round(hours, 3),
            "cost": _money(cost),
        }

    # ------------------------------------------------------------------ warnings

    async def _warnings(
        self,
        org: Org,
        month: dt.date,
        unpriced: dict[str, list],
        unknown: set[int],
        unknown_hours: float,
    ) -> tuple[list[dict[str, Any]], dict[str, set[str]]]:
        """The month's warnings, and the items each is about: lowercased GPU types for
        ``unpriced``, run pks for ``unknown_gpu``. Nothing under ``WARN_HOURS`` is raised."""
        found: list[tuple[str, set[str], dict[str, Any]]] = []
        types = sorted(
            (entry for entry in unpriced.values() if entry[1] >= self.WARN_HOURS),
            key=lambda entry: -entry[1],
        )
        if types:
            details = {
                "gpu_types": [
                    {"gpu_type": gpu_type, "gpu_hours": round(hours, 3)}
                    for gpu_type, hours in types
                ]
            }
            kinds = {(gpu_type or "").casefold() for gpu_type, _ in types}
            found.append(("unpriced", kinds, details))
        if unknown and unknown_hours >= self.WARN_HOURS:
            details = {"runs": len(unknown), "hours": round(unknown_hours, 3)}
            found.append(("unknown_gpu", {str(pk) for pk in unknown}, details))
        if not found:
            return [], {}
        dismissals = {
            kind: (frozenset(items), by, at)
            for kind, items, by, at in await self.store.db.fetch(
                "SELECT kind, items, dismissed_by, extract(epoch FROM dismissed_at)::float8 "
                "FROM budget_dismissals WHERE org_pk = $1 AND month = $2",
                org.pk,
                month,
            )
        }
        owners = await self.store.accounts.briefs(d[1] for d in dismissals.values())
        warnings = []
        for kind, items, details in found:
            dismissal = dismissals.get(kind)
            if dismissal is not None and not items <= dismissal[0]:
                dismissal = None  # something new: it shows again
            by = dismissal[1] if dismissal is not None else None
            warnings.append(
                {
                    "id": f"{kind}:{gpus.format_month(month)}",
                    "kind": kind,
                    "fingerprint": fingerprint(items),
                    "dismissed": dismissal is not None,
                    "dismissed_by": owners.get(by) if by is not None else None,
                    "dismissed_at": dismissal[2] if dismissal is not None else None,
                    **details,
                }
            )
        return warnings, {kind: items for kind, items, _ in found}

    async def dismissed_warnings(self, org: Org) -> list[dict[str, Any]]:
        """Every warning that is dismissed now, with its month, newest month first. Dismissals
        of warnings that are about something new, or no longer raised, aren't listed."""
        found = []
        for (month,) in await self.store.db.fetch(
            "SELECT DISTINCT month FROM budget_dismissals WHERE org_pk = $1 ORDER BY month DESC",
            org.pk,
        ):
            for warning in (await self.budget(org, month))["warnings"]:
                if warning["dismissed"]:
                    found.append({"month": gpus.format_month(month), **warning})
        return found

    async def warning_items(self, org: Org, month: dt.date, kind: str) -> set[str] | None:
        """What the month's warning of ``kind`` is about now; None if it isn't raised."""
        return (await self._report(org, month))[1].get(kind)

    async def dismiss(
        self, org: Org, month: dt.date, kind: str, items: set[str], user_pk: int
    ) -> None:
        """Dismiss the warning of ``kind`` in ``month`` for everyone in ``org``, for as long
        as it is about no more than ``items``."""
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            await self._lock(conn, org)
            await conn.execute(
                "INSERT INTO budget_dismissals "
                "(org_pk, month, kind, items, dismissed_by, dismissed_at) "
                "SELECT $1, $2, $3, $4, $5, to_timestamp($6) "
                "WHERE EXISTS (SELECT FROM orgs WHERE pk = $1 AND deleted_at IS NULL) "
                "ON CONFLICT (org_pk, month, kind) DO UPDATE SET items = excluded.items, "
                "dismissed_by = excluded.dismissed_by, dismissed_at = excluded.dismissed_at",
                org.pk,
                month,
                kind,
                sorted(items),
                user_pk,
                time.time(),
            )

    async def restore(self, org: Org, month: dt.date, kind: str) -> None:
        """Show a dismissed warning again."""
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            await self._lock(conn, org)
            await conn.execute(
                "DELETE FROM budget_dismissals WHERE org_pk = $1 AND month = $2 AND kind = $3",
                org.pk,
                month,
                kind,
            )

    # ------------------------------------------------------------------ deleted projects

    async def deleted_projects(self, org: Org) -> list[dict[str, Any]]:
        """The deleted projects of ``org`` that used GPU hours, with their spend in each
        month, most recently deleted first."""
        found: dict[int, _Spend] = {}
        for (
            pk,
            name,
            deleted_at,
            excluded_at,
            excluded_by,
            month,
            cost,
            hours,
            runs,
        ) in await self.store.db.fetch(
            """
                SELECT d.pk, d.name, extract(epoch FROM d.deleted_at)::float8,
                       extract(epoch FROM d.excluded_at)::float8, d.excluded_by, u.month,
                       sum(u.seconds / 3600 * u.gpu_count * coalesce(rt.hourly_cost, 0)),
                       sum(u.seconds / 3600 * u.gpu_count), array_agg(DISTINCT u.run_name)
                FROM gpu_usage u
                JOIN deleted_projects d ON d.pk = u.deleted_project_pk
                LEFT JOIN gpu_rates rt
                    ON rt.org_pk = u.org_pk AND lower(rt.gpu_type) = lower(u.gpu_type)
                WHERE u.org_pk = $1 AND u.seconds * u.gpu_count > 0
                GROUP BY d.pk, u.month
                """,
            org.pk,
        ):
            spend = found.setdefault(pk, _Spend(pk, name, deleted_at, excluded_at, excluded_by))
            spend.add(month, runs, cost, hours)
        owners = await self.store.accounts.briefs(s.excluded_by for s in found.values())
        return [
            spend.info(owners)
            for spend in sorted(found.values(), key=lambda d: (-(d.deleted_at or 0.0), -d.pk))
        ]

    async def deleted_project(self, org: Org, pk: int) -> dict[str, Any] | None:
        """One of ``deleted_projects``; None if there's no such deleted project with spend."""
        for item in await self.deleted_projects(org):
            if item["id"] == pk:
                return item
        return None

    async def set_excluded(self, org: Org, pk: int, excluded: bool, user_pk: int) -> None:
        """Leave the spend of the deleted project ``pk`` out of the budget, or count it
        again."""
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            await self._lock(conn, org)
            await conn.execute(
                "UPDATE deleted_projects SET excluded_at = to_timestamp($3), excluded_by = $4 "
                "WHERE pk = $1 AND org_pk = $2",
                pk,
                org.pk,
                time.time() if excluded else None,
                user_pk if excluded else None,
            )


def _money(value: float) -> float:
    return round(value, 2) if math.isfinite(value) else 0.0


@dataclass(eq=False)
class _Spend:
    """A deleted project whose runs used GPUs: its cost, GPU hours and runs, in total and by
    month. Admins can leave its spend out of the budget (``excluded_at``). ``name`` is None for
    projects deleted before names were kept."""

    pk: int
    name: str | None
    deleted_at: float | None
    excluded_at: float | None
    excluded_by: int | None
    cost: float = 0.0
    hours: float = 0.0
    runs: set[str] = field(default_factory=set)
    months: dict[dt.date, tuple[float, float]] = field(default_factory=dict)

    def add(self, month: dt.date, runs: list[str], cost: float, hours: float) -> None:
        self.cost += cost
        self.hours += hours
        self.runs.update(runs)
        before = self.months.get(month, (0.0, 0.0))
        self.months[month] = (before[0] + cost, before[1] + hours)

    def info(self, owners: dict[int, dict[str, Any]]) -> dict[str, Any]:
        return {
            "id": self.pk,
            "name": self.name,
            "deleted_at": self.deleted_at,
            "cost": _money(self.cost),
            "gpu_hours": round(self.hours, 3),
            "runs": len(self.runs),
            "months": [
                {
                    "month": gpus.format_month(month),
                    "cost": _money(cost),
                    "gpu_hours": round(hours, 3),
                }
                for month, (cost, hours) in sorted(self.months.items())
            ],
            "excluded": self.excluded_at is not None,
            "excluded_at": self.excluded_at,
            "excluded_by": owners.get(self.excluded_by) if self.excluded_by else None,
        }


def fingerprint(items: set[str]) -> str:
    """A short digest of what a warning is about: it changes when that does."""
    return hashlib.sha256("\n".join(sorted(items)).encode()).hexdigest()[:16]
