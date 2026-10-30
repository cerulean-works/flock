# Authenticate an unattended job

Give a job or CI runner a CLI token so Flock clients can reach your account without a browser prompt.

## Before you start

[Sign in](sign-in) on a machine you control. Your job needs Flock installed and network access to the public API. This procedure uses a person's existing sign-in; Flock has no separate service-account credential.

## What the job receives

A CLI token represents the signed-in machine acting as your account. Passing it to another process gives that process the same Flock account access; it does not create an independent service identity. S3 clients can use it to request ephemeral S3 keys. The wandb SDK needs its own wandb key.

## Configure the job

1. Read the token privately on the signed-in machine:

   ```bash
   uv run flock token
   ```

2. Store it in your runner's secret store as `FLOCK_TOKEN`. Cerulean's API is the default, so no host variable is needed. Do not commit the token to a job file or print it in logs.
3. Have the runner inject the token, then check access inside the job:

   ```bash
   uv run flock whoami
   ```

4. Use the same environment for the work. For example, request storage credentials:

   ```bash
   uv run flock s3 buckets list
   ```

The identity check must show the intended user, organization and `FLOCK_TOKEN` as its source. For a local child process, you can pass the sign-in without writing another file:

```bash
FLOCK_TOKEN="$(uv run flock token)" uv run flock whoami
```

Revoking or logging out the source sign-in also ends the job's access. See [Credential resolution](credentials#machines-without-a-person) for expiry and [Sign out](sign-in#sign-out-and-revoke-access) for revocation. To log experiments, configure a [wandb key](../app/accounts#manage-your-account-and-keys) separately.
