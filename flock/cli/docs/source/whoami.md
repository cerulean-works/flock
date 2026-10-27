# flock whoami

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
