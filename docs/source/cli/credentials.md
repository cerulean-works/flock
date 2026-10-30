# Credential resolution and Python API

Every Flock client finds its server and credential the same way, through [`~cli.credentials.resolve`]. `flock whoami`, `flock token` and `flock logout` use it too. This lets CI set an environment variable while laptops and training nodes use `flock login`.

## The order

The first of these that exists wins:

1. **Arguments.** A token passed to `resolve(token=...)` by the calling code.
2. **`FLOCK_TOKEN`.** A CLI token, from `flock token`, sent as `Authorization: Bearer <token>`.
3. **The saved sign-in** from `flock login`, in `credentials.json`.

The server comes from the `host` argument, else `FLOCK_HOST`. With an argument or `FLOCK_TOKEN` and neither of those, it is `https://flock-api.cerulean.works`. The saved sign-in brings its own server, and is only used when `host` and `FLOCK_HOST` are unset or name that same server, so its token is never sent anywhere else.

| Variable | Meaning |
|---|---|
| `FLOCK_TOKEN` | A CLI token (`flock token`) that takes precedence over the saved sign-in. `flock logout` doesn't change it. |
| `FLOCK_HOST` | Overrides Cerulean's API address. Set this only when selecting another installation. Without a scheme, `https://` is assumed. |
| `FLOCK_CONFIG_DIR` | The folder holding `credentials.json`. Default: `$XDG_CONFIG_HOME/flock`, else `~/.config/flock`. |

## The credentials file

`flock login` saves `<config dir>/credentials.json`:

```json
{
  "host": "https://flock-api.cerulean.works",
  "token": "flock_cli_…",
  "user": {"id": 7, "email": "ada@example.com", "name": "Ada", "...": "..."},
  "created_at": 1793097594.8
}
```

- It is written atomically (a temporary file, then a rename), so a reader never sees half a file.
- Only its owner can read it (mode `0600`).
- Signing in to another server replaces it: the command keeps one sign-in at a time.
- When `flock login` logged the wandb SDK in, the file also names the wandb key it created (`wandb_key`), so `flock logout` can tell you about it.

## Using the sign-in elsewhere

For an unattended runner, follow [Authenticate a job](authenticate-jobs). Python clients use the resolved [`~cli.credentials.Credentials`] and its `auth_headers()` method; see [Python credential API](credentials).

## Machines without a person

For CI and other machines nobody signs in on, use a CLI token of yours: run `flock token` where you're signed in, and set the result as `FLOCK_TOKEN` in the CI's secrets. It belongs to that machine's sign-in, so it ends when you log out there, revoke it on your account page, or change your password, and after 90 days without use. Flock has no credentials of CI's own yet.

A wandb key doesn't work as `FLOCK_TOKEN`: wandb keys only work with the wandb SDK, and Flock's API refuses them with 401.

## Python API


Finding a Flock server and credential from Python. [How credentials are found](credentials) explains the order. Without a server named, credentials go to `DEFAULT_HOST`, `https://flock-api.cerulean.works`.

[[autodoc]] cli.credentials.resolve

[[autodoc]] cli.credentials.Credentials

[[autodoc]] cli.credentials.auth_headers

[[autodoc]] cli.credentials.load

[[autodoc]] cli.credentials.save

[[autodoc]] cli.credentials.delete

[[autodoc]] cli.credentials.config_dir

[[autodoc]] cli.credentials.credentials_path

[[autodoc]] cli.credentials.normalize_host
