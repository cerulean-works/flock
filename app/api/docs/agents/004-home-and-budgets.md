# 004: home feed, GPU tracking and budgets

| | |
| --- | --- |
| Status | applied |
| Date | 2026-10-18 |
| Builds on | [001](001-wandb-compatible-flock.md), [002](002-projects-and-crud.md), [003](003-organizations-and-auth.md) |
| Keeps | D2 (in-memory working set, one server per database), D18–D23 |
| Result | 202 tests in `tests/app/` pass; see [section 5](#5-how-it-was-actually-built) for where the build departed from the plan |

## 1. Goal

flock-ui gets a home page shaped like GitHub's. A central feed shows updates across every
project the user can see. A right-hand panel shows the org's budget: what's left this month, the
cost of each project, and the five most expensive runs. Flock serves the data, and the
dashboard does the rest.

This stage adds:

1. **The home feed.** It's derived from runs and projects that already exist (run started,
   finished, failed or crashed; project created). There's no new activity table.
2. **GPU tracking.** Flock reads each run's GPU type and count from the
   `wandb-metadata.json` the wandb SDK uploads. An owner or admin can override them.
3. **Prices and budgets.** Org admins record an hourly cost per GPU type (for example, an H200
   hour costs 60) and a monthly budget. The budget resets at the start of each UTC month.
4. **The budget panel.** For a month: spent, remaining, cost by project, and the top 5 runs.

**Out of scope:**
- activity the runs table can't show: deletes, renames, membership changes and comments in the
  feed;
- live feed updates (the dashboard polls);
- invoices and real billing;
- CPU and memory pricing;
- alerts when a budget runs out.

**Constraint:** every read is served from memory (D2). The feed and the budget panel answer in
milliseconds.

---

## 2. Design decisions

The numbering continues from 003.

### D25. GPU type and count: detected, with an override

Migration `0005_gpus_and_budgets.sql` adds four columns to `runs`:
- `gpu_type` and `gpu_count`: what was detected;
- `gpu_type_override` and `gpu_count_override`: what a person set.

The **effective** value is the override if one is set, and otherwise the detected value.

**What a NULL means:**
- a `gpu_count` of NULL means the GPUs are unknown (no metadata has arrived yet);
- `0` means the run has no GPU. A file with no GPU info also records `0`, so it isn't scanned
  again.

**Detection.** The SDK uploads `wandb-metadata.json` as an ordinary run file. When
`Store.save_file` stores a file with that name, it parses it (up to 1 MB). The pure function
`gpus.detect(metadata) -> (type | None, count)` reads, in order:

| Source | Type from | Count from |
| --- | --- | --- |
| NVIDIA | `gpu`, else `gpu_nvidia[0].name` | `gpu_count`, else `len(gpu_nvidia)` |
| AMD | `gpu_amd[0].series` or `.model` | `len(gpu_amd)` |
| TPU | `tpu.name` | `tpu.count` |
| Trainium | `trainium.name` | `trainium.neuronDeviceCount` |

`gpus.normalize(name)` reduces a device name to a price-table key:
- "NVIDIA H100 80GB HBM3" becomes `H100`;
- "NVIDIA A100-SXM4-80GB" becomes `A100`;
- "NVIDIA GeForce RTX 4090" becomes `RTX 4090`;
- "AMD Instinct MI300X" becomes `MI300X`.

An unknown name loses its vendor prefix and memory suffix and is otherwise kept as it is.

**Backfill.** On startup, runs uploaded before this stage have a metadata file but no detected
count. `Store.open` scans those in a thread.

**Override.** `PATCH .../runs/{run}` and the bulk `runs/update` accept `gpu_type` (1–64
characters) and `gpu_count` (0–1024). Sending `null` clears the override, so the detected value
applies again. The usual ownership rule applies: owners and admins can change it.

**On the run.**
- `gpu_type`, `gpu_count` and `gpu_hours` (the effective count × hours the run was alive) are
  run attributes, so they're table columns that can be filtered and sorted.
