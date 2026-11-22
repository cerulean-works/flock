# Eval launches

Endpoints for launching evals on Modal from the dashboard, which the server follows in the background. Paths are relative to `/api/v1/orgs/{org}/evals/launches`. Members and admins of the organization can use them, and others get 404. They answer 404 on a server that doesn't launch evals. For the dashboard's side, see [Launch evals from the dashboard](../launch-evals).

## Endpoints

| Endpoint | Meaning |
|---|---|
| `GET catalogue` | `{"suites": [...], "tasks": [...], "max_usd", "monthly_usd"}`: the suites and tasks a launch may run (every dataset of each is public), and the caps in US dollars. |
| `POST` (the launches path itself) | Launches an eval. Answers 202 with the launch. See [Launching](#launching). |
| `GET` (the launches path itself) | `{"launches": [...]}`: the organization's running launches and those of the last 30 days, newest first, at most 100. Add `?project=` for one project's. |
| `GET {id}` | One launch. |
| `DELETE {id}` | Cancels a running launch, and answers it. Only whoever launched it, or an admin, can cancel (403 otherwise). It answers 409 for the few seconds while Modal is still starting the call. A launch that already ended is answered as it is. |

## Launching[[launching]]

The body has these fields:

| Field | Default | Meaning |
|---|---|---|
| `model` | required | A public Hugging Face model, as `owner/name`. |
| `revision` | `main` | A branch, tag or commit, resolved to a commit at launch. |
| `suites` | `[]` | Suites from the catalogue. |
| `tasks` | `[]` | Tasks from the catalogue. At least one suite or task is needed. |
| `add_bos` | `false` | Start each request with the BOS token, as Gemma models need. |
| `project` | required | The project the run is logged to. |

The launch is refused with:

| Status | When |
|---|---|
| 422 | No suite or task, one given twice, or one not in the catalogue. |
| 403, 404 | The model is gated, private or not on the Hub. |
| 409 | This month's launches leave no room for another launch's cap under the monthly cap. |
| 502 | Modal refused the launch. |

## A launch

| Field | Meaning |
|---|---|
| `id` | The launch's number. |
| `project`, `run` | Where it logs: run `run` of `project`, named before it starts. |
| `model`, `revision`, `sha` | The model, the revision asked for and the commit scored. |
| `suites`, `tasks`, `add_bos` | What it scores. |
| `state` | `running`, `finished`, `failed` or `cancelled`. |
| `cap_usd` | Its cap at launch. |
| `flock_commit` | The flock commit that scored it, once finished. |
| `error` | Why it failed. |
| `started_by` | Who launched it, or `null` once that account is deleted. |
| `started_at`, `finished_at` | Unix times. |
