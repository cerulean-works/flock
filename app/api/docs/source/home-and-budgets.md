# GPU costs, budgets and the home feed

Flock records which GPUs each run used and how long it ran. Admins of an organization set an hourly price for each GPU type and a monthly budget, and Flock reports what was spent. It also serves a feed of recent activity for a dashboard's home page. This page explains how each of these works and how to set them up.

The examples use `curl` with an admin's access token in `$TOKEN` and Flock's URL in `$FLOCK`, as in the [Quickstart](quickstart). The endpoints are listed in [Home feed and budgets](api/home).

## Which GPUs a run used

The `wandb` SDK uploads a `wandb-metadata.json` file when a run starts, describing the machine. Flock reads the GPU model and count from it, and keeps a short type name that prices are set by:

| The SDK reports | Type | Count |
|---|---|---|
| 8 × `NVIDIA H200` | `H200` | 8 |
| 4 × `NVIDIA A100-SXM4-80GB` | `A100` | 4 |
| `NVIDIA GeForce RTX 4090` | `RTX 4090` | 1 |
| 8 × `AMD Instinct MI300X` | `MI300X` | 8 |
| no GPU | `null` | 0 |

TPUs and Trainium devices are read too. Until the metadata arrives, the count is `null`: Flock doesn't know yet. Runs uploaded before Flock tracked GPUs are read when it starts.

The type, count and GPU hours are [run attributes](api/projects#run-attributes), so you can filter and sort the run table by them:

```bash
curl -s --json '{"filter": {"gpu_type": "H200"}, "columns": ["gpu_count", "gpu_hours"]}' -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/projects/flock/llm-pretrain/runs/query
```

### Correcting the GPUs

What the SDK reports isn't always what the run used:
- **It counts every GPU on the machine**, including ones hidden from the run with `CUDA_VISIBLE_DEVICES`.
- **A multi-node run reports one node**: the GPUs of the machine that logs the run.

Set the right values on the run. Owners and admins can do this, as with other [run edits](managing-runs#editing-runs):

```bash
curl -s -X PATCH --json '{"gpu_count": 32}' -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/projects/flock/llm-pretrain/runs/9fc2v9ee
```

The response shows the values in effect (`gpu_type` and `gpu_count`) and what was detected (`gpu_detected`). Send `null` to go back to the detected value. The correction applies to the run's whole history, including months already past.

## How run time is counted

A run is billed for the time it was alive: the time between the heartbeats its client sends while it logs. The SDK sends one every few seconds.
- **Gaps aren't billed.** If a run is silent for longer than [`FLOCK_CRASH_TIMEOUT`](configuration) (10 minutes by default), the gap doesn't count. A run preempted on Friday and resumed on Monday pays for neither the weekend nor the time it was marked crashed.
- **Time is counted per UTC month.** A run that crosses midnight at the end of a month is split between the two months.
- **Deleting doesn't refund.** Spend stays in the organization's budget after its run or project is deleted, so deleting runs never frees budget.

GPU hours are the run time multiplied by the GPU count. The run detail's `runtime` is in seconds, and `gpu_hours` is in hours.

> [!NOTE]
> Runs logged before Flock tracked GPUs are counted from their creation to their last heartbeat. For those runs, a gap between a preemption and a resume can't be told apart from running time.

## Setting prices and a budget

Org admins set the prices of GPU types and the monthly budget in one request:

```bash
curl -s -X PATCH --json '{"monthly_budget": 20000, "rates": {"H200": 60, "A100": 25}}' -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/orgs/flock/billing
```

- **Prices** are the cost of one GPU for one hour. Types match without regard to case, so `h200` and `H200` are the same price. `null` removes a price.
- **The budget** applies from the current month onward. Earlier months keep the budget they had. `null` removes it.
- **`currency`** is a label, `USD` by default. Nothing is converted.

The response lists every GPU type the organization's runs used, with its price or `null`. Use it to find the types that still need a price:

```json
{
  "currency": "USD",
  "monthly_budget": 20000.0,
  "budgets": [{"month": "2026-10", "amount": 20000.0}],
  "rates": [{"gpu_type": "A100", "hourly_cost": 25.0}, {"gpu_type": "H200", "hourly_cost": 60.0}],
  "gpu_types": [
    {"gpu_type": "H200", "gpu_hours": 1520.5, "runs": 31, "hourly_cost": 60.0},
    {"gpu_type": "L40S", "gpu_hours": 12.0, "runs": 2, "hourly_cost": null}
  ]
}
```

Costs always use the current prices. If you change a price, the cost of past months changes too.

## Reading the budget

Every member can read where the month's budget went:

```bash
curl -s -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/orgs/flock/budget
```

The response has:
- **what was spent and what's left:** `spent`, `budget`, and `remaining`, which goes negative when the budget is exceeded;
- **the cost of each project**, most expensive first. Spend of deleted projects is grouped under `"project": null`;
- **the five most expensive runs** of the month;
- **GPU hours with no price:** `unpriced`, by GPU type, which cost nothing until an admin sets a price;
- **runs with no GPU information:** `unknown_gpu`, for example runs logged without the SDK's metadata.

Add `?month=2026-09` for an earlier month. The full response is in [`GET /orgs/{org}/budget`](api/home#budget).

## The home feed

`GET /api/v1/feed` lists what happened across all the projects you can see, newest first:

| Kind | When |
|---|---|
| `run_started` | A run was created. |
| `run_finished`, `run_failed`, `run_crashed` | A run ended, at its last heartbeat. |
| `project_created` | A project was created. |

```bash
curl -s -H "Authorization: Bearer $TOKEN" "$FLOCK/api/v1/feed?limit=20"
```

Each item names the organization, the project and the user who logged the run, with a short summary of the run. Filter the feed by organization, project, user (`owner=me` for your own runs) or kind. For example, `kinds=run_started` lists the latest runs. Follow `next` to load older items.

The feed is computed from runs and projects as they are now. A deleted run disappears from it, and renames, deletions and membership changes don't appear. The feed isn't pushed live, so poll it to keep a home page fresh.
