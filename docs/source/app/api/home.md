# Home feed and budgets

Endpoints for a dashboard's home page: the activity feed, an organization's GPU prices and budget, and what it spent. Paths are relative to `/api/v1`, and `{org}` is an organization's ID (its `name`). Organizations you don't belong to answer 404. [GPU costs, budgets and the home feed](../budget-behavior) explains how GPUs are detected and run time is counted.

## `GET /feed`[[feed]]

What happened across the projects you can see, newest first. Super admins see every organization.

| Query parameter | Default | Meaning |
|---|---|---|
| `org` | every organization you belong to | Only this organization. 404 if you don't belong to it. |
| `project` | every project | Only this project of `org`. Needs `org`, or answers 400. 404 if it doesn't exist. |
| `owner` | everyone | `me`, or a user id: only runs that user logged. Leaves out `project_created`. |
| `kinds` | every kind | A comma-separated list of the kinds below. Unknown kinds answer 400. |
| `cursor` | none | The `next` of the previous page. Malformed cursors answer 400. |
| `limit` | 30 | Items per page, from 1 to 100. |

| Kind | `at` |
|---|---|
| `run_started` | When the run was created. |
| `run_finished`, `run_failed`, `run_crashed` | The run's last heartbeat. |
| `project_created` | When the project was created. |

```json
{
  "items": [
    {
      "id": "run_finished:812",
      "kind": "run_finished",
      "at": 1792310034.5,
      "org": "flock",
      "project": "llm-pretrain",
      "actor": {"id": 3, "name": "Ana", "email": "ana@example.com", "avatar_url": null},
      "run": {
        "id": "9fc2v9ee",
        "display_name": "gpt-small-baseline",
        "state": "finished",
        "gpu_type": "H200",
        "gpu_count": 8,
        "runtime": 5400.0,
        "tags": ["baseline"],
        "group": null,
        "job_type": "train"
      }
    },
    {
      "id": "project_created:4",
      "kind": "project_created",
      "at": 1792282194.3,
      "org": "flock",
      "project": "llm-pretrain",
      "actor": null,
      "description": "Pretraining runs"
    }
  ],
  "next": "WzE3OTIyODIxOTQuMywwLDRd"
}
```

