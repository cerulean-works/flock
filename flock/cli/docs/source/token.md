# flock token

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

- **No server call.** It doesn't contact the server, so it works offline. To check that the token still works, use [`flock whoami`](whoami).
- **Errors.** Without a credential, it exits with code `1` and prints the reason to stderr.

> [!WARNING]
> The token signs in as you. Don't paste it into logs or shared files. If it leaks, revoke the machine under **Command line sign-ins** on your account page.
