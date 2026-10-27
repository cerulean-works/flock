# flock logout

Ends this machine's sign-in on the Flock server and deletes the saved credential.

```bash
flock logout [--host URL]
```

| Flag | Default | Meaning |
|---|---|---|
| `--host` | `FLOCK_HOST`, else the saved sign-in's server | Log out only if the saved sign-in is for this server. |

## What it does

1. It sends `POST /api/v1/auth/logout` with the CLI token, which ends that sign-in. The machine disappears from **Command line sign-ins** on your account page.
2. It deletes `credentials.json` and prints `Logged out of <host>.`

If the server can't be reached, it still deletes the file, and warns that the sign-in stays listed on your account page until you revoke it there or it expires. A sign-in that was already revoked is logged out without a warning.

## What it leaves alone

- **The wandb SDK.** Its login, and the API key `flock login` created for it, stay as they are. When `flock login` created that key, `flock logout` names it, so you can revoke it on your account page.
- **`FLOCK_TOKEN`.** When the credential comes from the environment, `flock logout` changes nothing and exits with code `1`. Unset the variable instead.

Without any credential, it prints `Not logged in.` and exits with code `0`.
