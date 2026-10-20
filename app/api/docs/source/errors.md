# Errors and status codes

The status codes the HTTP API answers with, the shape of error bodies, and the errors the `wandb` SDK can report.

## Error bodies

Most errors have a JSON body with an `error` message:

```json
{"error": "only admins of flock can do this", "errors": [{"message": "only admins of flock can do this"}]}
```

- **`errors`** repeats the message in the shape GraphQL clients expect, so the `wandb` SDK can show it. Some errors leave it out.
- **`ids`** lists run ids when the error is about specific runs: the runs in the way of a conflict, the runs you don't own, or the run ids that weren't found.

```json
{"error": "cannot delete active runs (pass force=true)", "ids": ["9fc2v9ee"]}
```

Requests that fail validation (422) have FastAPI's body instead, which lists each problem under `detail`:

```json
{"detail": [{"type": "string_too_short", "loc": ["body", "password"], "msg": "String should have at least 8 characters", "input": "short", "ctx": {"min_length": 8}}]}
```

## Status codes

| Code | Meaning |
|---|---|
| 200 | Success, with a body. |
| 201 | Created: a project, organization, invitation, API key, reset link, or an account from signing up or accepting an invitation. |
| 202 | Accepted: a [W&B import](api/orgs#imports) started in the background. |
| 204 | Success, with no body: deletes, signing out, changing your password. |
| 302 | A redirect, in the Google sign-in flow. |
| 400 | A request Flock can't apply: an invalid filter, a bulk operation that selects nothing, an empty `keys` list, signing out with an API key, a W&B API key that W&B rejects, a different `name` when updating an organization (its ID can't change), or a body that isn't JSON or valid gzip on the wandb protocol endpoints. |
| 401 | Missing, invalid or expired credentials. Also a wrong email or password, an invalid refresh token or Google code, and accepting an invitation with neither a password nor credentials. |
| 403 | Not allowed: your role doesn't allow it, the runs aren't yours (with `ids`), signing up after the first account, a wrong current password, an invitation for another email, or an invalid upload signature. |
| 404 | Not found, or in an organization you don't belong to. Also an invalid, expired or used invitation or reset link, and Google sign-in when it isn't configured. |
| 409 | A conflict. See below. |
| 422 | The request doesn't match the endpoint's schema: a missing or unknown field, a value of the wrong type, an invalid name, display name or email, or a password of the wrong length. |
| 502 | W&B couldn't be reached when starting an [import](api/orgs#imports). |

## Conflicts

A 409 changes nothing. These requests answer it:

| Request | Conflict | `ids` |
|---|---|---|
| Delete a run, runs, a project or an organization | Some runs are active and `force` isn't set. | the active runs (as `<project>/<run>` for an organization) |
| Rename a project | It has active runs. | the active runs |
| Move runs | Some are active. | the active runs |
| Move runs | The target project already has runs with these ids. | the clashing runs |
| Create or rename a project, or create an organization | The name (for an organization, the ID) is taken. | none |
| Invite someone | They're already a member. | none |
| Start a W&B import | One is already running in the organization. | none |
| Accept an invitation as a new account | An account with that email exists: sign in to accept. | none |
| Demote or remove an org admin | They're the organization's last admin. | none |
| Demote or delete a super admin | They're the last super admin. | none |

Active runs stop being active when they finish, or when [crash detection](deploying#crash-detection) marks them `crashed`.

## Errors in the wandb SDK

The SDK talks to Flock through GraphQL, which reports errors with HTTP 200 and an `errors` list. The SDK shows the message and stops. These are the messages you can see:

| Message | Cause |
|---|---|
| `organization <name> not found` | The `entity` doesn't exist, or you aren't a member of it. |
| `you are not in any organization` | No `entity` was given, and your account belongs to no organization. |
| `run <entity>/<project>/<run> belongs to another user` | A member tried to resume or write to someone else's run. |
| `operation '<name>' is not supported by this server` | The SDK used a feature Flock doesn't implement, such as artifacts or `wandb.Api()`. See [wandb compatibility](wandb-compatibility). |
| `invalid file name '<name>'` | The SDK asked to upload a file with an absolute path or `..` in its name. |

An unknown API key fails `wandb login` and `wandb.init` with HTTP 401.
