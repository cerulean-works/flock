# Accounts, organizations and roles

Every request to Flock comes from a user account. This page explains how organizations and roles work, how people join, and how to handle password resets, sessions and API keys. Flock sends no email: when someone needs an invitation or a reset, an admin creates a link and passes it on.

The examples use `curl` with an admin's access token in `$TOKEN` and Flock's URL in `$FLOCK`, as in the [Quickstart](quickstart). The endpoints are listed in [Sign-in and accounts](api/auth) and [Organizations and invites](api/orgs).

## Organizations

An organization is a wandb entity. `wandb.init(entity="flock")` logs into the organization `flock`, and every project belongs to one organization. A user can belong to any number of organizations, with a role in each.

An organization has two names:
- **Its ID** (`name` in the API) is the entity. It appears in URLs, in API paths and in `wandb.init(entity=...)`. It's set when the organization is created and never changes, so scripts and links keep working.
- **Its display name** (`display_name`) is what the dashboard shows, such as `Flock Research`. It's free text, and admins can change it at any time.

Only super admins create organizations. A super admin becomes an admin of each organization they create:

```bash
curl -s --json '{"name": "vision", "display_name": "Vision Lab"}' -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/orgs
```

Without `display_name`, the organization is shown by its ID. The dashboard suggests an ID from the display name you type, such as `vision-lab` for `Vision Lab`, and you can edit it before creating the organization.

To people outside it, an organization doesn't exist: every request about it, or about anything in it, answers 404.

An admin changes the display name with `PATCH /api/v1/orgs/{org}`, even while runs are active, since clients address the organization by its ID. The ID itself can't be changed. Deleting an organization deletes all its projects and runs, permanently.

## Roles

| Role | Can |
|---|---|
| member | Read everything in the organization, create projects, log runs, and edit, move or delete the runs they logged. |
| org admin | Everything a member can, plus invite people, change roles, remove members, change the organization's display name or delete it, update or delete projects, and change any run. |
| super admin | Everything an org admin can, in every organization, whether or not they're a member. Also create organizations and manage user accounts. |

Roles are looked up on every request, so a change applies at once. [Permissions](permissions) has the full matrix.

Two rules keep the server manageable:
- **An organization keeps its last admin.** Demoting or removing its last admin answers 409.
- **The server always keeps a super admin.** Demoting or deleting the last one answers 409.

## The first account

A new Flock instance has no accounts. Until the first one exists, it refuses every request that needs credentials, and it logs where to create the account:

```text
WARNING app: no accounts yet: open http://localhost:5173/setup to create the first one, which becomes the super admin
```

The address is your dashboard's setup page, built from `FLOCK_WEB_URL`. Without a dashboard, sign up through the API:

```bash
curl -s --json '{"email": "ana@example.com", "name": "Ana Lima", "password": "correct horse battery"}' $FLOCK/api/v1/auth/signup
```

The first account becomes a super admin, and an admin of every existing organization. It creates no organization automatically. After that, signing up answers 403, and accounts are only created from invitations.

The dashboard's setup page works in two steps. It creates the account without an organization, then asks the super admin for the first one's display name and ID, creates it and makes it their default. Signing in as a super admin while Flock has no organization returns to that second step. A first sign-in with Google also creates the account without an organization, so the dashboard asks for its name next.

> [!WARNING]
> Whoever reaches a new Flock instance first becomes its super admin. Sign up before you make Flock reachable by anyone else.

## Inviting people

An org admin invites someone by email, with the role they'll have (`member` by default):

```bash
curl -s --json '{"email": "ben@example.com", "role": "member"}' -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/orgs/flock/invites
```

```json
{"id":1,"email":"ben@example.com","role":"member","created_by":1,"created_at":1792282226.86671,"expires_at":1792887026.86671,"url":"http://localhost:5173/invite/brYNOXcLkqwwVBH24dTsqe4l042F1xFerdLE4RB32uM","token":"brYNOXcLkqwwVBH24dTsqe4l042F1xFerdLE4RB32uM"}
```

Send the `url` to Ben yourself. It works once, and for 7 days (`FLOCK_INVITE_TTL`). Flock stores only a hash of the token, so this response is the only place you see it.

The link opens the dashboard's invitation page, which accepts it in one of three ways:
- **A new account.** The page posts a name and a password to `POST /api/v1/invites/{token}/accept`. Flock creates the account, adds it to the organization and signs it in.
- **An existing account.** Someone who already has an account signs in as the invited email, and the page posts an empty body to the same endpoint. That adds the membership.
- **Google.** The page sends the browser to `/api/v1/auth/google/start?invite=<token>`. See [Setting up Sign in with Google](google-sign-in).

An invitation belongs to its email address. Accepting it as another account answers 403. Flock doesn't verify addresses: the link is the proof, so whoever holds it can create the account. Send links privately.

