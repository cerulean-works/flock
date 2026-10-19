"""GPU spend: the usage ledger (how long each run was alive per UTC month, with its GPUs),
per-org GPU prices and monthly budgets, and the budget panel computed from them."""

import asyncio
import datetime as dt
import math
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.api.src.core import gpus

if TYPE_CHECKING:
    from app.api.src.core.store import Org, Project, Run, Store


@dataclass(eq=False)
class Usage:
    """A run's seconds alive in one month. Kept when the run (``run`` becomes None) or its
    project (``project`` becomes None) is deleted, so its spend still counts."""

    org: Org
    project: Project | None
    run: Run | None
    run_pk: int
    run_name: str
    month: dt.date
    seconds: float
    gpu_type: str | None
    gpu_count: int | None

    @property
    def gpu_hours(self) -> float:
        return self.seconds / 3600 * (self.gpu_count or 0)


class Budgets:
    """Ledger, prices and budgets of every org, kept in memory (``store.budgets``)."""

    TOP_RUNS = 5

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
        for record in await store.db.fetch(
            "SELECT org_pk, project_pk, run_pk, run_name, month, seconds, gpu_type, gpu_count "
            "FROM gpu_usage"
        ):
            org_pk, project_pk, run_pk, run_name, month, seconds, gpu_type, gpu_count = record
            run = store.runs_by_pk.get(run_pk) if run_pk is not None else None
            project = store.projects_by_pk.get(project_pk) if project_pk is not None else None
            usage = Usage(
                orgs[org_pk], project, run, run_pk or 0, run_name, month, seconds, gpu_type,
                gpu_count,
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
                if usage.gpu_type and usage.gpu_count:
                    key = usage.gpu_type.casefold()
                    names.setdefault(key, usage.gpu_type)
                    hours[key] += usage.gpu_hours
                    runs[key].add(usage.run_pk)
        for project in self.store._org_projects(org):
            for run in project.runs:
                if run.gpu_type and run.gpu_count:
                    key = run.gpu_type.casefold()
                    names.setdefault(key, run.gpu_type)
                    runs[key].add(run.pk)
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
        """Spend of ``org`` in ``month`` against its budget, by project, with its top runs."""
        spent = gpu_hours = 0.0
        projects: dict[Project | None, list[float]] = defaultdict(lambda: [0.0, 0.0, 0])
        unpriced: dict[str, list] = {}
        unknown_runs, unknown_seconds = 0, 0.0
        top: list[tuple[float, float, Usage]] = []
        for usage in org.usage.get(month, ()):
            if usage.gpu_count is None:
                unknown_runs += 1
                unknown_seconds += usage.seconds
            rate = self.rate(org, usage.gpu_type)
            hours = usage.gpu_hours
            cost = hours * (rate or 0.0)
            if rate is None and hours > 0:
                key = (usage.gpu_type or "").casefold()
                entry = unpriced.setdefault(key, [usage.gpu_type, 0.0])
                entry[1] += hours
            spent += cost
            gpu_hours += hours
            totals = projects[usage.project]
            totals[0] += cost
            totals[1] += hours
            totals[2] += 1
            if usage.run is not None and (cost > 0 or hours > 0):
                top.append((cost, hours, usage))
        top.sort(key=lambda item: (-item[0], -item[1], -item[2].run_pk))
        budget = self.budget_for(org, month)
        return {
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
            "top_runs": [
                self._top_run(usage, cost, hours) for cost, hours, usage in top[: self.TOP_RUNS]
            ],
            "unpriced": [
                {"gpu_type": gpu_type, "gpu_hours": round(hours, 3)}
                for gpu_type, hours in sorted(unpriced.values(), key=lambda u: -u[1])
            ],
            "unknown_gpu": {"runs": unknown_runs, "hours": round(unknown_seconds / 3600, 3)},
        }

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