- The run detail also returns `gpu_detected: {type, count}`, `runtime` (seconds the run was
  alive), and `cost` (with the org's current prices).

### D26. A usage ledger that outlives runs

```
gpu_usage(pk, org_pk → orgs ON DELETE CASCADE, project_pk → projects ON DELETE SET NULL,
          run_pk → runs ON DELETE SET NULL, run_name, month date, seconds,
          gpu_type, gpu_count, UNIQUE (run_pk, month))
```

Each row holds the wall-clock seconds one run was alive during one UTC month.

**Keeping rows priceable.** A row also stores the run's effective GPU type and count. These are
kept in step when the GPUs are detected or overridden, and the project is kept in step when a
run moves. That way a row can still be priced after its run or project is deleted.

**Deletes don't refund.** When a run is deleted, its spend stays in the org's budget, counted
under its project. When a project is deleted, its spend is grouped as "deleted projects".

**Accrual.** Every `file_stream` request is a heartbeat. In the same statement that updates
`heartbeat_at`, the ledger adds `now - previous heartbeat_at`:
- the delta counts only when it is at most `crash_timeout`, so the time between a preemption
  and a resume isn't billed;
- a delta that crosses a month boundary is split by `gpus.split_by_month`;
- the write is `INSERT ... ON CONFLICT (run_pk, month) DO UPDATE SET seconds = seconds + ...`;
- it is one more CTE in the existing statement, so it adds no extra round trip.

**In memory.**
- `Usage` objects are indexed by `Run.usage[month]` and `Org.usage[month]`.
- When a run or project is deleted, its entries are detached (`run` or `project` set to None)
  but kept.

**Backfill.** Migration 0005 fills the ledger for existing runs from `created_at` to
`heartbeat_at`, split by month with `generate_series`. Resume gaps in that older data can't be
told apart from running time.

### D27. Prices and budgets per org

- **Currency.** `orgs.currency` (default `USD`) is three uppercase letters. It's a display label
  only; nothing is converted.
- **Prices.** `gpu_rates(org_pk, gpu_type, hourly_cost)` stores the cost of one GPU for one
  hour. GPU types are matched case-insensitively, through a unique index on
  `(org_pk, lower(gpu_type))`.
- **Budgets.**
  - `org_budgets(org_pk, month, amount)` holds the budget that applies from `month` onward. The
    budget for month M is the latest row with month ≤ M.
  - Setting the budget writes the current month's row, so earlier months keep the budget they
    had. A NULL amount means "no budget".

**Cost** is the sum, over ledger rows, of `seconds / 3600 × gpu_count × price(gpu_type)`, using
the **current** prices:
- changing a price re-prices past months as well;
- a GPU type with no price costs 0 and is listed as `unpriced`, so the panel can prompt an admin
  to set it;
- runs whose GPUs are unknown are counted under `unknown_gpu`.

**Who can do what.** Members read prices and budgets, since the home panel shows them to
everyone in the org. Only admins change them.

### D28. The API

All routes are under `/api/v1` and require a signed-in user.

**`GET /feed`**: the home feed, newest first.

| Query | Meaning |
| --- | --- |
| `org` | only this org (default: every org the user belongs to; all orgs for a super admin) |
| `project` | only this project (requires `org`) |
| `owner` | `me` or a user id: only that user's runs (`project_created` is dropped) |
| `kinds` | comma-separated subset of the kinds below |
| `cursor` | the `next` value of the previous page |
| `limit` | 1–100, default 30 |

Item kinds:

| Kind | `at` |
| --- | --- |
| `run_started` | the run's `created_at` |
| `run_finished`, `run_failed`, `run_crashed` | the run's last heartbeat |
| `project_created` | the project's `created_at` |

```json
{
  "items": [
    {
      "id": "run_finished:812",
      "kind": "run_finished",
      "at": 1760781234.5,
      "org": "flock",
      "project": "llm-pretrain",
      "actor": {"id": 3, "name": "Alice", "email": "alice@lab.test"},
      "run": {
        "id": "k2x9a1bq", "display_name": "fresh-sun-12", "state": "finished",
        "gpu_type": "H200", "gpu_count": 8, "runtime": 5400.0,
        "tags": ["baseline"], "group": null, "job_type": "train"
      }
    },
    {
      "id": "project_created:4",
      "kind": "project_created",
      "at": 1760700000.0,
      "org": "flock",
      "project": "llm-pretrain",
      "actor": null,
      "description": "..."
    }
  ],
  "next": "opaque cursor, or null on the last page"
}
```

- `actor` is the run's owner. It's null for runs from before accounts existed, and for
  `project_created` (projects don't record who created them).
- The feed is computed per project, vectorized over the run table's `created_at`,
  `heartbeat_at` and `state` columns, with `argpartition` taking the top k. The newest
  runs are the `run_started` items, so the dashboard filters with `kinds=run_started`.

**`GET /orgs/{org}/billing`** (members): settings for the org.

```json
{
  "currency": "USD",
  "monthly_budget": 20000.0,
  "budgets": [{"month": "2026-09", "amount": 15000.0}, {"month": "2026-10", "amount": 20000.0}],
  "rates": [{"gpu_type": "H200", "hourly_cost": 60.0}],
  "gpu_types": [
    {"gpu_type": "H200", "gpu_hours": 1520.5, "runs": 31, "hourly_cost": 60.0},
    {"gpu_type": "A100", "gpu_hours": 88.0, "runs": 4, "hourly_cost": null}
  ]
}
```

`gpu_types` lists every type seen in the org's runs or ledger, so the settings form can offer
the types that still have no price.

**`PATCH /orgs/{org}/billing`** (admins) takes a body where every key is optional:

```json
{"monthly_budget": 20000, "currency": "USD", "rates": {"H200": 60, "A100": null}}
```

- `rates` are merged into the existing prices, and `null` removes one.
- `monthly_budget` applies from the current month onward, and `null` removes the budget.
- The response is the same as the GET.

**`GET /orgs/{org}/budget?month=YYYY-MM`** (members): the home panel. `month` defaults to the
current UTC month.

```json
{
  "org": "flock",
  "month": "2026-10",
  "currency": "USD",
  "budget": 20000.0,
  "spent": 12345.67,
  "remaining": 7654.33,
  "gpu_hours": 205.8,
  "projects": [
    {"project": "llm-pretrain", "cost": 11000.0, "gpu_hours": 183.3, "runs": 12},
    {"project": null, "cost": 1345.67, "gpu_hours": 22.5, "runs": 3}
  ],
  "top_runs": [
    {
      "entity": "flock", "project": "llm-pretrain", "id": "k2x9a1bq",
      "display_name": "fresh-sun-12", "state": "finished",
      "owner": {"id": 3, "name": "Alice", "email": "alice@lab.test"},
      "gpu_type": "H200", "gpu_count": 8, "gpu_hours": 12.0, "cost": 720.0
    }
  ],
  "unpriced": [{"gpu_type": "A100", "gpu_hours": 8.0}],
  "unknown_gpu": {"runs": 2, "hours": 3.5}
}
```

- **Fields that can be null:** `budget` and `remaining` are null when no budget is set.
  `remaining` can be negative.
- **`projects`** is sorted by cost, then GPU hours. The `null` project collects deleted
  projects.
- **`top_runs`** holds the 5 most expensive runs that still exist, by cost and then GPU hours.
  `gpu_hours` and `cost` are for that month.
- **`unknown_gpu`** counts runs with no GPU info, and their wall-clock hours.
- **Cost of computing it:** the endpoint reads only the month's ledger entries for that org.

### D29. Known gaps after this stage

- **Prices aren't versioned.** Changing a price re-prices history.
- **Months are UTC.** There's no per-org time zone.
- **The ledger backfill counts resume gaps** for runs logged before 0005.
- **`gpu_count` is what the machine reported.** NVML sees every GPU, not just
  `CUDA_VISIBLE_DEVICES`, and a multi-node run reports one node. The override corrects both.
- **Members can override GPU info on their own runs,** which changes the org's spend. The
  override is visible in the run detail (`gpu_detected` and the effective values).
- **The feed has gaps.** It has no deletes, renames or membership events. It isn't live, and
  deleted runs disappear from it.
- **`top_runs` leaves out deleted runs,** whose spend still counts.

---

## 3. Plan

### Step 1. Migration 0005

Run columns, `orgs.currency`, `org_budgets`, `gpu_rates`, `gpu_usage`, and the ledger
backfill. A migration test upgrades a 0004 database that has runs.

### Step 2. `src/core/gpus.py`

`normalize`, `detect`, `month_of` and `split_by_month`, all pure functions with unit tests.

### Step 3. The store

- **New fields.** `Run` gets its GPU fields, `usage` and `runtime`. `Org` gets `currency`,
  `rates`, `budgets` and `usage`.
- **Ledger.** Accrual happens in `file_stream`; the ledger stays consistent in `edit_runs`,
  `move_runs`, `delete_runs`, `_drop_project` and `delete_org`.
- **Detection** happens in `save_file`, plus the startup scan.
- **New module.** `src/core/budgets.py` (`Budgets`, held by the store as `store.budgets`) loads
  the prices, budgets and ledger, changes settings, and computes the panel. The feed is a
  method there too.

Done when: spend accrues from heartbeats, survives a restart, and survives deleting runs and
projects.

### Step 4. The API

- **`src/api/home.py`:** `feed`, `billing` (GET and PATCH) and `budget`.
- **`runs.py`:** the GPU fields in `RunFields`; `gpu_detected`, `runtime` and `cost` in the run
  detail.

### Step 5. Tests

- `test_gpus.py`;
- `test_budget.py`: detection through a real upload, override, accrual, gaps, permissions,
  panel numbers, deletes, moves and restart;
- `test_feed.py`: kinds, order, cursor, filters and visibility;
- the 0005 migration test.

### Step 6. Docs and status

README, the docs site (a Guide page and an API reference page), and this doc marked `applied`.

---

## 4. Known gaps

See D29.

---

## 5. How it was actually built

The plan held. These are the details it left open, and where the build differs.

- **Modules.** `src/core/gpus.py` holds the pure functions: `normalize`, `detect`,
  `split_by_month`, and month parsing and formatting. `src/core/budgets.py` holds `Usage` and
  `Budgets` (`store.budgets`). The feed is a separate class, `src/core/feed.py`
  (`store.feed`), not a method of `Budgets`. `src/api/home.py` serves the feed, billing and
  budget routes.
- **On `Run`.** The detected and override columns are fields (`gpu_type_detected`,
  `gpu_count_detected`, `gpu_type_override`, `gpu_count_override`). `gpu_type`, `gpu_count`,
  `runtime` and `gpu_hours` are properties, and `Run.ATTRIBUTES` lists the three GPU ones, so
  they become table columns through the existing `attributes()` path.
- **Every ledger write rides on an existing statement.**
  - Accrual is a fourth CTE in the `file_stream` statement. When nothing accrues, it inserts
    from empty arrays.
  - Detection updates `runs` and the run's `gpu_usage` rows in one statement.
  - Overrides add a CTE to the `edit_runs` statement, carrying each run's new effective GPUs
    as arrays.
  - A move adds a CTE to the `move_runs` statement.
  - Deletes need no SQL: the foreign keys `SET NULL`.
- **Detection when nothing changed.** A run's effective GPUs often stay the same: the override
  hides the change, or a re-upload says the same thing. Then only the ledger and `runs` are
  written, and dashboards get no event.
- **Startup.** `gpu_hours` cells are filled after the ledger loads, because the run table is
  built first. The scan for unread metadata runs after the ledger loads. It reads the files
  in a thread, then records them under each run's lock.
- **The feed takes `limit + 1` candidates per project and kind.** That tells whether there's
  a next page. Rows tied in time with the last candidate are kept, so ties resolve by run pk
  exactly as the final sort does. A tie with the cursor's own time is settled per row on
  `(rank, pk)`.
- **API details the plan left open.**
  - `currency: null` answers 422.
  - Rates are stored with the spelling they were set with, and matched lowercased. The
    response lists them sorted case-insensitively.
  - `unknown_gpu.hours` and every `gpu_hours` are rounded to 3 decimals, and money to cents.
- **The run detail** gets `gpu_detected`, `runtime` and `cost`. The feed's run summary has no
  cost: its price depends on the org's current rates and isn't needed there.
- **Tests:**
  - `test_gpus.py` covers names, metadata shapes and month splitting.
  - `test_budget.py` covers detection through a real signed upload, overrides, accrual over
    real heartbeats, the `crash_timeout` gap, permissions, the panel and its ordering, spend
    surviving moves, deletes and a restart, and monthly budgets.
  - `test_feed.py` covers the order of kinds, cursor pages, filters, visibility and bad
    requests.
  - `test_0005_fills_the_usage_ledger_and_reads_uploaded_gpus` checks the backfill split across
    a month boundary, and the startup scan.
  - The real-SDK test checks that what Flock detected matches `gpus.detect` of the
    uploaded file.
  - Two latency tests (feed and budget) run on the 10k-run sweep.
- **Timings,** on the 10k-run sweep:
  - the feed: about 0.5 ms for the first page, and 0.7 ms for a page filtered by kind after a
    cursor;
  - the budget panel over 10k ledger entries: about 2.8 ms.
