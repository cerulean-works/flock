# 011: one credential per job, and a simpler account page

| | |
| --- | --- |
| Status | applied |
| Date | 2026-10-27 |
| Builds on | [003](003-organizations-and-auth.md), [008](008-cli-login.md), [009](009-s3-proxy.md), [010](010-storage-tab.md) |
| Amends | D20 (API keys become wandb keys, `flock-wandb-…`, accepted only where the wandb SDK goes), D51 (`FLOCK_TOKEN` takes a CLI token), D52 (the key `flock login` makes for wandb), D57 (who may exchange for S3 keys), D60 (where S3 keys are listed) |
| Keeps | D46–D50 (device sign-in, CLI sessions), D53–D59, D61–D69 |
| Result | 388 tests in `tests/app`, `tests/cli` and `tests/s3` pass on moto (6 new in `tests/app/test_credentials.py`, which absorbed `test_auth.py`'s API key test, and 1 in `tests/app/test_wandb_integration.py`); 381 in `app/web`, unchanged; see [section 6](#6-how-it-was-actually-built) |

## 1. Goal

Flock now has four kinds of credential, and some of them overlap. An "API key" is made for the
wandb SDK, but it also works for the REST API and can be exchanged for S3 keys, so the same
string ends up doing every job. This stage gives each kind one job and makes the account page
show that:

1. **One credential per job** (D70). A browser session is for the web app, a CLI token for
   Flock's own clients, a wandb key for the wandb SDK, and an S3 key for S3.
2. **wandb keys say what they are.** New ones start with `flock-wandb-` (D71), and they only
   work where the wandb SDK sends them (D72).
3. **A simpler account page** (D74): a profile header, and one keys section with a tab for
   wandb keys and a tab for S3 keys. A new key's secret is shown alone, without a command
   wrapped around it.

**Out of scope:**
- service accounts or org-owned tokens for CI (see [section 4](#4-known-gaps-after-this-stage));
- changing how browser sessions, CLI sessions or S3 keys work;
- removing Gravatar from the server. Only the account page's button goes.

---

## 2. Design decisions

The numbering continues from 010.

### D70. Which credential does what

| Credential | Looks like | Made by | Accepted by |
| --- | --- | --- | --- |
| browser session | a JWT (two dots) | signing in on the web | the REST API, from the web app |
| CLI token | `flock_cli_…` | `flock login` (D47) | the REST API and `POST s3/credentials`: the `flock` CLI and every `flock.*` package |
| wandb key | `flock-wandb-…` | the account page, or `flock login`'s wandb step | the wandb protocol only (D72) |
| S3 key | `FLK…` + derived secret | `flock.cli.s3`, `flock s3 credentials`, or the account page | the S3 proxy only |

This table goes in the API docs as the reference for "which credential do I use". Error
messages name the right credential: a wandb key sent to the REST API gets 401 "wandb keys only
work with the wandb SDK: use `flock login` or a browser session".

### D71. wandb keys start with `flock-wandb-`

- **The shape.** New keys are `flock-wandb-` followed by 40 random characters from
  `[A-Za-z0-9]`: 52 characters in all, about 238 bits of randomness.
- **Why 40 after the last dash:**
  - The current wandb SDK (0.31) accepts a key whose part after its first `-` is at least 40
    characters of letters, digits, `_` and `-`. Here that part is `wandb-` plus 40, which
    passes. Checked against `wandb.sdk.lib.wbauth.validation.check_api_key`.
  - Older SDKs required the part after the *last* `-` to be exactly 40 characters, which the
    40 random characters are. So the key passes both.
  - Today's `flock-` + 48 hex keys pass only the newer check.
- **Storage is unchanged.** Keys are still stored as a sha256 in `api_keys`, with their first 12
  characters (`flock-wandb-`) kept as the prefix. Every row in `api_keys` is a wandb key,
  including the older `flock-` ones (D73), so no new column is needed.
- **The routes** are renamed to say what they hold: `GET`/`POST me/wandb-keys` and
  `DELETE me/wandb-keys/{id}`. The old `me/api-keys` routes stay as aliases for one release.
  They list every wandb key and create new ones.
- **Who can create one:** any signed-in credential except a wandb key itself, so `flock login`
  can make one with its CLI token (D52).

### D72. Where a wandb key works

A wandb key is accepted by **the wandb protocol** (001's GraphQL, `file_stream`, and the file
upload and download routes the SDK uses), and nowhere else.

- **This doesn't limit wandb compatibility.** wandb's CLI and public API (`wandb.Api()`,
  `wandb sync`, `wandb pull`, …) only ever call wandb's own endpoints: `/graphql` and the file
  URLs it returns. They never call Flock's `/api/v1`, which only the Flock web app and Flock's
  own clients use. Every query those wandb tools need is a GraphQL operation, so it falls inside
  what a wandb key may use.
- **Today Flock's GraphQL only implements the logging calls** (`Viewer`, `UpsertBucket`,
  `RunResumeStatus`, `CreateRunFiles`, `RunState`). Reading projects, runs, history and files
  through wandb's public API needs further operations, a separate stage. Once added, they work
  with wandb keys with no change to this split.

- **Refused** by the REST API (`/api/v1/…`) and by `POST s3/credentials`. A leaked wandb key
  then gives access to tracking and nothing else: not storage, not account settings.
- **In code:** `current_user` in `deps.py` returns which kind of credential it saw. The REST
  routers depend on a `RestUser` that refuses wandb keys, and the wandb protocol's router on a
  `WandbUser` that takes wandb keys and the other kinds. One router-level
  dependency each, so no route can forget.
- **S3 keys** made for a wandb key (D57) are no longer possible. Existing ones keep working until
  they expire, which is 8 hours by default.

### D73. Existing keys stay valid, as wandb keys

Keys created before this stage (`flock-` + 48 hex) stay valid, and are wandb keys like any other.
They get no special treatment: no separate kind, no badge, no wider access. They're accepted
where every wandb key is (D72), and they're listed, and revoked, with the rest. Nobody used them
for the REST API or S3 exchange, so nothing deployed loses access. There's no migration.
People will rotate them to `flock-wandb-` keys soon, so no test covers the old format. The
implementer checks it once by hand instead (Step 6).

### D74. The account page

The page becomes four blocks, top to bottom:

1. **The profile header.**

   ```
   [ picture ]  Ada Lovelace
                ada@example.com
                [Change profile]  [Change password]
   ```

   - **Change profile** opens a dialog with the name field and the picture: change, remove,
     and the same type and size rules as today.
   - **Change password** opens a dialog with today's three fields and its "signs you out
     everywhere else" note.
   - The separate Picture, Name and Password sections go, and so does **Use Gravatar**.
     Removing a picture shows initials. The server's Gravatar fetch for new accounts (005) is
     unchanged.
2. **Organizations**, unchanged.
3. **Keys**, one section with two tabs: **wandb** and **S3**.
   - **The wandb tab:**
     - the user's wandb keys, older `flock-` ones included (D73);
     - a create form (name), and revoke;
     - a one-line explanation: "For the wandb SDK. Use it as `WANDB_API_KEY`, or paste it into
       `wandb login`."
   - **The S3 tab:** 009's S3 keys section moved here as it is. That's long-lived keys with
     org, expiry and read-only; ephemeral keys folded per machine; create and revoke.
   - **The tab is in the URL** (`?keys=wandb|s3`), as other pages keep their tabs.
4. **Command line sign-ins** (008), unchanged, below the keys.

### D75. A new key shows its secret, nothing else

When a key is created, the dialog or card shows **the secret alone**, with a copy button and
"Copy it now: it isn't shown again".

- **wandb keys:** the key itself, `flock-wandb-…`. No `wandb login --host … <key>` command, and
  no "Or set WANDB_BASE_URL and WANDB_API_KEY" line.
- **S3 keys:** the access key ID and the secret, each with a copy button, and the endpoint as
  a plain read-only field. No AWS CLI, boto3 or environment snippets. Those live once, in
  Storage's Connect panel (D64).
- **The same component** (`ApiKeyCreator`, renamed `WandbKeyCreator`) is used on the account
  page and on the empty Runs page.
  - **The empty Runs page** keeps its step-by-step instructions as text around it ("Install the
    SDK", "Log in with your key"). The key box itself shows only the key.
  - The host the SDK needs appears once, in that step's text: "Log in to
    https://flock-api.cerulean.works with this key".

---

## 3. Plan

One PR, `feat/auth-split`, stacked on `feat/storage-tab` (010).

### Step 1. wandb keys

`Accounts.create_api_key` makes `flock-wandb-` keys. The routes are `me/wandb-keys`, with the
`me/api-keys` aliases. No migration.

### Step 2. The split

- In `deps.py`: `current_user` records the credential's kind, and `RestUser` and `WandbUser` use
  it.
- The REST routers depend on `RestUser`, and `wandb_protocol.py` on `WandbUser`.
- `POST s3/credentials` takes CLI tokens and browser sessions.
- The error messages of D70.

### Step 3. The test harness

`FlockServer` bootstraps its admin with an API key today, and calls everything with it. Instead:
- REST calls use a CLI token, made through the device flow's store methods;
- wandb SDK tests use a wandb key;

This is the bulk of the diff, since most tests go through the harness.

### Step 4. Web

- The profile header and its two dialogs.
- The keys tabs.
- `WandbKeyCreator` and the S3 creator per D75.
- The empty Runs page.
- Removing Use Gravatar.
- Types and specs.

### Step 5. The CLI

`flock login`'s wandb step creates its key through `me/wandb-keys`, and `flock logout`'s message
calls it a wandb key. `FLOCK_TOKEN` docs say it takes a CLI token (`flock token`).

### Step 6. Tests

- **The credential matrix**: every kind against a REST route, a wandb protocol call,
  `POST s3/credentials` and the proxy, checking each answer and its error message.
- **wandb keys:**
  - the shape;
  - `check_api_key` from the installed wandb SDK accepts them;
  - a real `wandb.init` and `log` with one;
  - the REST API refusing them.
- **The `me/api-keys` aliases.**
- **By hand, not as a test** (D73): an old-format `flock-` key still logs a run through the
  wandb SDK and is refused by the REST API.
- **Web specs:** the header and dialogs, the tabs, a new key showing only its secret, and no
  Gravatar button.

### Step 7. Docs and status

- The D70 table in the API docs.
- The CLI docs (`FLOCK_TOKEN`).
- The README.
- Mark this file `applied`, with a "How it was actually built" section.

---

## 4. Known gaps after this stage

- **CI has no credential of its own.** It uses a person's CLI token (`flock token`, set as
  `FLOCK_TOKEN`), which ends when that person logs out or changes their password, and expires
  after 90 idle days. Service accounts are a later stage.

## 5. Settled in review

1. **wandb keys are restricted to the wandb protocol** (D72). wandb CLI and public API
   compatibility only needs `/graphql`, so it isn't affected.
2. **Existing `flock-` keys stay valid as ordinary wandb keys** (D73), with no special treatment.
3. **CI uses a person's CLI token for now** (`FLOCK_TOKEN=$(flock token)`). A credential of CI's
   own is a later stage (section 4).
4. **New keys show only their secret**, both wandb and S3 keys (D75).

## 6. How it was actually built

The plan was followed, in six commits: the web app (step 4, made first), the API (steps 1 and
2), the web's switch to the `me/wandb-keys` routes, the test harness and the new tests (steps 3
and 6), the CLI (step 5), and the docs (step 7).
These are the places where the code differs from the decisions above, or adds to them.

- **Public routes.** A router-level dependency would force a credential onto the routes that
  take none, so `auth.py`, `avatars.py` and `wandb_protocol.py` each have a second router,
  `public`, with no dependency: signing up and in, refresh, the device flow's start and poll,
  Google, invitation and reset links, `auth/config`, avatar images, and, on the wandb side, the
  signed upload URLs and the SDK's telemetry. `app.py` includes `auth.public` before
  `auth.router`, so `POST auth/device/token` isn't taken for `POST auth/device/{user_code}`.
  Accepting an invitation while signed in, the one public route that may read a credential,
  calls `rest_user` itself, so a wandb key gets the same 401 there.
- **`current_user`** still returns the `User`, as every route expects, and records the kind in
  `request.state.credential` (`browser`, `cli` or `wandb`). `rest_user` (`RestUser`) reads it;
  `wandb_user` (`WandbUser`) takes any kind. Routes keep `UserDep`, which FastAPI resolves once
  per request, so the router's check costs nothing extra. Router dependencies run before the
  route's own, so a wandb key gets the D70 message, not a 404 from a project lookup.
- **No credential, or an unknown one,** answers 401 "invalid or missing access token, CLI token
  or wandb key" everywhere, the wandb protocol included.
- **Dead branches went.** `POST auth/logout` no longer has the "not an API key" 400 (a wandb key
  gets the 401). `POST s3/credentials`, `presign` and the bucket events' `via` no longer handle
  a wandb key; the core still takes `api_key` for S3 keys issued before D72, which list and end
  with their wandb key as before.
- **The key's shape** comes from `secrets.choice` over letters and digits, 40 times.
  `Accounts.WANDB_KEY_PREFIX` holds the prefix. `seed_demo.py` makes its super admin's key the
  same way.
- **The aliases** are the same handlers under a second decorator, marked `deprecated` in the
  OpenAPI schema. The response shape is unchanged, so the web's `WandbKey` types didn't change;
  only the account page's server calls moved to `me/wandb-keys`.
- **`flock login`** checked whether the SDK's key already works with `GET me`, which a wandb key
  can no longer call. It asks `/graphql`'s `Viewer` instead, as the SDK does. `flock whoami`
  shows the server's reason when `FLOCK_TOKEN` holds a wandb key, instead of "revoked or
  expired".
- **The test harness.** `FlockServer.bootstrap` gives the super admin a wandb key (`api_key`)
  and a CLI token (`token`, through `Accounts.start_cli_session`), and `add_user` gives each user
  both (`User.token` is new). A `Client` with both sends the token to `/api/…` and the wandb key
  everywhere else; with one, it sends that one everywhere, so a test can still aim a single
  credential at any route. Existing tests barely changed. The ones that did: listings of CLI
  sessions now include the user's `tests` session (`tests.app.utils.machines` leaves it out in
  the CLI tests); a password change ends `user.api`'s REST access, so those tests check the wandb
  key still works instead; and tests that used an API key for REST, `FLOCK_TOKEN` or S3
  exchange use the CLI token, or check the 401.
