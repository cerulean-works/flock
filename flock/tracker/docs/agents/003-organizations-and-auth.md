# 003: organizations and authentication

| | |
| --- | --- |
| Status | applied |
| Date | 2026-10-18 |
| Builds on | [001](001-wandb-compatible-tracker.md), [002](002-projects-and-crud.md) |
| Supersedes | D8 (API keys mapped to usernames, or any key accepted) |
| Amends | D12 (projects belong to an org rather than a free-form entity), D17 (a new `project` event payload) |
| Keeps | D2 (in-memory working set, one server per database), D3, D11, D13–D16 |
| Result | 161 tests in `tests/tracker/` pass; see [section 5](#5-how-it-was-actually-built) for where the build departed from the plan |

## 1. Goal

Until now the tracker trusted whoever held a key. `TRACKER_API_KEYS` mapped keys to usernames,
and when it was empty, any key was accepted. An entity was a free-form string on each
project. This stage adds:

1. **Organizations.** An org is a wandb entity: `wandb.init(entity="acme")` logs into org
   `acme`. Orgs are records, and they own projects. Only a super admin creates them.
2. **Accounts.** Users sign in with email and password, or with Google. Sessions are JWTs.
   The wandb SDK keeps using API keys, which are now per user.
3. **Three kinds of users:**
   - the **super admin**: the first user to sign up. Creates orgs, and administers every org
     and every user;
   - **org admins**: invite users, change roles, rename or delete the org, update or delete
     projects, and edit, move or delete any run in the org;
   - **members**: read the org, create projects, log runs, and edit, move or delete their own
     runs.
4. **Invite links instead of email.** The tracker never sends mail. Creating an invite (or a
   password reset) returns a link that the admin passes on. Email addresses aren't verified;
   the link is the proof.

**Out of scope:**
- sending email, and verifying email addresses;
- the flock-ui pages for login, invites and resets (the links point at flock-ui; the tracker
  provides the API);
- SSO other than Google, SCIM, per-project permissions, and service accounts (a user's API
  key serves that purpose);
- moving projects between orgs.

**Constraint:** the latency budgets from 001 still hold. Every request is now authenticated,
so authenticating must cost microseconds, not a database round trip.

---

## 2. Design decisions

The numbering continues from 002.

### D18. Organizations are wandb entities

Migration `0004_orgs_and_users.sql` adds `orgs(pk, name UNIQUE, created_at, updated_at)`,
backfilled from the distinct `projects.entity` values. `projects.entity` is replaced by
`org_pk REFERENCES orgs ON DELETE CASCADE`, with `UNIQUE (org_pk, name)`.

- **In memory:** an `Org` class next to `Project` in `store.py`. `Project.org` is a reference,
  and `Project.entity` becomes a property reading `org.name`, the same pattern 002 used for
  `Run.project`. `Store.orgs` (by name) and `Store.orgs_by_pk` hold them. The `_catalog` lock
  now also guards org names.
- **No implicit orgs.** `wandb.init` still creates projects, but only in an existing org.
  An unknown org is `NotFound`. Only a super admin creates orgs.
- **Rename** mirrors `update_project`: take every project lock of the org; refuse with 409
  (and the ids) while any run is active, since live clients address runs by entity; re-key
  `Store.projects`; send each project's streams a `project` event `{"entity_renamed_to":
  name}`, then close them.
- **Delete** mirrors `delete_project`: refused while runs are active unless `force`; one
  `DELETE FROM orgs` cascades through projects, runs and everything they logged; then memory
  is cleaned up, events are sent, and files are removed in a thread.
- **Org names** follow the same rule as project names (the `Name` validator).

### D19. Users, memberships and roles

Migration 0004 also adds:

```sql
users(pk, email UNIQUE, name, password_hash NULL, google_sub UNIQUE NULL,
      is_superadmin, default_org_pk NULL, created_at)
memberships(user_pk, org_pk, role CHECK (role IN ('admin', 'member')), created_at,
            PRIMARY KEY (user_pk, org_pk))          -- cascades from both sides
