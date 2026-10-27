# 007: budget management

| | |
| --- | --- |
| Status | applied |
| Date | 2026-10-27 |
| Builds on | [003](003-organizations-and-auth.md), [004](004-home-and-budgets.md) |
| Keeps | D2 (small state in memory), D26 (a ledger that outlives runs and projects), D27 |
| Result | 223 tests in `tests/app/` pass |

## 1. Goal

The home page's budget card had three problems:

1. **Deleted projects' spend can't be taken back.** D26 keeps it on purpose, but a project
   deleted because it ran by mistake (or was paid for elsewhere) still eats the budget.
2. **Runs with no GPU time count.** CPU-only runs showed up in the breakdown as
   "0 GPU h · 3 runs", and a few seconds of runs that never uploaded their metadata raised
   "15 runs (0 h) with unknown GPUs."
3. **Warnings can't be dismissed.** A warning an admin has seen and accepted stays forever.

## 2. Design decisions

### D41. Deleted projects become records

Deleting a project deletes its runs (`ON DELETE CASCADE`) and nulls `gpu_usage.project_pk`
(`SET NULL`). Its spend stayed, but every deleted project's spend was one anonymous pile.

- **`deleted_projects`** (migration 0009): `pk`, `org_pk`, `name`, `deleted_at`,
  `excluded_at`, `excluded_by`. `delete_project` inserts one, in the same transaction as the
  delete, only if the project has ledger rows, and points them at it
  (`gpu_usage.deleted_project_pk`). The pk is its own: the project's pk is gone.
- **Old rows.** Ledger rows already orphaned before 0009 lost their project's name. The
  migration puts them in one `deleted_projects` row per org with a null name and time.
- **In memory.** `Org.deleted_projects` by pk; `Usage.deleted_project` points at one.
- **Org deletes** cascade as before: no record is written.

### D42. Exclusion is a flag, reversible, admin-only

`excluded_at` set means the deleted project's spend leaves `spent`, `gpu_hours`, `remaining`
and the breakdown in every month, and is reported under `excluded`. Clearing it counts it
again. Nothing is deleted, so it's always reversible. Only org admins can list deleted
projects and change the flag (`AdminOrgDep`): it changes what everyone's budget says.
Billing's per-type GPU hours still include excluded spend: they describe what ran.

### D43. What "counts" is GPU hours, and warnings have a floor

- **Totals.** A ledger entry with no GPU hours (count 0 or unknown) counts towards no
  project's runs, no GPU type's runs, and no breakdown row. `unknown_gpu` counts entries
  with seconds alive, of runs that still exist (a deleted run's GPUs can't be corrected).
- **Warnings** are raised server-side in `budget.warnings`, with a floor of
  `Budgets.WARN_HOURS = 0.05`: under it the dashboard's `hours()` would show `0`. The floor
  applies to each unpriced GPU type and to the unknown runs together, so a hundred
  two-minute runs still warn. The raw `unpriced` and `unknown_gpu` fields keep reporting
  what's there.

### D44. Dismissals are per org, about a set of items

- **Who.** Org admins, for everyone: a dismissal hides it from members too. Per-user
  dismissals would let each member hide what they've seen, but then admins can't tell
  that the org accepted a warning, and the state would be per user for little gain.
- **Key.** `budget_dismissals (org_pk, month, kind)` with `items text[]`: lowercased GPU types
  for `unpriced`, run pks for `unknown_gpu`. A warning is dismissed while its current items
  are a subset of the dismissed ones. So it reappears when something new is in it, but not
  when it shrinks (a run's GPUs got corrected) or when its hours grow (a run still running).
- **Races.** Each warning carries a `fingerprint` (a hash of its items). Dismissing sends the
  one that was shown; a mismatch answers 409, so nobody dismisses what they haven't seen.
- **Restore.** `DELETE` on the dismissal shows it again.

## 3. How it was built

- **API** (`src/api/home.py`): `PUT`/`DELETE /orgs/{org}/budget/warnings/{kind}:{month}/dismissal`,
  `GET /orgs/{org}/budget/deleted-projects`, `PATCH /orgs/{org}/budget/deleted-projects/{id}`.
  `GET /orgs/{org}/budget` gains `excluded` and `warnings`.
- **Core** (`src/core/budgets.py`): `DeletedProject`, `Dismissal`, `Budgets._report` (the
  budget and each warning's items), `dismiss`, `restore`, `deleted_projects`,
  `set_excluded`, `retire_project`.
- **Dashboard.** The budget card renders `warnings`; admins get a dismiss button on each and
  a "Show dismissed warnings" toggle to bring them back. A line says how much deleted
  projects' spend is left out. The org settings page gets **Spend of deleted projects**,
  with **Leave out** and **Count again** per project. `src/lib/budget.ts` holds the text and
  totals, tested in `budget.spec.ts`.
- **Tests.** `test_budget.py`: zero-hour runs, dismissals (permissions, 409, subset rule,
  restart, restore), exclusion (permissions, totals, restart, reverting). The 0009 migration
  test checks the legacy row.
