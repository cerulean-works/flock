# Home feed and budgets

Endpoints for a dashboard's home page: the activity feed, an organization's GPU prices and budget, and what it spent. Paths are relative to `/api/v1`, and `{org}` is an organization's name. Organizations you don't belong to answer 404. [GPU costs, budgets and the home feed](../home-and-budgets) explains how GPUs are detected and run time is counted.

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
| `run.runtime` | Seconds the run was alive so far. See [How run time is counted](../home-and-budgets#how-run-time-is-counted). |
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
| `gpu_types` | Every GPU type the organization's runs used, or that has a price: `{"gpu_type", "gpu_hours", "runs", "hourly_cost"}`. `gpu_hours` covers every month, `runs` counts deleted runs too, and `hourly_cost` is `null` for types without a price. Sorted by GPU hours, most first. |

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
| `spent` | The month's cost: GPU hours times the current price of each GPU type, rounded to cents. |
| `remaining` | `budget` minus `spent`. Negative when the budget is exceeded, `null` without a budget. |
| `gpu_hours` | GPU hours in the month, priced or not. |
| `projects` | Cost, GPU hours and number of runs of each project that ran in the month, most expensive first. Deleted runs count under their project. `"project": null` collects the spend of deleted projects. |
| `top_runs` | Up to 5 runs that cost the most in the month (ordered by GPU hours where there's no price). Deleted runs aren't listed, though their spend counts. `owner` is `null` for runs without one. |
| `unpriced` | GPU hours of GPU types without a price, which cost nothing. |
| `unknown_gpu` | Runs that ran in the month with no GPU information, and their hours (not GPU hours). |

## GPUs on runs[[run-gpus]]

Runs have three GPU [attributes](projects#run-attributes): `gpu_type`, `gpu_count` and `gpu_hours`. [`PATCH .../runs/{run}`](projects#patch-run) and [`POST .../runs/update`](projects#update-runs) accept `gpu_type` (1 to 64 characters) and `gpu_count` (0 to 1024) to override what was detected, and `null` to go back to it. The run detail also returns:

| Field | Meaning |
|---|---|
| `gpu_detected` | `{"type", "count"}` as read from the run's `wandb-metadata.json`. `count` is `null` until the file arrives. |
| `runtime` | Seconds the run was alive. |
| `cost` | What the run cost over all months, at the current prices. |
