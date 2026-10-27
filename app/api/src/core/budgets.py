"""GPU spend: the usage ledger (how long each run was alive per UTC month, with its GPUs),
per-org GPU prices and monthly budgets, and the budget panel computed from them."""

import asyncio
import datetime as dt
import hashlib
import math
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.api.src.core import gpus

if TYPE_CHECKING:
    from app.api.src.core.store import Org, Project, Run, Store


@dataclass(eq=False)
class DeletedProject:
    """A deleted project whose runs used GPUs. Admins can leave its spend out of the budget
    (``excluded_at``). ``name`` is None for projects deleted before names were kept."""

    pk: int
    name: str | None
    deleted_at: float | None
    excluded_at: float | None = None
    excluded_by: int | None = None


@dataclass
class Dismissal:
    """A budget warning dismissed for its org, with the items it was about then."""

    items: frozenset[str]
    dismissed_by: int | None
    dismissed_at: float


@dataclass(eq=False)
class Usage:
    """A run's seconds alive in one month. Kept when the run (``run`` becomes None) or its
    project (``project`` becomes None, ``deleted_project`` set) is deleted, so its spend
    still counts."""

    org: Org
    project: Project | None
    run: Run | None
    run_pk: int
    run_name: str
    month: dt.date
    seconds: float
    gpu_type: str | None
    gpu_count: int | None
    deleted_project: DeletedProject | None = None

    @property
    def gpu_hours(self) -> float:
        return self.seconds / 3600 * (self.gpu_count or 0)

    @property
    def excluded(self) -> bool:
        """Its deleted project's spend is left out of the budget."""
        return self.deleted_project is not None and self.deleted_project.excluded_at is not None


