# CLI command index

Run `uv run flock COMMAND` from the repository root, or `flock COMMAND` in the activated workspace environment. All commands accept `--help`.

| Command | Purpose | Reference |
|---|---|---|
| `flock login` | Authorize this machine in a browser | [Login](#flock-login) |
| `flock whoami` | Check the credential and list organizations | [Identity](#flock-whoami) |
| `flock token` | Print the resolved CLI token | [Token](#flock-token) |
| `flock logout` | End the saved sign-in | [Logout](#flock-logout) |
| `flock s3 credentials` | Issue temporary S3 keys | [S3 commands](commands/s3) |
| `flock s3 configure` | Write an AWS profile | [S3 commands](commands/s3#flock-s3-configure) |
| `flock s3 buckets` | List, create and delete buckets | [S3 commands](commands/s3#flock-s3-buckets) |
| `flock eval run`, `flock eval compare` | Score models and compare results | [Evals commands](../evals/commands) |

For account and storage commands, see [Credential resolution](credentials). Local evaluations need no Flock sign-in unless they log to the tracker.

## flock login

Signs this machine in to Flock. You approve the sign-in in a browser where you're signed in to Flock, and the machine gets a CLI token of its own. [Sign in from a terminal](sign-in) walks through it.

```bash
flock login [--host URL] [--no-browser] [--wandb | --no-wandb]
```

| Flag | Default | Meaning |
|---|---|---|
| `--host` | `FLOCK_HOST`, else the server you're signed in to, else `https://flock-api.cerulean.works` | The Flock API's URL, the same address the wandb SDK uses. The browser page's address comes from the server, so you don't need the web app's. |
| `--no-browser` | off | Only print the page's address and the code. |
| `--wandb` / `--no-wandb` | ask, yes | Also log the wandb SDK in to this Flock server, or don't. See [The wandb SDK](#the-wandb-sdk). |

### How it signs in

`flock login` runs OAuth 2.0's device authorization grant (RFC 8628):

1. It asks the server for a sign-in and prints the code, such as `WDJB-MJHT`.
2. In a terminal, it waits for Enter, then opens the page with the code filled in. With `--no-browser`, without a terminal, or over SSH (`SSH_CONNECTION` is set), it prints the page's address instead.
3. It asks the server every 5 seconds whether the sign-in was approved, and more slowly when the server asks it to.
4. Once you approve, it saves the token to `credentials.json` (see [The credentials file](credentials#the-credentials-file)) and prints `✓ Logged in to <host> as <email>`.

The code is valid for 15 minutes. Nothing is saved until the sign-in succeeds.

### Signing in again

If the saved sign-in still works for this server, `flock login` asks `Already logged in as <email>. Log in again? [y/N]`. Without a terminal, it says so and stops. A saved sign-in that stopped working (revoked, expired, or for another server) is replaced without asking.

### The wandb SDK

Flock speaks the wandb SDK's protocol, so the SDK can log to Flock instead of wandb.ai. After signing in, `flock login` offers to set that up:

1. It creates a wandb key named `wandb on <hostname>` with its new CLI token, listed on your account page like any other. The wandb SDK can only send wandb keys, and wandb keys only work with the SDK, not with the `flock` command or Flock's REST API.
2. It calls `wandb.login(key=..., host=<host>, relogin=True, verify=True)`. That saves the key to `~/.netrc` and points the SDK's `base_url` at Flock, as `wandb login --host <host> <key>` does.

The question's default is yes. That sends every wandb project on the machine to Flock, including ones meant for wandb.ai, so answer no (or pass `--no-wandb`) on a machine that also logs to wandb.ai.

- **Already logged in.** When the wandb SDK already points at this server with a key that works, `flock login` says so, doesn't ask, and creates no second key.
- **Not installed.** The wandb SDK isn't a dependency of the `flock` command. Without it, `flock login` prints the `wandb login --host` command to run once you've created a key.
- **No terminal.** Nothing is asked and the default applies: the SDK is logged in unless `--no-wandb` is given.

### Exit codes

| Code | When |
|---|---|
| `0` | Signed in, or already signed in and kept. |
| `1` | The sign-in was denied in the browser, the code expired, you pressed Ctrl-C, or the server couldn't be reached. The message says which. |

Pending sign-ins are stored in PostgreSQL and survive server restarts. Any replica can answer the polling request. If the code expires before approval, run `flock login` again.

## flock whoami

Shows who you're signed in to Flock as, and checks that the server still accepts the credential.

```bash
flock whoami [--host URL] [--json]
```

| Flag | Default | Meaning |
|---|---|---|
| `--host` | `FLOCK_HOST`, else the saved sign-in's server | The Flock API to ask. |
| `--json` | off | Print `GET /api/v1/me`'s answer as JSON, with `host` and `source` added. |

It finds the credential as [How credentials are found](credentials) describes, and calls `GET /api/v1/me` with it:

```text
Ada <ada@example.com>
  Server: https://flock-api.cerulean.works
  Orgs:   Cerulean Labs (cerulean), admin, default
          Interpretability (interp), member
  From:   flock login (/home/ada/.config/flock/credentials.json)
```

- **Orgs** lists each organization's display name, its ID and your role. `default` marks the one `wandb.init` logs to when it names no entity.
- **From** is `flock login` or `FLOCK_TOKEN`. With `--json`, `source` is `"file"` or `"env"`.

It exits with code `1`, saying to run `flock login`, when there's no credential or the server refuses it (revoked, expired, or ended by a password change).

## flock token

Prints the credential and a newline to stdout, and nothing else, so scripts can hand your sign-in to another tool.

```bash
flock token [--host URL]
```

| Flag | Default | Meaning |
|---|---|---|
| `--host` | `FLOCK_HOST`, else any | Print the credential for this server only. |

```bash
curl -H "Authorization: Bearer $(flock token)" https://flock-api.cerulean.works/api/v1/me
FLOCK_TOKEN=$(flock token) python train.py
```

It finds the credential as [How credentials are found](credentials) describes, so with `FLOCK_TOKEN` set it prints that.

- **No server call.** It doesn't contact the server, so it works offline. To check that the token still works, use [`flock whoami`](#flock-whoami).
- **Errors.** Without a credential, it exits with code `1` and prints the reason to stderr.

> [!WARNING]
> The token signs in as you. Don't paste it into logs or shared files. If it leaks, revoke the machine under **Command line sign-ins** on your account page.

## flock logout

Ends this machine's sign-in on the Flock server and deletes the saved credential.

```bash
flock logout [--host URL]
```

| Flag | Default | Meaning |
|---|---|---|
| `--host` | `FLOCK_HOST`, else the saved sign-in's server | Log out only if the saved sign-in is for this server. |

### What it does

1. It sends `POST /api/v1/auth/logout` with the CLI token, which ends that sign-in. The machine disappears from **Command line sign-ins** on your account page.
2. It deletes `credentials.json` and prints `Logged out of <host>.`

If the server can't be reached, it still deletes the file, and warns that the sign-in stays listed on your account page until you revoke it there or it expires. A sign-in that was already revoked is logged out without a warning.

### What it leaves alone

- **The wandb SDK.** Its login, and the wandb key `flock login` created for it, stay as they are. When `flock login` created that key, `flock logout` names it, so you can revoke it on your account page.
- **`FLOCK_TOKEN`.** When the credential comes from the environment, `flock logout` changes nothing and exits with code `1`. Unset the variable instead.

Without any credential, it prints `Not logged in.` and exits with code `0`.