To list the invitations that are still pending, use `GET /api/v1/orgs/flock/invites`. To withdraw one, delete it by its `id`: `DELETE /api/v1/orgs/flock/invites/1`. Inviting someone who is already a member answers 409.

> [!IMPORTANT]
> Set `FLOCK_WEB_URL` to your dashboard's address. Invitation and reset links point there. Without it, they point at Flock itself, which has no page to show them.

## Changing roles and removing members

An org admin changes a role with the member's user id, which `GET /api/v1/orgs/flock/members` lists:

```bash
curl -s -X PATCH --json '{"role": "admin"}' -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/orgs/flock/members/2
```

`DELETE /api/v1/orgs/flock/members/2` removes the member. Members can leave an organization by deleting their own membership. Runs that a removed member logged stay in the organization, and they keep their owner.

## Password resets

There is no "forgot password" email. An admin creates a one-time reset link instead:

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/users/2/password-reset
```

```json
{"url":"http://localhost:5173/reset/y95n5Sj37gh5zkzoP-ovo-S8sBqTpxCuBdQXpfz4cv0","expires_at":1792368626.964961}
```

- **Who can:** a super admin can reset anyone's password. An org admin can reset the password of a member of an organization they administer, unless that member is a super admin.
- **How long:** the link works once, for 24 hours (`FLOCK_RESET_TTL`).
- **What it does:** the dashboard's reset page posts the new password to `POST /api/v1/password-resets/{token}`. That ends every session of the user and signs them in again.

An org admin who resets a member's password can sign in as that member, which includes the member's access to other organizations. Give the org admin role only to people you trust with that.

To change your own password, use `POST /api/v1/me/password` with `current` and `new`. Your other sessions end, and the current one continues. Accounts created through Google have no password, and can set one without `current`.

## Sessions

Signing in with `POST /api/v1/auth/login` returns two tokens:
- **An access token**, a JWT valid for 15 minutes (`FLOCK_ACCESS_TOKEN_TTL`). Send it as `Authorization: Bearer <access_token>`.
- **A refresh token**, valid for 30 days (`FLOCK_REFRESH_TOKEN_TTL`). Exchange it for new tokens with `POST /api/v1/auth/refresh` before the access token expires. Each refresh token works once, and every refresh extends the session by another 30 days.

These end a session at once, not when its access token expires:
- signing out with `POST /api/v1/auth/logout`
- changing the password (except the session that changed it)
- a password reset
- deleting the account

Passwords are 8 to 256 characters. Emails are stored in lowercase, and only checked for their shape.

## API keys

The `wandb` SDK and scripts use API keys. Each user creates their own:

```bash
curl -s --json '{"name": "laptop"}' -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/me/api-keys
```

- A key acts as its user, with all of their roles.
- Flock shows the key once, when it's created. Listings with `GET /api/v1/me/api-keys` show only its first 12 characters (`prefix`) and its `name`.
- Keys don't expire, and they keep working after a password change. To revoke one, delete it by its `id`: `DELETE /api/v1/me/api-keys/3`.

The [API overview](api/index#credentials) lists the ways to send a key.

## Avatars

Users and organizations can have an avatar image, which Flock stores and serves. Responses that show a user or an organization include its `avatar_url`.

**Gravatar.** When an account is created, Flock looks up the email's [Gravatar](https://gravatar.com) in the background. If there is one, it becomes the account's avatar. Accounts created before then, or whose owner sets up a Gravatar later, fetch it on request:

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/me/avatar/gravatar
```

Looking up a Gravatar sends gravatar.com the SHA-256 hash of the email. To turn the lookup off, set [`FLOCK_GRAVATAR`](configuration) to `false`.

**Uploads.** Users upload their own avatar, and org admins their organization's. The body is the image file, of at most 1 MiB:

```bash
curl -s -X PUT --data-binary @me.png -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/me/avatar
curl -s -X PUT --data-binary @flock.png -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/orgs/flock/avatar
```

**Showing them.** An `avatar_url` loads without credentials, so a page can use it as an `<img>` source. It's a path on Flock: a dashboard served from another address either forwards `/api/v1/avatars/*` to Flock, or puts Flock's address in front. The URL is the image's SHA-256, so it can't be guessed. It changes whenever the image does, which lets browsers cache each one for good. [Avatars](api/auth#avatars) lists the accepted formats.

## Managing user accounts

Super admins manage every account:
- `GET /api/v1/users` lists them.
- `PATCH /api/v1/users/{id}` with `{"is_superadmin": true}` makes a user a super admin, and `false` stops them being one.
- `DELETE /api/v1/users/{id}` deletes an account. Its runs stay, without an owner, so only admins can change them afterwards. Deleting the last admin of an organization leaves it without an admin, until a super admin promotes another member.

Other users can't delete accounts, including their own: they ask a super admin.
