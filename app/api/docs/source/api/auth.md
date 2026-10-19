# Sign-in and accounts

Endpoints for signing in, for your own account and API keys, and for managing users and password resets. Paths are relative to `/api/v1`. For how these fit together, see [Accounts, organizations and roles](../accounts).

## Token responses[[tokens]]

Signing up, signing in, refreshing, accepting an invitation as a new account, redeeming a password reset and exchanging a Google code all return tokens:

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJmbG9jay10cmFja2VyIi...",
  "token_type": "bearer",
  "expires_in": 900,
  "refresh_token": "cbFhTT9y9bV_vKma4FPS2Mbc86Gx6MBaR_kVDlhsDq0",
  "refresh_expires_at": 1794874194.31,
  "user": {"id": 1, "email": "ana@example.com", "...": "..."}
}
```

| Field | Meaning |
|---|---|
| `access_token` | A JWT to send as `Authorization: Bearer <access_token>`. |
| `token_type` | Always `bearer`. |
| `expires_in` | Seconds until the access token expires (`FLOCK_ACCESS_TOKEN_TTL`). |
| `refresh_token` | An opaque token for [`POST /auth/refresh`](#refresh). It works once. |
| `refresh_expires_at` | When the session ends unless it's refreshed. |
| `user` | The [user object](#user-object). Refreshing doesn't return it. |

## The user object[[user-object]]

| Field | Meaning |
|---|---|
| `id` | The user's integer id. Runs they log have it as their `owner`. |
| `email` | Email address, lowercase. |
| `name` | Display name. |
| `is_superadmin` | Whether the user is a super admin. |
| `has_password` | Whether the account has a password. Accounts created through Google don't. |
| `google` | Whether a Google account is linked. |
| `default_org` | The organization `wandb.init` logs into when no `entity` is given, or `null` if the user belongs to none. |
| `orgs` | The user's memberships, as `{"name", "role"}` objects sorted by name. `role` is `admin` or `member`. |
| `created_at` | When the account was created. |
| `avatar_url` | The path of the user's [avatar](#avatars) image, such as `/api/v1/avatars/3f2a…`, or `null`. |
| `avatar_source` | `gravatar` or `upload`: where the avatar came from. `null` without one. |

## Signing in

### `GET /auth/config`[[auth-config]]

Public. What a sign-in page should offer:

```json
{"signup_open": false, "google": true}
```

`signup_open` is `true` only while Flock has no accounts. `google` is `true` when Sign in with Google is configured.

### `POST /auth/signup`[[signup]]

Public. Creates the first account, which becomes a super admin.

| Field | Type | Meaning |
|---|---|---|
| `email` | string | Email address. Stored in lowercase. |
| `name` | string | Display name, 1 to 128 characters. |
| `password` | string | 8 to 256 characters. |
| `create_org` | boolean | Optional, `true` by default. When Flock has no organization yet, create one named after `FLOCK_DEFAULT_ENTITY` for the account. A dashboard that asks for the first organization's name passes `false`, then calls [`POST /orgs`](orgs#organizations). |

Answers 201 with [tokens](#tokens). Once an account exists, it answers 403: other people join through [invitations](orgs#invitation-links).

### `POST /auth/login`[[login]]

Public. Signs in with `email` and `password`. The email is matched in any case. Answers 200 with [tokens](#tokens), or 401 for a wrong email or password.

### `POST /auth/refresh`[[refresh]]

Public. Exchanges `{"refresh_token": "..."}` for new tokens, without the `user`. The refresh token used is replaced: send the new one next time. An expired, used or ended refresh token answers 401.

### `POST /auth/logout`[[logout]]

Ends the session of the access token that makes the request. Answers 204. Called with an API key, which has no session, it answers 400.

### Sign in with Google[[google]]

| Endpoint | Meaning |
|---|---|
| `GET /auth/google/start?invite=<token>` | Public. Redirects the browser to Google. `invite` is optional, and accepts that invitation on the way. |
| `GET /auth/google/callback` | Public. Where Google sends the browser back. Redirects to the dashboard's `/auth/google?code=<code>`, or to `/login?error=<reason>&message=<text>`. |
| `POST /auth/google/exchange` | Public. Exchanges `{"code": "..."}` for [tokens](#tokens). The code works once, for 60 seconds. Otherwise it answers 401. |

The start and callback answer 404 when Google sign-in isn't configured. [Setting up Sign in with Google](../google-sign-in) explains the flow and the error reasons.

## Your account[[me]]

### `GET /me`[[get-me]]

Returns your [user object](#user-object).

### `PATCH /me`[[update-me]]

Changes your profile. Fields you leave out don't change.

| Field | Meaning |
|---|---|
| `name` | Display name, 1 to 128 characters. |
| `default_org` | The organization `wandb.init` logs into when no `entity` is given. It must be one of yours, or it answers 404. `null` goes back to the first organization you joined. |

Returns your updated user object.

### `POST /me/password`[[change-password]]

Changes your password with `{"current": "...", "new": "..."}`. `new` is 8 to 256 characters. Accounts without a password leave out `current`. Answers 204, or 403 if `current` is wrong.

Your other sessions end. The session that made the request continues, and API keys keep working.

## Avatars[[avatars]]

| Endpoint | Meaning |
|---|---|
| `GET /avatars/{digest}` | The image behind an `avatar_url`. Public: it needs no credentials. |
| `PUT /me/avatar` | Sets your avatar. The body is the image itself, not JSON. Returns your user object. |
| `DELETE /me/avatar` | Removes your avatar. Returns your user object. |
| `POST /me/avatar/gravatar` | Replaces your avatar with your email's Gravatar. Answers 404 if Gravatar has none, 409 if `FLOCK_GRAVATAR` is off, and 502 if Gravatar can't be reached or sends something that isn't an image. Returns your user object. |

Images must be PNG, JPEG, GIF or WebP, of at most 1 MiB (1,048,576 bytes). Flock reads the type from the image's first bytes and ignores the `Content-Type` header. Other types, including SVG, answer 415. Larger images answer 413, and an empty body answers 422. Images are stored as they are, without resizing.

`{digest}` is the SHA-256 of the image, in hex. The bytes behind a URL never change, so responses can be cached for good (`Cache-Control: public, max-age=31536000, immutable`), and `If-None-Match` with the `ETag` answers 304. A new avatar has a new URL. An image nobody uses any more is deleted, and its URL answers 404.

## API keys[[api-keys]]

| Endpoint | Meaning |
|---|---|
| `GET /me/api-keys` | Lists your keys as `{"keys": [...]}`, oldest first. Each has `id`, `name`, `prefix` (the key's first 12 characters) and `created_at`, but not the key. |
| `POST /me/api-keys` | Creates a key. The body's `name` is 1 to 128 characters, `default` if left out. Answers 201 with the listing fields plus `key`, which is shown only this once. |
| `DELETE /me/api-keys/{id}` | Revokes one of your keys at once. Answers 204, or 404 if you have no key with this id. |

Keys look like `flock-` followed by 48 hexadecimal characters, the shape the `wandb` SDK accepts. They don't expire.

## Users[[users]]

Super admins only. Others get 403.

| Endpoint | Meaning |
|---|---|
| `GET /users` | Every account, as `{"users": [...]}` of [user objects](#user-object), oldest first. |
| `PATCH /users/{id}` | `{"is_superadmin": true}` makes a user a super admin, and `false` stops them being one. Returns the updated user object. Demoting the last super admin answers 409. |
| `DELETE /users/{id}` | Deletes an account, with its API keys and sessions. Its runs stay, with no owner. Answers 204. Deleting the last super admin answers 409. |

An unknown `id` answers 404.

## Password reset links[[password-resets]]

### `POST /users/{id}/password-reset`[[create-reset]]

Creates a one-time link that sets a new password for user `id`. It takes no body. Answers 201:

```json
{"url": "https://flock.example.com/reset/y95n5Sj37gh5zkzoP-ovo-S8sBqTpxCuBdQXpfz4cv0", "expires_at": 1792368626.96}
```

The link is valid for `FLOCK_RESET_TTL` (24 hours by default). Several links for one user can be valid at once.

Super admins can reset anyone's password. Org admins can reset the password of members of an organization they administer, except super admins, which answers 403. Members get 403. A user who shares no organization with you answers 404.

### Redeeming a link[[redeem-reset]]

| Endpoint | Meaning |
|---|---|
| `GET /password-resets/{token}` | Public. Returns `{"email": "..."}` of the account the link resets. |
| `POST /password-resets/{token}` | Public. Sets the password from `{"password": "..."}`, 8 to 256 characters. Ends every session of the user, then answers 200 with new [tokens](#tokens). |

An invalid, expired or used token answers 404.