- **Tests.** `tests/app/test_credentials.py` has the matrix (each kind against `GET me`,
  `Viewer`, `POST s3/credentials` and the proxy, with each error), wandb keys sent all three
  ways, their shape, creation by each credential but a wandb key, revocation, the `me/api-keys`
  aliases, and invitations. `test_wandb_integration.py` checks `check_api_key` and logs a run
  with a new key. `test_s3_keys.py` checks that S3 keys issued to a wandb key before D72 still
  list and end with it.
- **The web app** (the first commit) differs from D74 and D75 in a few places:
  - With no S3 on the server there are no tabs: the Keys section shows the wandb keys alone.
  - An account without a password (Google sign-in) gets **Set password** instead of **Change
    password**, in the header and the dialog, without the current-password field.
  - The password toast says "Password changed" or "Password set" from whether the account had
    a password when the form was sent. The reloaded profile always has one, so reading it
    afterwards said "changed" every time.
  - The interface says "a wandb key" (lowercase, as wandb writes its name) everywhere it said
    "an API key".
  - The empty Runs page's step names only the host ("Log in to https://… with this key"); the key
    box below it shows only the key, and installing the SDK is a step of its own.

Checked by hand against a local Postgres, API and web dev server: signing up, creating a wandb
key on the account page (it starts with `flock-wandb-`), logging a run with it through the wandb
SDK, `curl` with it against `/api/v1/me` (401 with the D70 message, sent as Bearer, Basic and
`x-api-key`), `flock login` against the local API approved through the API, then `flock
whoami` (and `flock whoami` with the wandb key in `FLOCK_TOKEN`, which shows the D70 message).
A second `flock login --wandb`, with `NETRC` and `WANDB_CONFIG_DIR` pointed at a scratch
folder, made a `flock-wandb-` key with its CLI token and logged the SDK in with it, and
`flock logout` then named it as a wandb key. An old-format key (`flock-` and 48
hex characters, inserted into `api_keys` directly) logged a run through the wandb SDK and got
the same 401 from `/api/v1/me` (D73).
