# 007: budget management

| | |
| --- | --- |
| Status | applied |
| Date | 2026-10-27 |
| Builds on | [003](003-organizations-and-auth.md), [004](004-home-and-budgets.md) |
| Keeps | D2 (small state in memory), D26 (a ledger that outlives runs and projects), D27 |
| Result | 224 tests in `tests/app/` pass |

## 1. Goal

The home page's budget card had three problems:

1. **Deleted projects' spend can't be taken back.** D26 keeps it on purpose, but a project
   deleted because it ran by mistake (or was paid for elsewhere) still eats the budget.
2. **Runs with no GPU time count.** CPU-only runs showed up in the breakdown as
   "0 GPU h · 3 runs", and a few seconds of runs that never uploaded their metadata raised
   "15 runs (0 h) with unknown GPUs."
3. **Warnings are noisy.** Everyone sees them on the home page, and one an admin has
   seen and accepted stays forever.

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
- **Warnings** are computed server-side, with a floor of `Budgets.WARN_HOURS = 0.05`: under
  it the dashboard's `hours()` would show `0`. The floor applies to each unpriced GPU type and
  to the unknown runs together, so a hundred two-minute runs still warn.

### D44. Warnings are for admins, in the org's settings

Only admins can act on a warning (set a price, correct a run's GPUs, dismiss it), so only
admins see them.

- **API.** `GET /orgs/{org}/budget` carries no `warnings`, and leaves out `unpriced` and
  `unknown_gpu` for members (nothing in the member UI reads them). Warnings are served by an
  admin-only `GET /orgs/{org}/budget/warnings`: the month's active warnings, plus every
  dismissed warning of any month that is still dismissed.
- **Dashboard.** The home budget card shows no warnings for anyone. The org settings page has
  **Budget warnings**: the current month's active warnings with a × to dismiss, and a
  dismissed list (month, who and when) with **Show again**.

### D45. Dismissals are per org, about a set of items

- **Who.** Org admins, for the whole org. Per-user dismissals would let each admin hide what
  they've seen, but then nobody could tell that the org accepted a warning.
- **Key.** `budget_dismissals (org_pk, month, kind)` with `items text[]`: lowercased GPU types
  for `unpriced`, run pks for `unknown_gpu`. A warning is dismissed while its current items
  are a subset of the dismissed ones. So it's active again when something new is in it, but
  not when it shrinks (a run's GPUs got corrected) or when its hours grow (a run still
  running). A dismissal whose warning is no longer raised isn't listed, and applies again if
  the warning comes back with the same items.
- **Races.** Each warning carries a `fingerprint` (a hash of its items). Dismissing sends the
  one that was shown; a mismatch answers 409, so nobody dismisses what they haven't seen.
- **Restore.** `DELETE` on the dismissal shows it again.

## 3. How it was built

- **API** (`src/api/home.py`): `GET /orgs/{org}/budget/warnings`,
  `PUT`/`DELETE /orgs/{org}/budget/warnings/{kind}:{month}/dismissal`,
  `GET /orgs/{org}/budget/deleted-projects`, `PATCH /orgs/{org}/budget/deleted-projects/{id}`.
  `GET /orgs/{org}/budget` gains `excluded`, and `unpriced` and `unknown_gpu` become
  admin-only.
- **Core** (`src/core/budgets.py`): `DeletedProject`, `Dismissal`, `Budgets._report` (the
  budget and each warning's items), `dismissed_warnings`, `dismiss`, `restore`,
  `deleted_projects`, `set_excluded`, `retire_project`.
- **Dashboard.** The home budget card says how much deleted projects' spend is left out. The
  org settings page gets **Budget warnings** (`BudgetWarnings.svelte`) and **Spend of deleted
  projects** (`DeletedProjectsSpend.svelte`, with **Leave out** and **Count again** per
  project). `src/lib/budget.ts` holds the text and totals, tested in `budget.spec.ts`.
- **Tests.** `test_budget.py`: zero-hour runs, warnings and dismissals (admin-only, 409, the
  subset rule, the dismissed list, restart, restore), the member budget without GPU details,
  and exclusion (permissions, totals, restart, reverting). The 0009 migration test checks the
  legacy row.
