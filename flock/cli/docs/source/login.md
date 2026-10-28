# flock login

Signs this machine in to Flock. You approve the sign-in in a browser where you're signed in to Flock, and the machine gets a CLI token of its own. [Installation](installation#signing-in) walks through it.

```bash
flock login [--host URL] [--no-browser] [--wandb | --no-wandb]
```

| Flag | Default | Meaning |
|---|---|---|
| `--host` | `FLOCK_HOST`, else the server you're signed in to, else `https://flock-api.cerulean.works` | The Flock API's URL, the same address the wandb SDK uses. The browser page's address comes from the server, so you don't need the web app's. |
| `--no-browser` | off | Only print the page's address and the code. |
| `--wandb` / `--no-wandb` | ask, yes | Also log the wandb SDK in to this Flock server, or don't. See [The wandb SDK](#the-wandb-sdk). |

## How it signs in

`flock login` runs OAuth 2.0's device authorization grant (RFC 8628):

1. It asks the server for a sign-in and prints the code, such as `WDJB-MJHT`.
2. In a terminal, it waits for Enter, then opens the page with the code filled in. With `--no-browser`, without a terminal, or over SSH (`SSH_CONNECTION` is set), it prints the page's address instead.
3. It asks the server every 5 seconds whether the sign-in was approved, and more slowly when the server asks it to.
4. Once you approve, it saves the token to `credentials.json` (see [The credentials file](credentials#the-credentials-file)) and prints `✓ Logged in to <host> as <email>`.

The code is valid for 15 minutes. Nothing is saved until the sign-in succeeds.

## Signing in again

If the saved sign-in still works for this server, `flock login` asks `Already logged in as <email>. Log in again? [y/N]`. Without a terminal, it says so and stops. A saved sign-in that stopped working (revoked, expired, or for another server) is replaced without asking.

## The wandb SDK

Flock speaks the wandb SDK's protocol, so the SDK can log to Flock instead of wandb.ai. After signing in, `flock login` offers to set that up:

1. It creates a wandb key named `wandb on <hostname>` with its new CLI token, listed on your account page like any other. The wandb SDK can only send wandb keys, and wandb keys only work with the SDK, not with the `flock` command or Flock's REST API.
2. It calls `wandb.login(key=..., host=<host>, relogin=True, verify=True)`. That saves the key to `~/.netrc` and points the SDK's `base_url` at Flock, as `wandb login --host <host> <key>` does.

The question's default is yes. That sends every wandb project on the machine to Flock, including ones meant for wandb.ai, so answer no (or pass `--no-wandb`) on a machine that also logs to wandb.ai.

- **Already logged in.** When the wandb SDK already points at this server with a key that works, `flock login` says so, doesn't ask, and creates no second key.
- **Not installed.** The wandb SDK isn't a dependency of the `flock` command. Without it, `flock login` prints the `wandb login --host` command to run once you've created a key.
- **No terminal.** Nothing is asked and the default applies: the SDK is logged in unless `--no-wandb` is given.

## Exit codes

| Code | When |
|---|---|
| `0` | Signed in, or already signed in and kept. |
| `1` | The sign-in was denied in the browser, the code expired, you pressed Ctrl-C, or the server couldn't be reached. The message says which. |

A server restart forgets sign-ins that are waiting for approval. The CLI then reports an expired code: run `flock login` again.