| Field | Meaning |
|---|---|
| `id` | Unique within the feed: the kind and the run's or project's internal id. |
| `actor` | The user who logged the run, or `null` for runs without an owner and for `project_created`. |
| `run.runtime` | Seconds the run was alive so far. See [How run time is counted](../budget-behavior#how-run-time-is-counted). |
| `run.state` | The run's state now, which may differ from the item's kind: a `run_started` item of a run that has since finished shows `finished`. |
| `description` | The project's description, on `project_created` items. |
| `next` | Pass it as `cursor` to get the next page. `null` on the last page. |

## `GET /orgs/{org}/billing`[[billing]]

The organization's prices and budgets. Every member can read them.

| Field | Meaning |
|---|---|
| `currency` | A three-letter label, `USD` by default. |
| `monthly_budget` | The budget of the current month, or `null`. |
| `budgets` | Every budget change: `{"month": "2026-10", "amount": 20000.0}`, in month order. A budget applies from its month until the next change. `amount` is `null` where the budget was removed. |
| `rates` | `{"gpu_type", "hourly_cost"}` for each priced GPU type, sorted by type. |
| `gpu_types` | Every GPU type the organization's runs used, or that has a price: `{"gpu_type", "gpu_hours", "runs", "hourly_cost"}`. `gpu_hours` covers every month, `runs` counts the runs with GPU hours of that type, deleted runs too, and `hourly_cost` is `null` for types without a price. Sorted by GPU hours, most first. |

## `PATCH /orgs/{org}/billing`[[update-billing]]

Changes prices and budgets. Org admins only; members answer 403. Every field is optional:

| Field | Meaning |
|---|---|
| `monthly_budget` | A number of at least 0: the budget from the current month onward. `null` removes it from the current month onward. |
| `currency` | Three uppercase letters, such as `EUR`. |
| `rates` | An object of GPU type to hourly cost per GPU, such as `{"H200": 60}`. It's merged into the existing prices: types you leave out keep their price, and `null` removes one. Types are 1 to 64 characters and match without regard to case. |

Negative numbers, a lowercase currency and unknown fields answer 422. The response is the same as [`GET /orgs/{org}/billing`](#billing).

## `GET /orgs/{org}/budget`[[budget]]

What the organization spent in a month, against its budget. Every member can read it.

| Query parameter | Default | Meaning |
|---|---|---|
| `month` | the current UTC month | A month as `YYYY-MM`. Anything else answers 422. |

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
  "excluded": {"projects": 1, "cost": 480.0, "gpu_hours": 8.0},
  "top_runs": [
    {
      "entity": "flock",
      "project": "llm-pretrain",
      "id": "9fc2v9ee",
      "display_name": "gpt-small-baseline",
      "state": "finished",
      "owner": {"id": 3, "name": "Ana", "email": "ana@example.com", "avatar_url": null},
      "gpu_type": "H200",
      "gpu_count": 8,
      "gpu_hours": 12.0,
      "cost": 720.0
    }
  ],
  "unpriced": [{"gpu_type": "L40S", "gpu_hours": 8.0}],
  "unknown_gpu": {"runs": 2, "hours": 3.5}
}
```

| Field | Meaning |
|---|---|
| `budget` | The month's budget, or `null` if none was set. |
| `spent` | The month's cost: GPU hours times the current price of each GPU type, rounded to cents. Leaves out `excluded`. |
| `remaining` | `budget` minus `spent`. Negative when the budget is exceeded, `null` without a budget. |
| `gpu_hours` | GPU hours in the month, priced or not. Leaves out `excluded`. |
| `projects` | Cost, GPU hours and number of runs of each project that used GPU hours in the month, most expensive first. Runs without GPU hours aren't counted. Deleted runs count under their project. `"project": null` collects the spend of deleted projects that wasn't left out. |
| `excluded` | The deleted projects an admin [left out of the budget](#update-deleted-project) that used GPU hours in the month, and their cost and GPU hours. |
| `top_runs` | Up to 5 runs that cost the most in the month (ordered by GPU hours where there's no price). Deleted runs aren't listed, though their spend counts. `owner` is `null` for runs without one. |
| `unpriced` | Admins only: GPU hours of GPU types without a price, which cost nothing. Left out for members. |
| `unknown_gpu` | Admins only: runs that still exist and were alive in the month with no GPU information, and their hours (not GPU hours). Left out for members. |

The warnings about `unpriced` and `unknown_gpu` are served by [`GET /orgs/{org}/budget/warnings`](#warnings).

## `GET /orgs/{org}/budget/warnings`[[warnings]]

The organization's budget warnings. Org admins only; members answer 403.

| Query parameter | Default | Meaning |
|---|---|---|
| `month` | the current UTC month | The month of the active warnings, as `YYYY-MM`. Anything else answers 422. |

```json
{
  "month": "2026-10",
  "warnings": [
    {
      "id": "unpriced:2026-10",
      "month": "2026-10",
      "kind": "unpriced",
      "fingerprint": "4f0e2c9a1b7d6e35",
      "dismissed": false,
      "dismissed_by": null,
      "dismissed_at": null,
      "gpu_types": [{"gpu_type": "L40S", "gpu_hours": 8.0}]
    }
  ],
  "dismissed": [
    {
      "month": "2026-10",
      "id": "unknown_gpu:2026-10",
      "kind": "unknown_gpu",
      "fingerprint": "9b1c4e0f2a7d3c55",
      "dismissed": true,
      "dismissed_by": {"id": 1, "name": "Admin", "email": "admin@example.com", "avatar_url": null},
      "dismissed_at": 1792310034.5,
      "runs": 2,
      "hours": 3.5
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `warnings` | The month's warnings that nobody dismissed. |
| `dismissed` | Every dismissed warning, of any month, that is still raised and about nothing new since it was dismissed. Newest month first. |

Each warning is about a set of things: GPU types for `unpriced`, runs for `unknown_gpu`. A warning is raised only when they add up to 0.05 hours or more (GPU hours for `unpriced`, hours for `unknown_gpu`), and an `unpriced` warning lists only the GPU types that do.

| Warning field | Meaning |
|---|---|
| `id` | `{kind}:{month}`, used to [dismiss](#dismiss-warning) it. |
| `month` | The month it's about, as `YYYY-MM`. |
| `kind` | `unpriced` or `unknown_gpu`. |
| `fingerprint` | Changes when what the warning is about changes. |
| `dismissed` | An admin dismissed it, and it's about nothing new since. |
| `dismissed_by`, `dismissed_at` | Who dismissed it and when (a Unix time), or `null`. |
| `gpu_types` | For `unpriced`: `{"gpu_type", "gpu_hours"}`, most GPU hours first. |
| `runs`, `hours` | For `unknown_gpu`: the number of runs and their hours. |

## `PUT /orgs/{org}/budget/warnings/{warning}/dismissal`[[dismiss-warning]]

Dismisses a warning for everyone in the organization. Org admins only; members answer 403. `{warning}` is the warning's `id`, such as `unknown_gpu:2026-10`.

| Field | Meaning |
|---|---|
| `fingerprint` | The warning's `fingerprint`, as you read it. |

It answers 204. The warning stays dismissed while it's about the same GPU types or runs, or a subset of them, and is active again once it's about something new. A warning that isn't raised now, or an unknown kind or month, answers 404. A `fingerprint` that no longer matches answers 409: read the warnings again.

## `DELETE /orgs/{org}/budget/warnings/{warning}/dismissal`[[restore-warning]]

Shows a dismissed warning again. Org admins only. It answers 204, also when the warning wasn't dismissed.

## `GET /orgs/{org}/budget/deleted-projects`[[deleted-projects]]

The deleted projects whose runs used GPU hours, most recently deleted first. Org admins only; members answer 403.

```json
{
  "currency": "USD",
  "deleted_projects": [
    {
      "id": 12,
      "name": "llm-sweep-old",
      "deleted_at": 1792310034.5,
      "cost": 480.0,
      "gpu_hours": 8.0,
      "runs": 4,
      "months": [{"month": "2026-10", "cost": 480.0, "gpu_hours": 8.0}],
      "excluded": true,
      "excluded_at": 1792311000.0,
      "excluded_by": {"id": 1, "name": "Admin", "email": "admin@example.com", "avatar_url": null}
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `id` | The deleted project's id, for [`PATCH`](#update-deleted-project). It isn't the id the project had. |
| `name` | The project's name when it was deleted. `null` collects the projects deleted before Flock kept names, one entry per organization, with `deleted_at` also `null`. |
| `cost`, `gpu_hours`, `runs` | Over every month, at the current prices. Runs without GPU hours aren't counted. |
| `months` | The same, by month, in month order. |
| `excluded` | Its spend is left out of the budget. `excluded_at` and `excluded_by` say when and by whom, or are `null`. |

## `PATCH /orgs/{org}/budget/deleted-projects/{id}`[[update-deleted-project]]

Leaves a deleted project's spend out of the budget, or counts it again. Org admins only; members answer 403.

| Field | Meaning |
|---|---|
| `excluded` | `true` to leave its spend out of every month, `false` to count it again. |

The response is the deleted project, as in the list. An `id` that isn't listed answers 404.

## GPUs on runs[[run-gpus]]

Runs have three GPU [attributes](projects#run-attributes): `gpu_type`, `gpu_count` and `gpu_hours`. [`PATCH .../runs/{run}`](projects#patch-run) and [`POST .../runs/update`](projects#update-runs) accept `gpu_type` (1 to 64 characters) and `gpu_count` (0 to 1024) to override what was detected, and `null` to go back to it. The run detail also returns:

| Field | Meaning |
|---|---|
| `gpu_detected` | `{"type", "count"}` as read from the run's `wandb-metadata.json`. `count` is `null` until the file arrives. |
| `runtime` | Seconds the run was alive. |
| `cost` | What the run cost over all months, at the current prices. |
