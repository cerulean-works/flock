# Organizations and invites

Endpoints for organizations, their members and their invitations. Paths are relative to `/api/v1`, and `{org}` is an organization's ID (its `name`). Organizations you don't belong to answer 404 to every endpoint here, as if they didn't exist. Super admins belong to every organization as admins. For how roles work, see [Accounts, organizations and roles](../accounts).

## Organizations[[organizations]]

An organization is returned as:

```json
{"name": "flock", "display_name": "Flock Research", "created_at": 1792282194.3, "updated_at": 1792282194.3, "project_count": 2, "member_count": 2, "role": "admin", "avatar_url": null, "s3_ephemeral_ttl": 28800, "s3_key_max_ttl": 2592000}
```

| Field | Meaning |
|---|---|
| `name` | The organization's ID: the entity in URLs, API paths and `wandb.init(entity=...)`. It's set at creation and never changes. |
| `display_name` | What people see it called. Org admins can change it. |
| `role` | Your own role in it: `admin` or `member`. |
| `avatar_url` | The path of its avatar image, or `null`. |
| `s3_ephemeral_ttl`, `s3_key_max_ttl` | Its [S3 key lifetimes](../s3#key-lifetimes), in seconds. The maximum is `null` when keys may never expire. |

| Endpoint | Who | Meaning |
|---|---|---|
| `GET /orgs` | anyone | Your organizations as `{"orgs": [...]}`, sorted by display name, then by ID. Super admins get every organization. |
| `POST /orgs` | super admins | Creates an organization from `{"name": "vision", "display_name": "Vision Lab"}`, with you as its admin. `display_name` defaults to `name`. Answers 201, or 409 if the ID is taken. Add `wandb` to [import a W&B entity](#imports) into it. |
| `GET /orgs/{org}` | members | One organization. |
| `PATCH /orgs/{org}` | org admins | Changes its display name, with `{"display_name": "..."}`, and its S3 key lifetimes, with `s3_ephemeral_ttl` and `s3_key_max_ttl` (seconds, at least 600; `null` for no maximum). An ephemeral lifetime longer than the maximum answers 422; lowering only the maximum below it lowers it too. Returns the organization. |
| `DELETE /orgs/{org}` | org admins | Deletes it, with all its projects and runs. Its S3 buckets are deleted too, and emptied in the background. Answers 204. |
| `PUT /orgs/{org}/avatar` | org admins | Sets its avatar. The body is the image, with the same rules as [`PUT /me/avatar`](auth#avatars). Returns the organization. |
| `DELETE /orgs/{org}/avatar` | org admins | Removes its avatar. Returns the organization. |

Organization IDs follow the rules of project names: 1 to 128 characters, with no spaces around them, no control characters and none of `/ \ # ? % :`. Other IDs answer 422. A server holds at most 99999 organizations, counting deleted ones, because S3 bucket names carry the organization's number in five digits. Past that, `POST /orgs` answers 409.

Display names are free text: 1 to 128 characters after Flock trims the spaces around them, with no control characters. Other display names answer 422. Two organizations can have the same display name.

**The ID can't change.** Projects, runs, URLs and wandb clients address the organization by it. `PATCH` accepts `name` only with the current ID, so a client can send back the organization it read. A different `name` answers 400. Changing the display name works while runs are active, and dashboards streaming its projects aren't interrupted.

**Deleting** is permanent:
- It's refused with 409 while runs are active, listing them in `ids`, unless you add `?force=true`.
- Dashboards streaming its projects get a `project` event with `"deleted": true`.
- Users whose default organization it was fall back to the first organization they joined.

## Imports from W&B[[imports]]

An organization can copy every project and run of a W&B entity. The import runs in the background. See [Importing from W&B](../importing-from-wandb) for what is copied.

Start one by adding `wandb` when creating the organization, or with `POST /orgs/{org}/import`. Both take:

| Field | Meaning |
|---|---|
| `entity` | The W&B entity (team or user) to copy. Required. |
| `api_key` | A W&B API key that can read the entity. Required. Flock uses it for this import only and never stores it. |
| `base_url` | The W&B server's API URL. Defaults to [`FLOCK_WANDB_BASE_URL`](../configuration). |

Flock checks the key and the entity before creating anything:

| Problem | Answer |
|---|---|
| W&B rejects the key | 400, `W&B rejected the API key` |
| The entity doesn't exist, or the key can't see it | 404 |
| W&B can't be reached | 502 |
| `base_url` isn't an `http` or `https` URL | 422 |

When `POST /orgs` starts an import, its response adds `import`: the import's progress, as below.

| Endpoint | Who | Meaning |
|---|---|---|
| `GET /orgs/{org}/import` | org admins | The progress of the organization's latest import, or 404 if it never had one. |
| `POST /orgs/{org}/import` | super admins | Starts an import, or resumes one: runs the organization already has are skipped. Answers 202 with its progress, or 409 while an import is running. |
| `DELETE /orgs/{org}/import` | super admins | Stops a running import, and returns its progress. The runs copied so far stay. 404 if there never was an import. |

The progress is returned as:

```json
{"entity": "acme", "base_url": "https://api.wandb.ai", "state": "running", "projects_total": 4, "projects_done": 1, "runs_total": 230, "runs_done": 61, "runs_skipped": 0, "runs_failed": 1, "current_project": "llm-pretrain", "error": "llm-pretrain/9fc2v9ee: W&B refused the query: ...", "started_by": 1, "started_at": 1792282194.3, "finished_at": null}
```

| Field | Meaning |
|---|---|
| `state` | `running`; `finished`; `failed` when the import stopped on an error; `cancelled`; or `interrupted` when Flock restarted during it. |
| `projects_total`, `projects_done` | Projects in the entity, and projects fully copied. |
| `runs_total` | Runs in the entity, as W&B counts them. |
| `runs_done`, `runs_skipped`, `runs_failed` | Runs copied, runs already in the organization, and runs that couldn't be copied. |
| `current_project` | The project being copied, or `null`. |
| `error` | Why the import failed, or the last run that couldn't be copied, or `null`. |
| `started_by` | The id of the super admin who started it. |
| `started_at`, `finished_at` | When it started, and when it ended (`null` while it runs). |

Deleting the organization stops its import.

## Members[[members]]

| Endpoint | Who | Meaning |
|---|---|---|
| `GET /orgs/{org}/members` | members | `{"members": [...]}`, sorted by email. Each has `id`, `name`, `email`, `avatar_url` and `role`. |
| `PATCH /orgs/{org}/members/{id}` | org admins | Changes the role of member `id` to `{"role": "admin"}` or `"member"`. Returns `{"id", "role"}`. |
| `DELETE /orgs/{org}/members/{id}` | org admins, or the member | Removes a member. Members remove themselves to leave. Their runs stay. Answers 204. |

Demoting or removing the organization's last admin answers 409. A user who isn't a member answers 404. Super admins who aren't members aren't listed.

## Invitations[[invitations]]

An invitation is returned as:

| Field | Meaning |
|---|---|
| `id` | Integer id, for withdrawing it. |
| `email` | The invited address, lowercase. |
| `role` | `admin` or `member`: the role the person gets. |
| `created_by` | The id of the user who created it, or `null` if that account was deleted. |
| `created_at`, `expires_at` | When it was created, and when it stops working. |

| Endpoint | Who | Meaning |
|---|---|---|
| `GET /orgs/{org}/invites` | org admins | `{"invites": [...]}`: the invitations that are neither accepted nor expired. |
| `POST /orgs/{org}/invites` | org admins | Invites `{"email": "ben@example.com", "role": "member"}`. `role` defaults to `member`. Answers 201 with the invitation plus `url` and `token`. |
| `DELETE /orgs/{org}/invites/{id}` | org admins | Withdraws an invitation. Answers 204, or 404 if it doesn't exist. |

The `url` is `<FLOCK_WEB_URL>/invite/<token>`. Without `FLOCK_WEB_URL`, it starts with `FLOCK_API_PUBLIC_URL`, else with the address of the request. The token is shown only in this response, and the link is valid for `FLOCK_INVITE_TTL` (7 days by default). Inviting someone who is already a member answers 409. An email that isn't shaped like one answers 422.

## Invitation links[[invitation-links]]

These are public: the token is the secret.

### `GET /invites/{token}`[[get-invite]]

What the invitation is for:

```json
{"org": "flock", "org_display_name": "Flock Research", "org_avatar_url": null, "email": "ben@example.com", "role": "member", "expires_at": 1792887026.87, "account_exists": false}
```

`org` is the organization's ID, and `org_display_name` what to call it.

`account_exists` says whether an account with this email exists. If it does, the person signs in to accept instead of creating an account.

### `POST /invites/{token}/accept`[[accept-invite]]

Accepts the invitation in one of two ways:
- **As a new account**, with `{"name": "Ben Okafor", "password": "..."}` and no credentials. `password` is 8 to 256 characters, and `name` defaults to the email. It creates the account as a member of the organization, with the invitation's role, and answers 201 with [tokens](auth#tokens). If an account with the email exists, it answers 409: sign in and accept as the second way.
- **As an existing account**, with `{}` and the credentials of the invited account, an access token or a CLI token. It adds the membership and answers 200 with `{"user": ...}`. Credentials of another account answer 403.

Without a password and without credentials, it answers 401. An invalid, expired or used token answers 404. Once accepted, the invitation is used up.
