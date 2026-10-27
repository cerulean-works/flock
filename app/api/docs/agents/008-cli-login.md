# 008: `flock login`, device sign-in for the command line

| | |
| --- | --- |
| Status | applied |
| Date | 2026-10-27 |
| Builds on | [003](003-organizations-and-auth.md) |
| Amends | D20 (a third kind of credential, the CLI token), D24 (new auth and `me` routes) |
| Keeps | D2 (one server per database, small state in memory), D19, D20's API keys and JWTs as they are, D23 |
| Result | 272 tests in `tests/app/` and `tests/cli/` pass (18 new in `tests/app/`, 36 in `tests/cli/`), and 315 in `app/web`; see [section 5](#5-how-it-was-actually-built) |

## 1. Goal

Flock code on a laptop or a training node can only authenticate today with an API key, which
the user creates in the web app and copies into an environment variable or `wandb login`.
This stage makes Flock work like `huggingface-cli login` or `gh auth login`: Flock keeps its own
sign-in on the machine, and every Flock client picks it up.

1. **A `flock` command.** The `flock/cli` package (an empty stub today) becomes the `flock`
   console script, with four commands: `flock login`, `flock logout`, `flock whoami` and
   `flock token`.
2. **Device sign-in.** `flock login` prints a short code and a URL. The user opens the URL in
   any browser where they're signed in to Flock, types the code and approves. The CLI then
   holds a credential for that machine. This is the OAuth 2.0 device authorization grant
   (RFC 8628), the flow GitHub's CLI uses.
3. **wandb, optionally.** After signing in, `flock login` asks whether to also log the wandb
   SDK in to this Flock server, and does it if the user says yes.
4. **Picked up automatically.** The sign-in is saved to a file that one small function reads.
   Later Flock clients (the hub's S3 credentials first) call that function, so nobody has to
   handle a key by hand.

API keys stay as they are, for the wandb SDK (it can only send an API key) and for machines
nobody signs in on, such as CI.

**Out of scope:**
- other commands (the existing `flock-eval` stays its own script for now);
- switching any existing client to the new credential: the hub's S3 credentials come in the hub
  PR, and `flock.eval` already logs through the wandb SDK;
- several saved servers or profiles at once;
- signing in with no browser at all, for example with email and password typed into the
  terminal.

---

## 2. Design decisions

The numbering continues from 007.

### D46. The device authorization grant

Four routes under `/api/v1/auth/device`. Field names and error codes follow RFC 8628, so
the flow is the familiar one and any standard client could drive it.

| Route | Who | Does |
| --- | --- | --- |
| `POST auth/device` `{client: {hostname, platform}}` | public | starts a sign-in. Returns `{device_code, user_code, verification_uri, verification_uri_complete, expires_in, interval}` |
| `POST auth/device/token` `{device_code}` | public | the CLI polls this. Returns 400 `{error}` with `authorization_pending`, `slow_down`, `access_denied` or `expired_token`, until the user approves. Then it returns the CLI token (D47) and `{user}` once |
| `GET auth/device/{user_code}` | signed-in browser | what the approval page shows: the requesting machine's hostname, platform and IP address, and when it asked |
| `POST auth/device/{user_code}` `{approve: bool}` | signed-in browser | approves the sign-in as the current user, or denies it |

- **Codes.**
  - The `device_code` is 32 random bytes. Only the CLI ever sees it.
  - The `user_code` is 8 letters from GitHub's alphabet (`BCDFGHJKLMNPQRSTVWXZ`, no vowels and
    no lookalikes), shown as `WDJB-MJHT`. Case and the dash are ignored when it's typed.
  - Both live 15 minutes (`FLOCK_DEVICE_CODE_TTL`). The poll interval is 5 seconds, and each
    `slow_down` adds 5 more, as RFC 8628 says.
- **Kept in memory, not in Postgres.** A pending sign-in only matters for 15 minutes, and D2
  makes this process the only server. That's the same choice as Google's one-time codes
  (`Accounts.issue_code`). A restart loses pending sign-ins, and the CLI then says to run
  `flock login` again. Pending sign-ins are swept on expiry and capped at 1000, so the public
  start route can't grow memory without bound.
- **Only a browser session can approve.** The approve and deny routes take a JWT and refuse API
  keys and CLI tokens. Otherwise a leaked key could approve sign-ins for new machines.
- **Guessing codes.** A user code has about 35 bits, so it can't be guessed within 15
  minutes. Only signed-in users can try at all, and each user gets 10 wrong codes per 10
  minutes before getting 429.
- **Phishing.** Someone can start a sign-in and send the code to a victim. The approval page
  defends against this the way GitHub's does: it names the machine and its IP address, and
  says that approving gives that machine access to the account.

### D47. What a sign-in gives the CLI: a CLI token, a kind of session

An approved sign-in creates a **CLI session**: a row in `sessions` with `kind = 'cli'`. The CLI
gets an opaque token, `flock_cli_` followed by 43 random URL-safe characters, and sends it as
`Authorization: Bearer <token>`.

- **Why not the web's access and refresh tokens.** Refresh tokens rotate on every use (D20).
  On a training node, eight DataLoader workers or several jobs on one machine share the
  credentials file. Two of them refreshing at once would sign the machine out. A file lock
  fixes that on one machine but not on a shared NFS home. A CLI token never rotates, so any
  number of processes can use it at once.
- **Why not an API key.** A CLI token belongs to a sign-in, not to the account.
  - It's listed by machine, with when it was last used.
  - Revoking it from the account page signs that machine out.
  - It ends, like any session, when the user changes their password, a password reset is
    redeemed, or the user is deleted (`Accounts.end_sessions`). API keys survive all of these.
  - API keys keep their D20 role: the wandb SDK, and machines without a person.
- **Storage.** Migration `0010_cli_sessions.sql` adds `kind text NOT NULL DEFAULT 'web' CHECK
  (kind IN ('web', 'cli'))`, `name text` (the machine's hostname) and `last_used_at
  timestamptz` to `sessions`.
  - A CLI session keeps its token's sha256 in `refresh_hash`, which is never rotated.
  - `POST auth/refresh` refuses CLI sessions.
  - In memory, `Accounts` maps token digests to CLI sessions, next to `keys`. Authenticating
    costs one sha256 and a dict lookup, the same as an API key, so D20's per-request budget
    holds.
- **Expiry.** A CLI session expires after 90 idle days (`FLOCK_CLI_SESSION_TTL`). Each use
  pushes `expires_at` and `last_used_at` forward. Both are written to the database at most once
  an hour per session, so authenticating stays a dict lookup.
- **Where it's accepted.** Everywhere `current_user` is: the REST API, and later the hub. The
  wandb protocol accepts it too, though the SDK never sends one. `POST auth/logout` with a CLI
  token ends that CLI session, for `flock logout`.
- **Telling credentials apart** (D20's rule, extended): a Bearer value with two dots is a JWT;
  one starting with `flock_cli_` is a CLI token; anything else is an API key.

### D48. Seeing and revoking CLI sign-ins

- `GET me/cli-sessions` lists the user's CLI sessions: `{id, name, created_at, last_used_at,
  expires_at}`. `DELETE me/cli-sessions/{id}` ends one, and its token stops working on the
  next request.
- The web app's account page gets a "Command line sign-ins" section under API keys, listing
  each machine with a revoke button.

### D49. The approval page

A new web route, `/device`, behind sign-in. When the user isn't signed in, it goes to
`/login?next=/device?code=…`, using the existing `safeNext` redirect. It has three states:

1. **Enter the code.** One field, prefilled from `?code=` when the user follows
   `verification_uri_complete`.
2. **Confirm.** The machine (hostname, platform, IP address, how long ago it asked), the account
   it will act as, and the warning from D46. Buttons: "Authorize" and "Cancel".
3. **Done.** "You can close this tab and return to your terminal", or that the sign-in was
   denied. An expired or unknown code goes back to step 1 with an error.

It follows `design.md`, and uses the setup and login pages' form layout.

### D50. The `flock` command

- **Package.** `flock/cli` becomes a real package. `flock/cli/main.py` holds a click group (click
  is what `flock-eval` uses). The root `pyproject.toml` gets `flock = "flock.cli.main:main"`
  under `[project.scripts]`. Its dependencies: `click` and `httpx`.
- **The server.** `--host` is the Flock API's URL, the same address the wandb SDK uses.
  - **Precedence:** `--host`, then `FLOCK_HOST`, then the server of the current sign-in, then
    the default, `https://flock-api.cerulean.works`.
  - **The browser page.** The CLI doesn't need the web app's address: the server builds
    `verification_uri` from its `FLOCK_WEB_URL` (D46).
- **`flock login [--host URL] [--no-browser] [--wandb | --no-wandb]`**
  - **The flow.**

    ```
    $ flock login
    ! Copy your one-time code: WDJB-MJHT
    Press Enter to open https://flock.cerulean.works/device in your browser...
    ✓ Logged in to https://flock-api.cerulean.works as ada@example.com
    Also log the wandb SDK in to this Flock server, for experiment tracking? [Y/n]
    ```

  - **Opening the browser.** On Enter, the CLI opens `verification_uri_complete` (with
    `webbrowser`) and polls at the server's interval. With `--no-browser`, without a TTY, or
    over SSH (`SSH_CONNECTION` is set), it only prints the URL and the code. That's the case on
    a training node.
  - **Already signed in.** If the saved token still works for this server, it says
    `Already logged in as … . Log in again? [y/N]`.
  - **Exit codes.** Denied, expired or interrupted sign-ins exit 1 with a one-line reason.
    Nothing is saved until the sign-in succeeds.
- **`flock logout`**
  - **Ending the session.** It ends the CLI session on the server with `POST auth/logout`,
    which now also accepts a CLI token as its Bearer credential and ends that session. Then it
    deletes the credentials file.
  - **Server unreachable.** If the server can't be reached, it still deletes the file. It
    warns that the session stays listed on the account page until it's revoked there or expires.
  - **What it leaves alone.** It doesn't touch the wandb SDK's login or the API key D52
    created. It says so, and names the key, so the user can revoke the key from the account
    page.
  - **A token from the environment.** When the credential comes from `FLOCK_TOKEN` rather than
    the file, `flock logout` changes nothing and exits 1, saying to unset the variable.
- **`flock whoami`**
  - **Output.** It calls `GET me` with the resolved credential and prints:
    - the user's name and email;
    - the server;
    - their orgs, each with its role, and the default org marked;
    - where the credential came from (`flock login`, or `FLOCK_TOKEN`).
  - **Machine-readable.** `--json` prints `GET me`'s answer plus `{host, source}`
    instead.
  - **Errors.** If not signed in, or the server refuses the credential, it exits 1 and says to
    run `flock login`.
- **`flock token`**
  - **Output.** It prints the resolved token and a newline to stdout, and nothing else. Scripts
    use it to hand the sign-in to another tool, for example
    `curl -H "Authorization: Bearer $(flock token)"` or `FLOCK_TOKEN=$(flock token)` on a
    remote job.
  - **No server call.** It doesn't contact the server, so it works offline and costs nothing.
    Checking the token is `whoami`'s job.
  - **Errors.** It exits 1, with the reason on stderr, when there's no credential.

### D51. Where the sign-in is saved, and how clients pick it up

- **The file.** `<config dir>/credentials.json` holds `{"host", "token", "user", "created_at"}`, where
  `host` is the API URL.
  It's written atomically (a temporary file, then rename) with mode 0600. The config dir is
  `FLOCK_CONFIG_DIR`, else `$XDG_CONFIG_HOME/flock`, else `~/.config/flock`. Signing in to
  another server replaces the file.
- **The one function every client calls.** A new module, `flock.cli.credentials`, uses only
  the standard library, so any Flock package can depend on `flock-cli` cheaply.
  - `resolve(host=None, token=None) -> Credentials | None` picks the first that exists:
    1. explicit arguments;
    2. `FLOCK_TOKEN` (a CLI token or an API key, since both are Bearer values) with
       `FLOCK_HOST`, which defaults to `https://flock-api.cerulean.works`;
    3. the credentials file.
  - `Credentials` carries `host`, `token` and `source` (`"argument"`, `"env"` or
    `"file"`).
  - `auth_headers()` returns the resolved `Authorization` header, for any client to use.
  - This order lets CI set an environment variable while laptops and nodes use `flock login`.
    It's the order `huggingface_hub` uses for `HF_TOKEN`.
- **Picked up automatically.** The hub (next PR) reads its server and token from `resolve()`,
  and S3 credentials come from `POST /hub/credentials`. So after `flock login`, `load_dataset`
  works with nothing else set.

### D52. Logging the wandb SDK in

Flock speaks wandb's protocol (001), so "log in to wandb" means pointing the wandb SDK at this
Flock server. Then `wandb.init`, and `flock.eval`'s tracking, go to Flock.

- **If the user says yes** (or passes `--wandb`):
  1. The CLI creates an API key named `wandb on <hostname>` with `POST me/api-keys`, using the
     new CLI token. The wandb SDK can only send API keys, so this is still a key, listed and
     revocable on the account page like any other.
  2. It calls `wandb.login(key=…, host=<host>, relogin=True, verify=True)`. That writes the key to
     `~/.netrc` and `base_url` to wandb's settings, the same as
     `wandb login --host <host> <key>`.
- **If the wandb SDK is already logged in to this host** with a key that still works, the CLI
  says so and skips the question. It doesn't create a second key.
- **If `wandb` isn't installed**, it isn't a dependency of `flock-cli`. The CLI then prints the
  `wandb login --host …` command and how to create a key, instead of asking.
- **The question's default is yes** (changed in review). Flock is where Cerulean tracks
  experiments, so pointing the wandb SDK at it is the common case. Saying yes points it at Flock
  for every project on the machine, including ones meant for wandb.ai, so `--no-wandb` opts out.

---

## 3. Plan

This is one PR, `feat/cli-login`. Run `tests/app` and the new `tests/cli` after every step.

### Step 1. Migration 0010 and CLI sessions in `Accounts`

`0010_cli_sessions.sql` per D47. In `accounts.py`:
- `Session` gains `kind`, `name` and `last_used_at`;
- token digests go in an in-memory map;
- new methods `start_cli_session`, `session_for_cli_token` (with the hourly write-back),
  `cli_sessions` and `end_cli_session`;
- `refresh` refuses CLI sessions.

**Done when** CLI sessions survive a restart and end with `end_sessions`.

### Step 2. Device sign-in in `Accounts`

A `DeviceGrant` record and the in-memory table: start, poll (with `slow_down`), approve, deny,
expiry sweep, the 1000 cap and the per-user limit on wrong codes. Add `FLOCK_DEVICE_CODE_TTL` and
`FLOCK_CLI_SESSION_TTL` to settings, both compose files and `docs/source/configuration.md`
(`test_settings.py` checks this).

### Step 3. Routes

The device routes in `api/auth.py`. In `deps.py`, `current_user` accepts CLI tokens, and a
`SessionUser` dependency accepts only JWTs, for approving. `me/cli-sessions` goes in
`api/auth.py` next to `me/api-keys`. `auth/logout` ends a CLI session when given a CLI token.

### Step 4. Web

The `/device` route (D49), proxy routes under `routes/api/v1/auth/device`, the account page
section (D48). Types go in `src/lib/types.ts`, with specs. Use `svelte-autofixer`, and run
`pnpm check && pnpm test`.

### Step 5. `flock/cli`

Write `main.py`, `login.py` (login and logout), `account.py` (whoami and token) and
`credentials.py`, and the package wiring (D50 and D51). The
`docs/source` pages (with the `write-docs` skill): installation, a reference page per
command, and how credentials are resolved. Add a docs build workflow for it, like the other packages'.

### Step 6. Tests

- `tests/app/test_device.py`:
  - the whole grant: pending, then approved, then a token once;
  - deny;
  - expiry;
  - `slow_down`;
  - approving with an API key or CLI token is refused;
  - the wrong-code limit;
  - the cap;
  - a code works only once.
- `tests/app/test_cli_sessions.py`:
  - a CLI token authenticates REST and the wandb protocol;
  - listing and revoking;
  - a password change or reset ends it;
  - idle expiry;
  - the write-back throttle;
  - `auth/refresh` refuses it.
- `tests/cli/`:
  - `flock login` against the real test server (`FlockServer`), with a stand-in for the
    browser that approves through the API;
  - the no-TTY and `--no-browser` output;
  - re-login;
  - the default host and its precedence;
  - denial and expiry exit codes;
  - `flock logout`: ends the session on the server; still deletes the file when the server is
    down; refuses with `FLOCK_TOKEN` set;
  - `flock whoami` (text and `--json`), signed out and with a revoked token;
  - `flock token`: stdout only, no network call (a transport that fails on any request);
  - the credentials file's mode and atomic write;
  - `resolve()`'s order;
  - the wandb branch: yes, no, already logged in and not installed, with `wandb.login`
    replaced by a fake that records its arguments.

### Step 7. Docs and status

Add the new routes and the CLI token to the API docs and the README. Mark this file `applied`,
with a "how it was actually built" section.

---

## 4. Settled in review

1. **"Log in to wandb"** means pointing the wandb SDK at Flock (D52).
2. **The default server** is the API at `https://flock-api.cerulean.works` (D50).
3. **CLI sessions** expire after 90 idle days, extended by use (D47).
4. **`flock logout`, `flock whoami` and `flock token`** are in scope (D50).

---

## 5. How it was actually built

The plan was followed in order, in four commits: the API (steps 1 to 3 and its tests), the CLI
(step 5 and its tests), the web app (step 4) and the docs (step 7). These are the places where
the code differs from the decisions above, or adds to them.

- **No proxy routes in the web app.** Step 4 planned proxy routes under
  `routes/api/v1/auth/device`. The CLI talks to the API's public address (D50), so it never
  needs the dashboard, and the approval page talks to Flock from the server like every other
  signed-in page: its load reads `GET auth/device/{user_code}` and a form action posts the
  decision. Entering the code is a plain `GET /device?code=…` form. After a decision the page
  redirects to `/device?done=approved|denied`, so a reload doesn't decide twice. A failed
  decision (the code expired meanwhile) lands back on step 1 with an error.
- **Approving creates the session lazily.** Approving only marks the grant. The CLI session
  (and its row) is created by the next poll, so a sign-in approved but never collected leaves
  nothing behind. The grant is dropped before the insert is awaited, so of two concurrent polls
  only one gets a token.
- **RFC 8628 details.** An unknown device code answers `expired_token` too (that is what a
  restart leaves the CLI with). `slow_down` is answered to polls sooner than 0.8 × the interval,
  so network jitter around a 5-second poll doesn't trip it. Errors carry an `error_description`.
  A full table of pending sign-ins answers 503. Wrong codes count on both the `GET` and the
  `POST` with a user code; past the limit, `TooManyAttempts` (new in `core/errors.py`) maps to
  429.
- **The hourly write-back is scheduled from FastAPI's thread pool.** `current_user` is a sync
  dependency, so it runs in a worker thread with no event loop. `session_for_cli_token` hands the
  write to the loop `Accounts.load` ran on, with `call_soon_threadsafe`. Because the database's
  `expires_at` can lag the in-memory one by up to an hour, a session idle for 90 days can be
  deleted up to an hour early after a restart.
- **`SessionUserDep`.** The browser-only dependency is `deps.session_user`, used as
  `SessionUserDep` like the other `*Dep` aliases. A CLI token's session is also kept in
  `request.state.session`, so `POST auth/logout` ends it, and `POST me/password` sent with a CLI
  token keeps that CLI session like it keeps a browser's.
- **`flock.cli.api`.** A fifth module beside the four planned: the small httpx client the
  commands share, kept out of `credentials.py` so that one stays standard-library only (a test
  checks its imports against `sys.stdlib_module_names`). Its `TRANSPORT` hook is how the
  `flock token` test proves no request is made.
- **`resolve()` never sends the saved token to another server.** `FLOCK_HOST` (or `host=`) also
  decides whether the saved sign-in applies: when it names another server, `resolve()` returns
  `None` rather than the file's token. Hosts are normalized (no trailing slash, `https://` added
  when no scheme is given). `auth_headers()` returns `{}` without a credential.
- **The credentials file can name the wandb key.** When `flock login` creates the D52 key, it
  adds `wandb_key: {name, prefix}` to the file, which is how `flock logout` names the key it
  leaves. The file is otherwise as D51 says.
- **Without a terminal** (stdin and stdout not both TTYs), `flock login` keeps a working
  sign-in instead of asking to replace it, and logs the wandb SDK in without asking (the
  question's default) unless `--no-wandb` is given. EOF at "Press Enter" aborts like Ctrl-C.
- **Detecting an existing wandb login** reads the SDK's own state: `base_url` from
  `wandb.sdk.wandb_setup.singleton().settings`, and the key from those settings
  (`WANDB_API_KEY`) or `wbauth.read_netrc_auth`. These are not public APIs, so any error reading
  them counts as "not logged in". The key is then checked against `GET /api/v1/me`. A wandb key
  that no longer works is replaced with a new one.
- **Tests.** The session's `postgres` fixture moved to `tests/conftest.py` (calling
  `tests.app.conftest.serve_postgres`), so `tests/app` and `tests/cli` share one container. The
  CLI tests run `flock` in-process with click's `CliRunner` against a real `FlockServer` whose
  device interval is 50 ms. The browser stand-in signs in with an access token, reads the
  pending code from the server's grant table (what a person reads off the terminal; each test
  then checks the printed code matches), and approves through the API. The wandb tests isolate
  the SDK with `WANDB_CONFIG_DIR`, `NETRC` and `wandb.teardown()` between tests.
- **Docs build.** `.github/workflows/flock-cli-docs.yml` builds `flock/cli/docs/source` on
  changes, like the hub's workflow on its branch, without publishing (the `flock` site name is
  the app's).

Checked by hand against a local Flock: `flock login --host <server> --no-browser` printed the
code, an approval through the API with a browser session's JWT completed it, and `flock
whoami` showed the account. The `/device` page (sign-in redirect, confirm, done) and the
account page's revoke button were also tried in a browser; after the revoke, `flock whoami`
reported the credential refused and `flock logout` still cleaned up.
