# How credentials are found

Every Flock client finds its server and credential the same way, through [`~cli.credentials.resolve`]. `flock whoami`, `flock token` and `flock logout` use it too. This lets CI set an environment variable while laptops and training nodes use `flock login`.

## The order

The first of these that exists wins:

1. **Arguments.** A token passed to `resolve(token=...)` by the calling code.
2. **`FLOCK_TOKEN`.** A CLI token or an API key: both are sent as `Authorization: Bearer <token>`.
3. **The saved sign-in** from `flock login`, in `credentials.json`.

The server comes from the `host` argument, else `FLOCK_HOST`. With an argument or `FLOCK_TOKEN` and neither of those, it is `https://flock-api.cerulean.works`. The saved sign-in brings its own server, and is only used when `host` and `FLOCK_HOST` are unset or name that same server, so its token is never sent anywhere else.

| Variable | Meaning |
|---|---|
| `FLOCK_TOKEN` | A credential that takes precedence over the saved sign-in. `flock logout` doesn't change it. |
| `FLOCK_HOST` | The Flock API's URL, such as `https://flock-api.example.com`. Without a scheme, `https://` is assumed. |
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
- When `flock login` logged the wandb SDK in, the file also names the API key it created (`wandb_key`), so `flock logout` can tell you about it.

## Using the sign-in elsewhere

On a remote job that can't run `flock login`, pass your sign-in along in the environment:

```bash
FLOCK_TOKEN=$(flock token) python train.py
```

Or send it to the API directly:

```bash
curl -H "Authorization: Bearer $(flock token)" https://flock-api.cerulean.works/api/v1/me
```

In Python, get the header from [`~cli.credentials.auth_headers`]:

```python
import httpx

from flock.cli import credentials

found = credentials.resolve()
if found is None:
    raise SystemExit("run flock login first")
me = httpx.get(f"{found.host}/api/v1/me", headers=found.auth_headers()).json()
```

`flock.cli.credentials` uses only the standard library, so any Flock package can depend on `flock-cli` without pulling in more.

## Machines without a person

For CI and other machines nobody signs in on, create an API key on your account page and set it as `FLOCK_TOKEN`. API keys don't end when you change your password, and the wandb SDK can use them too. A CLI token from `flock login` belongs to one machine's sign-in, and ends with it.