runs.owner_pk REFERENCES users ON DELETE SET NULL
```

Emails are stored lowercased. A user can belong to several orgs, with a role in each. Their
**default org** is the entity `wandb.init` uses when none is given: the first org they joined,
until they change it.

| Who | Can |
| --- | --- |
| super admin | everything an org admin can, in every org (whether or not they're a member); create orgs; list, promote, demote and delete users (never the last super admin) |
| org admin | invite users; change roles and remove members (never the last admin); rename or delete the org; update or delete projects; edit, move or delete any run in the org |
| member | read everything in the org; create projects (API or `wandb.init`); log runs; edit, move or delete runs they own; leave the org |
| anyone else | a 404 for the org and everything under it, so an org's existence isn't revealed |

- **Run ownership.** `runs.owner_pk` is set when a run is created and never changes. It shows
  in run details, and as a filterable `owner` column (the user's pk). Runs logged before this
  stage have no owner, so only admins can change them.
- **Writes from the SDK.** `upsert_run` and `file_stream` take the writer and raise a new
  `Forbidden` (403) when a non-admin writes to someone else's run. The check runs in the
  store under the project lock, so two users creating the same run id can't race.
- **Bulk operations by members.** If a selection contains runs the member doesn't own, the
  request fails with 403 `{error, ids}` and nothing changes, like the 409 for conflicts.
  Members who want only their own runs filter on `owner`.

### D20. Credentials

- **Passwords:** argon2id (`argon2-cffi`), 8–256 characters. Hashing and verifying run in
  `asyncio.to_thread`: they take tens of milliseconds of CPU and must not stall the event loop.
  A login for an unknown email verifies against a dummy hash, so timing doesn't reveal which
  emails have accounts.
- **API keys.** The wandb SDK can only send an API key (Basic auth, `api:<key>`), so keys stay
  and become per user. The format is `flock-` plus 48 hex characters, which passes the SDK's
  `check_api_key`. Keys are stored as a sha256 hash in `api_keys(pk, user_pk, name, prefix,
  hash UNIQUE, created_at)` and shown once, when created. The REST API accepts them too, so
  flock-ui's server-side `TRACKER_API_KEY` keeps working with a user's key.
- **JWT access tokens:** HS256 (`pyjwt`), with `sub` (user pk), `sid` (session pk), `iat`,
  `exp`, `iss` and `typ: "access"`. They live 15 minutes (`TRACKER_ACCESS_TOKEN_TTL`). No roles
  go in the token: memberships are looked up on every request, so a role change applies at
  once.
- **Refresh tokens and sessions:** opaque random strings, stored hashed in `sessions(pk,
  user_pk, refresh_hash, created_at, expires_at)`, valid 30 days, rotated on every refresh.
  Every request also checks that the token's `sid` is still a live session, in memory. So
  logging out, changing a password, a password reset or deleting the user revokes access
  immediately rather than when the access token expires.
- **The signing secret** comes from `TRACKER_JWT_SECRET`, or else from a random `jwt_secret`
  row the migration writes to `meta`, as 0001 does for `upload_secret`.
- **Which credential is which:** a Bearer value with two dots is a JWT; anything else is an API
  key. `x-api-key` and Basic auth carry API keys.
- **Cost per request:** users, memberships, key hashes and sessions are all loaded into memory
  at startup (D2 makes this one process the only writer). An API key costs one sha256 and a
  dict lookup; a JWT costs one HMAC.
- **Removed:** `TRACKER_API_KEYS` and the "accept any key" mode. Auth is always on.

### D21. Bootstrap, invites and password resets

- **The first signup.** `POST /auth/signup` (or a first Google sign-in) works only while there
  are no users, under a lock. That user becomes the super admin and an admin of every existing
  org; if there are none, the org `TRACKER_DEFAULT_ENTITY` (`flock`) is created for them. After
  that, signup returns 403, and accounts are only created from invites.
- **Invites.** `invites(pk, org_pk, email, role, token_hash, created_by, created_at,
  expires_at)`, valid 7 days. Creating one returns `{url, expires_at}`, where `url` is
  `<TRACKER_APP_URL or public URL>/invite/<token>`. The token is stored hashed and shown once.
  - `GET /invites/{token}` (public) shows the org, email and role, and whether an account
    with that email exists.
  - `POST /invites/{token}/accept` with `{name, password}` creates the account. If the email
    already has an account, the call must instead be made while signed in as that user, and
    it adds the membership. Google sign-in accepts an invite through `start?invite=`.
  - An accepted invite is used up. Inviting someone who is already a member is a 409.
- **Password resets.** `password_resets(pk, user_pk, token_hash, created_by, created_at,
  expires_at)`, valid 24 hours. A super admin can create one for anyone; an org admin for
  members of an org they administer, but never for a super admin (that would escalate).
  Redeeming it sets the password, ends all the user's sessions and returns new tokens.
  **Accepted risk:** an org admin can take over a member's account, including the member's
  access to other orgs.

### D22. Google sign-in, as a redirect flow in the tracker

Configured by `TRACKER_GOOGLE_CLIENT_ID`, `TRACKER_GOOGLE_CLIENT_SECRET` and optionally
`TRACKER_GOOGLE_REDIRECT_URL` (default `<public URL>/api/v1/auth/google/callback`). Without a
client id and secret, Google sign-in is off.

1. `GET /auth/google/start?invite=` creates a state, a nonce and a PKCE verifier, stores them
   in a 10-minute HttpOnly, SameSite=Lax cookie signed as a JWT (stateless, so a restart
   doesn't break a sign-in in progress), and redirects to Google with scope `openid email
   profile`.
2. `GET /auth/google/callback` checks the state against the cookie, then exchanges the code
   (with the client secret and the PKCE verifier) at Google's token endpoint, using `urllib`
   in a thread. It validates the ID token's `iss`, `aud`, `exp`, `nonce` and `email_verified`.
   The signature isn't checked: the token came straight from Google over TLS, which OIDC Core
   §3.1.3.7 allows. That keeps JWKS fetching and `cryptography` out.
3. The user is found by `google_sub`; else an account with the same email is linked (Google
   verified the email); else the invite is accepted, if its email matches; else, if there are
   no users yet, this is the bootstrap signup; otherwise the sign-in is refused.
4. The callback redirects to `<app_url>/auth/google?code=<one-time code>`. The code lives in
   memory for 60 seconds, and `POST /auth/google/exchange {code}` swaps it for tokens. Errors
   redirect to `<app_url>/login?error=<reason>`.

**Deployment:** the browser must reach the callback, so either the tracker's public URL is
exposed, or flock-ui proxies `/api/v1/auth/google/*` to it.

### D23. The wandb protocol under auth

- **Viewer** answers with the key's user: `entity` is their default org, `username` and
  `email` their email, `admin` whether they're a super admin, and `teams` their orgs.
- **UpsertBucket, CreateRunFiles and `file_stream`** require membership of the run's org,
  and ownership of the run or the admin role. **RunResumeStatus and RunState** require
  membership.
- An unknown org, or one the user doesn't belong to, is the GraphQL error `organization X not
  found`.
- **Signed upload URLs** are unchanged: the signature is the authorization.

### D24. The API

All under `/api/v1`. Everything needs a JWT or an API key, except the routes marked public.

| Area | Routes |
| --- | --- |
| auth (public) | `GET auth/config`; `POST auth/signup`, `auth/login`, `auth/refresh`, `auth/logout`; `GET auth/google/start`, `auth/google/callback`; `POST auth/google/exchange` |
| me | `GET`/`PATCH me` `{name, default_org}`; `POST me/password` `{current, new}`; `GET`/`POST me/api-keys`; `DELETE me/api-keys/{id}` |
| orgs | `GET orgs` (yours; all for the super admin); `POST orgs` (super admin); `GET`/`PATCH`/`DELETE orgs/{org}` (`?force` on delete) |
| members | `GET orgs/{org}/members`; `PATCH orgs/{org}/members/{user}` `{role}`; `DELETE orgs/{org}/members/{user}` (an admin, or the member leaving) |
| invites | `GET`/`POST orgs/{org}/invites`; `DELETE orgs/{org}/invites/{id}`; public `GET invites/{token}`, `POST invites/{token}/accept` |
| users | `GET users`, `PATCH users/{id}` `{is_superadmin}`, `DELETE users/{id}` (super admin); `POST users/{id}/password-reset` (D21) |
| resets (public) | `GET`/`POST password-resets/{token}` |
| existing | project and run routes now check membership (404), the admin role (project `PATCH`/`DELETE`) and run ownership (403 with ids); `GET projects` lists only your orgs' projects |

Errors: 401 without valid credentials, 403 for a missing role or for runs you don't own (with
`ids`), 404 for orgs you don't belong to, 409 for conflicts (last admin, last super admin,
existing member, active runs).

---

## 3. Plan

Run the whole tracker suite after every step; the latency tests guard the read path.

### Step 1. Dependencies

`pyjwt` and `argon2-cffi` in `flock/tracker/pyproject.toml`; `uv lock`, then
`uv sync --all-packages --inexact`. **Done when** `uv lock --check` passes.

### Step 2. Migration 0004

`src/db/migrations/0004_orgs_and_users.sql`: orgs and their backfill, the `projects.org_pk`
swap, users, memberships, `api_keys`, `sessions`, `invites`, `password_resets`,
`runs.owner_pk`, and the `jwt_secret` row. **Done when** a `test_0004_creates_orgs_from_entities`
seeds projects at schema 0003 and finds one org per entity, every project pointing at its
org, and every run without an owner.

### Step 3. Orgs in the store

`Org`, `Project.org`, orgs loaded before projects, `create_org`/`update_org`/`delete_org`,
`ensure_project` refusing unknown orgs, `Run.owner` and the `owner` column, `Forbidden` and the
owner checks in `upsert_run` and `file_stream`. **Pitfall:** `project.entity` was a plain
attribute and is used for keys everywhere; keep it as a property and re-key on rename.

### Step 4. Accounts

`src/core/accounts.py`: `User` and `Accounts`, loaded in `Store.open` as `store.accounts`.
In-memory users, memberships, key hashes and sessions; signup and bootstrap, login, token
issue and check, refresh, API keys, invites, resets, roles, and user administration.
**Pitfall:** argon2 must run off the event loop.

### Step 5. Settings

Remove `api_keys` (and the `user:key` parsing). Add `jwt_secret`, `access_token_ttl`,
`refresh_token_ttl`, `invite_ttl`, `reset_ttl`, `app_url`, `google_client_id`,
`google_client_secret`, `google_redirect_url`. Update `compose.yaml`.

### Step 6. Authorization in the API

`deps.py`: `current_user` resolves a JWT or an API key to a `User`; `OrgDep` and `AdminOrgDep`;
`ProjectDep` checks membership; a helper refusing runs the user doesn't own. Then the routers:
new `auth.py` and `orgs.py`; ownership and role checks in `projects.py` and `runs.py`;
`wandb_protocol.py` per D23. **Done when** the existing suite passes with the test harness
bootstrapping a super admin and using its API key.

### Step 7. Tests

- `test_auth.py`: bootstrap and closed signup, login, refresh rotation, logout, expiry,
  tampered tokens, password change ending sessions, API keys, and everything surviving a
  restart.
- `test_orgs.py`: org CRUD, rename (409 while active, re-keyed, events) and delete
  (cascade); invites (new and existing accounts, expiry, reuse); roles and the last-admin rule;
  the permission matrix (member, admin, non-member); 403 with ids for bulk operations; resets,
  including that an org admin can't reset a super admin.
- `test_google.py`: a fake token endpoint; start, callback and exchange; linking by email;
  accepting an invite; bootstrap; a bad state, nonce, audience or unverified email.
- `test_wandb_integration.py`: the SDK with a member's key; the default entity from Viewer; a
  non-member entity refused; resuming another user's run refused.

### Step 8. Docs and status

README: auth, orgs and roles, the new settings and endpoints. Mark this file `applied`, with a
"how it was actually built" section.

---

## 4. Known gaps after this stage

- **No rate limiting** of logins; argon2's cost is the only brake.
- **Live streams** are authorized when they open. Removing a member doesn't end a stream they
  already have open.
- **Bootstrap race:** whoever reaches a fresh server first becomes super admin. Don't expose a
  fresh server before signing up.
- **Refresh token reuse** isn't detected; an old token is just invalid.
- **No self-service account deletion**, and no audit log.
- **flock-ui** needs login, invite and reset pages, the Google redirect proxy, and handling of
  the `project` event's new payload.

---

## 5. How it was actually built

The plan was followed in order. These are the places where the code differs from the decisions
above, or adds to them.

- **The store's exceptions moved to `src/core/errors.py`** (`NotFound`, `Discarded`,
  `Conflict`, and the new `Forbidden`). `store.py` builds `Accounts`, and `accounts.py` raises
  the same exceptions, so keeping them in `store.py` would have been an import cycle.
  `app.py` now maps `NotFound` to 404 and `Forbidden` to 403 (with `ids`) globally.
- **The default org is mostly derived.** `users.default_org_pk` is only set when a user picks
  one (`PATCH /me`). Otherwise the default is the first org the user joined, so joining and
  leaving orgs never writes it.
- **An org's creator becomes its admin.** `POST /orgs` also adds the super admin who created it
  as an admin member, so the org has an admin from the start and shows up in their Viewer
  `teams`.
- **Ownership checks:** the API checks bulk selections and single-run edits
  (`deps.check_owner`); the store checks SDK writes (`upsert_run` and `file_stream` take
  `owner` and `admin`), under the project lock as D19 says. `CreateRunFiles` checks in the
  protocol layer, since it writes nothing itself.
- **Accepting an invite with an existing account** works with any of the user's credentials
  (an access token or an API key), not only a session.
- **Passwords are hashed before a transaction opens**, so a connection is never held for the
  ~50 ms argon2 takes. Login passwords are capped at 256 characters like new ones, so a huge
  body can't buy a long hash.
- **Google:** `GET /auth/config` reports `{signup_open, google}`. The flow cookie is named
  `flock_google_signin` and scoped to `/api/v1/auth/google`. An email already linked to one
  Google account can't be claimed by another (403, `error=forbidden`).
- **Test harness:** `TrackerServer` signs up a super admin (`admin@flock.test`) and creates an
  API key for it on every fresh database (`bootstrap=False` skips this), and carries the key
  across restarts. `TrackerServer.add_user` creates further users through the real invite flow
  and returns a client on their own API key. Test responses now expose headers (for redirects
  and cookies). `test_google.py` replaces Google's token endpoint with a local fake by
  patching `Google.TOKEN_URL`.

Measured at this checkpoint, on the same machine as 001 and 002, with every request now
authenticated by an API key: filter and curve latencies are unchanged (filters 0.5–3 ms, warm
curves p50 ~2 ms, cold curves p50 ~4 ms); deleting 953 runs takes ~120 ms and moving 500 runs
~65 ms.
