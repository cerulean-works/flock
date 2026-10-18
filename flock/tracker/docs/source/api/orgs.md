# Organizations and invites

Endpoints for organizations, their members and their invitations. Paths are relative to `/api/v1`, and `{org}` is an organization's name. Organizations you don't belong to answer 404 to every endpoint here, as if they didn't exist. Super admins belong to every organization as admins. For how roles work, see [Accounts, organizations and roles](../accounts).

## Organizations[[organizations]]

An organization is returned as:

```json
{"name": "flock", "created_at": 1792282194.3, "updated_at": 1792282194.3, "project_count": 2, "member_count": 2, "role": "admin", "avatar_url": null}
```

`role` is your own role in it: `admin` or `member`. `avatar_url` is the path of its avatar image, or `null`.

| Endpoint | Who | Meaning |
|---|---|---|
| `GET /orgs` | anyone | Your organizations as `{"orgs": [...]}`, sorted by name. Super admins get every organization. |
| `POST /orgs` | super admins | Creates an organization from `{"name": "vision"}`, with you as its admin. Answers 201, or 409 if the name is taken. |
| `GET /orgs/{org}` | members | One organization. |
| `PATCH /orgs/{org}` | org admins | Renames it, with `{"name": "..."}`. Returns the organization. |
| `DELETE /orgs/{org}` | org admins | Deletes it, with all its projects and runs. Answers 204. |
| `PUT /orgs/{org}/avatar` | org admins | Sets its avatar. The body is the image, with the same rules as [`PUT /me/avatar`](auth#avatars). Returns the organization. |
| `DELETE /orgs/{org}/avatar` | org admins | Removes its avatar. Returns the organization. |

Organization names follow the rules of project names: 1 to 128 characters, with no spaces around them, no control characters and none of `/ \ # ? % :`. Other names answer 422.

**Renaming** changes the entity of every project and run in the organization:
- It's refused with 409 while any run of the organization is active, and `ids` lists them as `<project>/<run>`.
- A name that another organization uses answers 409.
- Dashboards streaming its projects get a `project` event with `entity_renamed_to`, and their streams end.

**Deleting** is permanent:
- It's refused with 409 while runs are active, listing them in `ids`, unless you add `?force=true`.
- Dashboards streaming its projects get a `project` event with `"deleted": true`.
- Users whose default organization it was fall back to the first organization they joined.

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

The `url` is `<TRACKER_APP_URL>/invite/<token>`. Without `TRACKER_APP_URL`, it starts with `TRACKER_PUBLIC_URL`, else with the address of the request. The token is shown only in this response, and the link is valid for `TRACKER_INVITE_TTL` (7 days by default). Inviting someone who is already a member answers 409. An email that isn't shaped like one answers 422.

## Invitation links[[invitation-links]]

These are public: the token is the secret.

### `GET /invites/{token}`[[get-invite]]

What the invitation is for:

```json
{"org": "flock", "org_avatar_url": null, "email": "ben@example.com", "role": "member", "expires_at": 1792887026.87, "account_exists": false}
```

`account_exists` says whether an account with this email exists. If it does, the person signs in to accept instead of creating an account.

### `POST /invites/{token}/accept`[[accept-invite]]

Accepts the invitation in one of two ways:
- **As a new account**, with `{"name": "Ben Okafor", "password": "..."}` and no credentials. `password` is 8 to 256 characters, and `name` defaults to the email. It creates the account as a member of the organization, with the invitation's role, and answers 201 with [tokens](auth#tokens). If an account with the email exists, it answers 409: sign in and accept as the second way.
- **As an existing account**, with `{}` and the credentials of the invited account, an access token or an API key. It adds the membership and answers 200 with `{"user": ...}`. Credentials of another account answer 403.

Without a password and without credentials, it answers 401. An invalid, expired or used token answers 404. Once accepted, the invitation is used up.