class Budgets:
    """Ledger, prices and budgets of every org, kept in memory (``store.budgets``)."""

    TOP_RUNS = 5
    # Warnings about fewer hours than this aren't raised: they would read "0 h".
    WARN_HOURS = 0.05
    WARNINGS = ("unpriced", "unknown_gpu")

    def __init__(self, store: Store) -> None:
        self.store = store
        self._lock = asyncio.Lock()  # serializes price and budget changes

    async def load(self) -> None:
        store = self.store
        orgs = store.orgs_by_pk
        for org_pk, gpu_type, cost in await store.db.fetch(
            "SELECT org_pk, gpu_type, hourly_cost FROM gpu_rates"
        ):
            orgs[org_pk].rates[gpu_type.casefold()] = (gpu_type, cost)
        for org_pk, month, amount in await store.db.fetch(
            "SELECT org_pk, month, amount FROM org_budgets ORDER BY month"
        ):
            orgs[org_pk].budgets.append((month, amount))
        deleted: dict[int, DeletedProject] = {}
        for pk, org_pk, name, deleted_at, excluded_at, excluded_by in await store.db.fetch(
            "SELECT pk, org_pk, name, extract(epoch FROM deleted_at)::float8, "
            "extract(epoch FROM excluded_at)::float8, excluded_by FROM deleted_projects"
        ):
            deleted[pk] = DeletedProject(pk, name, deleted_at, excluded_at, excluded_by)
            orgs[org_pk].deleted_projects[pk] = deleted[pk]
        for org_pk, month, kind, items, dismissed_by, dismissed_at in await store.db.fetch(
            "SELECT org_pk, month, kind, items, dismissed_by, "
            "extract(epoch FROM dismissed_at)::float8 FROM budget_dismissals"
        ):
            orgs[org_pk].dismissals[(month, kind)] = Dismissal(
                frozenset(items), dismissed_by, dismissed_at
            )
        for record in await store.db.fetch(
            "SELECT org_pk, project_pk, run_pk, run_name, month, seconds, gpu_type, gpu_count, "
            "deleted_project_pk FROM gpu_usage"
        ):
            org_pk, project_pk, run_pk, run_name, month, seconds, gpu_type, gpu_count, gone = record
            run = store.runs_by_pk.get(run_pk) if run_pk is not None else None
            project = store.projects_by_pk.get(project_pk) if project_pk is not None else None
            usage = Usage(
                orgs[org_pk], project, run, run_pk or 0, run_name, month, seconds, gpu_type,
                gpu_count, deleted.get(gone) if gone is not None else None,
            )  # fmt: skip
            self._add(usage)
        for run in store.runs_by_pk.values():  # their table rows were filled before this
            if run.usage and run.gpu_count is not None:
                run.project.table.set(run.row, "gpu_hours", run.gpu_hours)

    @staticmethod
    def _add(usage: Usage) -> None:
        usage.org.usage.setdefault(usage.month, []).append(usage)
        if usage.run is not None:
            usage.run.usage[usage.month] = usage

    # ------------------------------------------------------------------ the ledger

    def accrual(self, run: Run, now: float) -> list[tuple[dt.date, float]]:
        """Seconds to add for a heartbeat at ``now``: the time since the previous one, unless
        the run was silent longer than ``crash_timeout`` (a crash, or preempted and resumed)."""
        since = run.heartbeat_at
        if not 0 < now - since <= self.store.settings.crash_timeout:
            return []
        return gpus.split_by_month(since, now)

    def accrue(self, run: Run, parts: list[tuple[dt.date, float]]) -> None:
        """Record in memory what ``file_stream`` added to the ledger."""
        for month, seconds in parts:
            usage = run.usage.get(month)
            if usage is None:
                usage = Usage(
                    run.project.org, run.project, run, run.pk, run.name, month, 0.0,
                    run.gpu_type, run.gpu_count,
                )  # fmt: skip
                self._add(usage)
            usage.seconds += seconds

    @staticmethod
    def sync_gpus(run: Run) -> None:
        """The run's ledger entries take its (new) effective GPUs."""
        for usage in run.usage.values():
            usage.gpu_type, usage.gpu_count = run.gpu_type, run.gpu_count

    @staticmethod
    def moved(run: Run) -> None:
        for usage in run.usage.values():
            usage.project = run.project

    @staticmethod
    def forget_run(run: Run) -> None:
        for usage in run.usage.values():
            usage.run = None

    @staticmethod
    def retire_project(project: Project, deleted: DeletedProject) -> None:
        """The ledger entries of ``project``, deleted, belong to ``deleted`` from now on."""
        org = project.org
        org.deleted_projects[deleted.pk] = deleted
        for entries in org.usage.values():
            for usage in entries:
                if usage.project is project:
                    usage.deleted_project = deleted

    @staticmethod
    def forget_project(project: Project) -> None:
        for entries in project.org.usage.values():
            for usage in entries:
                if usage.project is project:
                    usage.project = None

    # ------------------------------------------------------------------ prices and budgets

    @staticmethod
    def rate(org: Org, gpu_type: str | None) -> float | None:
        found = org.rates.get(gpu_type.casefold()) if gpu_type else None
        return found[1] if found else None

    def cost(self, org: Org, usage: Usage) -> float:
        return usage.gpu_hours * (self.rate(org, usage.gpu_type) or 0.0)

    def run_cost(self, run: Run) -> float:
        org = run.project.org
        return sum(self.cost(org, usage) for usage in run.usage.values())

    @staticmethod
    def budget_for(org: Org, month: dt.date) -> float | None:
        amount = None
        for start, value in org.budgets:  # sorted by month
            if start > month:
                break
            amount = value
        return amount

    async def update(
        self,
        org: Org,
        fields: dict[str, Any],
        rates: dict[str, float | None],
    ) -> None:
        """Change an org's currency or monthly budget (the keys present in ``fields``; the
        budget applies from the current month on) and merge ``rates`` (None removes one)."""
        store = self.store
        month = gpus.month_of(time.time())
        async with self._lock:
            if org.deleted:
                return
            async with store.db.pool.acquire() as conn, conn.transaction():
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
            if "monthly_budget" in fields:
                budgets = [(start, value) for start, value in org.budgets if start != month]
                org.budgets = sorted([*budgets, (month, fields["monthly_budget"])])
            for gpu_type, cost in rates.items():
                if cost is None:
                    org.rates.pop(gpu_type.casefold(), None)
                else:
                    org.rates[gpu_type.casefold()] = (gpu_type, cost)

    # ------------------------------------------------------------------ reports

    def owner(self, user_pk: int | None) -> dict[str, Any] | None:
        accounts = self.store.accounts
        user = accounts.users.get(user_pk) if user_pk is not None else None
        return accounts.brief(user) if user else None

    def billing(self, org: Org) -> dict[str, Any]:
        """An org's prices and budgets, with every GPU type its runs used."""
        hours: dict[str, float] = defaultdict(float)
        runs: dict[str, set[int]] = defaultdict(set)
        names: dict[str, str] = {}
        for entries in org.usage.values():
            for usage in entries:
                if usage.gpu_type and usage.gpu_hours > 0:  # runs with no GPU time don't count
                    key = usage.gpu_type.casefold()
                    names.setdefault(key, usage.gpu_type)
                    hours[key] += usage.gpu_hours
                    runs[key].add(usage.run_pk or -id(usage))  # 0: deleted before a restart
        for project in self.store._org_projects(org):
            for run in project.runs:
                if run.gpu_type and run.gpu_count:  # listed to be priced before they accrue
                    names.setdefault(run.gpu_type.casefold(), run.gpu_type)
        for key, (gpu_type, _) in org.rates.items():
            names.setdefault(key, gpu_type)
        month = gpus.month_of(time.time())
        return {
            "currency": org.currency,
            "monthly_budget": self.budget_for(org, month),
            "budgets": [
                {"month": gpus.format_month(start), "amount": amount}
                for start, amount in org.budgets
            ],
            "rates": [
                {"gpu_type": gpu_type, "hourly_cost": cost}
                for gpu_type, cost in sorted(org.rates.values(), key=lambda r: r[0].casefold())
            ],
            "gpu_types": [
                {
                    "gpu_type": names[key],
                    "gpu_hours": round(hours[key], 3),
                    "runs": len(runs[key]),
                    "hourly_cost": org.rates[key][1] if key in org.rates else None,
                }
                for key in sorted(names, key=lambda k: (-hours[k], k))
            ],
        }

    def budget(self, org: Org, month: dt.date) -> dict[str, Any]:
        """Spend of ``org`` in ``month`` against its budget, by project, with its top runs and
        its warnings. Entries without GPU hours count towards nothing, and the spend of deleted
        projects an admin excluded is reported apart."""
        return self._report(org, month)[0]

    def _report(self, org: Org, month: dt.date) -> tuple[dict[str, Any], dict[str, set[str]]]:
        """The budget, and what each of its warnings is about (see ``_warnings``)."""
        spent = gpu_hours = excluded_cost = excluded_hours = 0.0
        excluded: set[DeletedProject] = set()
        projects: dict[Project | None, list] = defaultdict(lambda: [0.0, 0.0, 0])
        unpriced: dict[str, list] = {}
        unknown: set[int] = set()
        unknown_seconds = 0.0
        top: list[tuple[float, float, Usage]] = []
        for usage in org.usage.get(month, ()):
            rate = self.rate(org, usage.gpu_type)
            hours = usage.gpu_hours
            cost = hours * (rate or 0.0)
            if usage.excluded:
                assert usage.deleted_project is not None
                if hours > 0:
                    excluded.add(usage.deleted_project)
                    excluded_cost += cost
                    excluded_hours += hours
                continue
            # Only runs that still exist: the GPUs of deleted ones can't be corrected.
            if usage.gpu_count is None and usage.run is not None and usage.seconds > 0:
                unknown.add(usage.run.pk)
                unknown_seconds += usage.seconds
            if hours <= 0:
                continue
            if rate is None:
                key = (usage.gpu_type or "").casefold()
                entry = unpriced.setdefault(key, [usage.gpu_type, 0.0])
                entry[1] += hours
            spent += cost
            gpu_hours += hours
            totals = projects[usage.project]
            totals[0] += cost
            totals[1] += hours
            totals[2] += 1
            if usage.run is not None:
                top.append((cost, hours, usage))
        top.sort(key=lambda item: (-item[0], -item[1], -item[2].run_pk))
        budget = self.budget_for(org, month)
        unknown_hours = unknown_seconds / 3600
        warnings, items = self._warnings(org, month, unpriced, unknown, unknown_hours)
        report = {
            "org": org.name,
            "month": gpus.format_month(month),
            "currency": org.currency,
            "budget": budget,
            "spent": _money(spent),
            "remaining": _money(budget - spent) if budget is not None else None,
            "gpu_hours": round(gpu_hours, 3),
            "projects": [
                {
                    "project": project.name if project is not None else None,
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
            "top_runs": [
                self._top_run(usage, cost, hours) for cost, hours, usage in top[: self.TOP_RUNS]
            ],
            "unpriced": [
                {"gpu_type": gpu_type, "gpu_hours": round(hours, 3)}
                for gpu_type, hours in sorted(unpriced.values(), key=lambda u: -u[1])
            ],
            "unknown_gpu": {"runs": len(unknown), "hours": round(unknown_hours, 3)},
            "warnings": warnings,
        }
        return report, items

    # ------------------------------------------------------------------ warnings

    def _warnings(
        self,
        org: Org,
        month: dt.date,
        unpriced: dict[str, list],
        unknown: set[int],
        unknown_hours: float,
    ) -> tuple[list[dict[str, Any]], dict[str, set[str]]]:
        """The month's warnings, and the items each is about: lowercased GPU types for
        ``unpriced``, run pks for ``unknown_gpu``. Nothing under ``WARN_HOURS`` is raised."""
        warnings, items = [], {}
        types = sorted(
            (entry for entry in unpriced.values() if entry[1] >= self.WARN_HOURS),
            key=lambda entry: -entry[1],
        )
        if types:
            items["unpriced"] = {(gpu_type or "").casefold() for gpu_type, _ in types}
            details = {
                "gpu_types": [
                    {"gpu_type": gpu_type, "gpu_hours": round(hours, 3)}
                    for gpu_type, hours in types
                ]
            }
            warnings.append(self._warning(org, month, "unpriced", items["unpriced"], details))
        if unknown and unknown_hours >= self.WARN_HOURS:
            items["unknown_gpu"] = {str(pk) for pk in unknown}
            details = {"runs": len(unknown), "hours": round(unknown_hours, 3)}
            warnings.append(self._warning(org, month, "unknown_gpu", items["unknown_gpu"], details))
        return warnings, items

    def _warning(
        self, org: Org, month: dt.date, kind: str, items: set[str], details: dict[str, Any]
    ) -> dict[str, Any]:
        dismissal = org.dismissals.get((month, kind))
        if dismissal is not None and not items <= dismissal.items:
            dismissal = None  # something new: it shows again
        return {
            "id": f"{kind}:{gpus.format_month(month)}",
            "kind": kind,
            "fingerprint": fingerprint(items),
            "dismissed": dismissal is not None,
            "dismissed_by": self.owner(dismissal.dismissed_by) if dismissal else None,
            "dismissed_at": dismissal.dismissed_at if dismissal else None,
            **details,
        }

    def dismissed_warnings(self, org: Org) -> list[dict[str, Any]]:
        """Every warning that is dismissed now, with its month, newest month first. Dismissals
        of warnings that are about something new, or no longer raised, aren't listed."""
        found = []
        for month in sorted({month for month, _ in org.dismissals}, reverse=True):
            for warning in self.budget(org, month)["warnings"]:
                if warning["dismissed"]:
                    found.append({"month": gpus.format_month(month), **warning})
        return found

    def warning_items(self, org: Org, month: dt.date, kind: str) -> set[str] | None:
        """What the month's warning of ``kind`` is about now; None if it isn't raised."""
        return self._report(org, month)[1].get(kind)

    async def dismiss(
        self, org: Org, month: dt.date, kind: str, items: set[str], user_pk: int
    ) -> None:
        """Dismiss the warning of ``kind`` in ``month`` for everyone in ``org``, for as long
        as it is about no more than ``items``."""
        now = time.time()
        async with self._lock:
            if org.deleted:
                return
            await self.store.db.execute(
                "INSERT INTO budget_dismissals "
                "(org_pk, month, kind, items, dismissed_by, dismissed_at) "
                "VALUES ($1, $2, $3, $4, $5, to_timestamp($6)) "
                "ON CONFLICT (org_pk, month, kind) DO UPDATE SET items = excluded.items, "
                "dismissed_by = excluded.dismissed_by, dismissed_at = excluded.dismissed_at",
                org.pk,
                month,
                kind,
                sorted(items),
                user_pk,
                now,
            )
            org.dismissals[(month, kind)] = Dismissal(frozenset(items), user_pk, now)

    async def restore(self, org: Org, month: dt.date, kind: str) -> None:
        """Show a dismissed warning again."""
        async with self._lock:
            if org.deleted:
                return
            await self.store.db.execute(
                "DELETE FROM budget_dismissals WHERE org_pk = $1 AND month = $2 AND kind = $3",
                org.pk,
                month,
                kind,
            )
            org.dismissals.pop((month, kind), None)

    # ------------------------------------------------------------------ deleted projects

    def deleted_projects(self, org: Org) -> list[dict[str, Any]]:
        """The deleted projects of ``org`` that used GPU hours, with their spend in each
        month, most recently deleted first."""
        totals: dict[DeletedProject, _Spend] = {}
        for month, entries in org.usage.items():
            for usage in entries:
                deleted = usage.deleted_project
                hours = usage.gpu_hours
                if deleted is None or hours <= 0:
                    continue
                spend = totals.setdefault(deleted, _Spend())
                spend.add(month, usage.run_name, self.cost(org, usage), hours)
        return [
            self._deleted_project(deleted, totals[deleted])
            for deleted in sorted(totals, key=lambda d: (-(d.deleted_at or 0.0), -d.pk))
        ]

    def _deleted_project(self, deleted: DeletedProject, spend: _Spend) -> dict[str, Any]:
        return {
            "id": deleted.pk,
            "name": deleted.name,
            "deleted_at": deleted.deleted_at,
            "cost": _money(spend.cost),
            "gpu_hours": round(spend.hours, 3),
            "runs": len(spend.runs),
            "months": [
                {
                    "month": gpus.format_month(month),
                    "cost": _money(cost),
                    "gpu_hours": round(hours, 3),
                }
                for month, (cost, hours) in sorted(spend.months.items())
            ],
            "excluded": deleted.excluded_at is not None,
            "excluded_at": deleted.excluded_at,
            "excluded_by": self.owner(deleted.excluded_by),
        }

    def deleted_project(self, org: Org, pk: int) -> dict[str, Any] | None:
        """One of ``deleted_projects``; None if there's no such deleted project with spend."""
        for item in self.deleted_projects(org):
            if item["id"] == pk:
                return item
        return None

    async def set_excluded(
        self, org: Org, deleted: DeletedProject, excluded: bool, user_pk: int
    ) -> None:
        """Leave the spend of a deleted project out of the budget, or count it again."""
        now = time.time() if excluded else None
        by = user_pk if excluded else None
        async with self._lock:
            if org.deleted:
                return
            await self.store.db.execute(
                "UPDATE deleted_projects SET excluded_at = to_timestamp($2), excluded_by = $3 "
                "WHERE pk = $1",
                deleted.pk,
                now,
                by,
            )
            deleted.excluded_at, deleted.excluded_by = now, by

    def _top_run(self, usage: Usage, cost: float, hours: float) -> dict[str, Any]:
        run = usage.run
        assert run is not None
        return {
            "entity": run.entity,
            "project": run.project.name,
            "id": run.name,
            "display_name": run.display_name,
            "state": run.state,
            "owner": self.owner(run.owner),
            "gpu_type": usage.gpu_type,
            "gpu_count": usage.gpu_count,
            "gpu_hours": round(hours, 3),
            "cost": _money(cost),
        }


def _money(value: float) -> float:
    return round(value, 2) if math.isfinite(value) else 0.0


class _Spend:
    """Cost, GPU hours and runs of a deleted project, in total and by month."""

    def __init__(self) -> None:
        self.cost = 0.0
        self.hours = 0.0
        self.runs: set[str] = set()
        self.months: dict[dt.date, tuple[float, float]] = {}

    def add(self, month: dt.date, run: str, cost: float, hours: float) -> None:
        self.cost += cost
        self.hours += hours
        self.runs.add(run)
        before = self.months.get(month, (0.0, 0.0))
        self.months[month] = (before[0] + cost, before[1] + hours)


def fingerprint(items: set[str]) -> str:
    """A short digest of what a warning is about: it changes when that does."""
    return hashlib.sha256("\n".join(sorted(items)).encode()).hexdigest()[:16]
