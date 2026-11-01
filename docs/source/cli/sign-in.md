# Sign in and manage access

Sign a machine in to Cerulean's Flock service, connect experiment logging, and understand which credentials give it access.

## What your login does

Your Flock account belongs to one or more **organizations**. Each organization groups its members, experiment projects and storage buckets. Your default organization is used when a command or logging script does not name one.

Flock uses different credentials for different jobs:

| Credential | Used by | How you get it |
|---|---|---|
| CLI token | The `flock` command, Flock Python clients and the REST API | `flock login` saves a token for this machine. |
| wandb key | The wandb SDK that logs experiments to Flock | The SDK setup offered during login creates one, or you create one on your account page. |
| S3 access key and secret | Tools that read and write stored objects | Exchange your sign-in for temporary keys, or create a persistent key in the browser. See [S3 access](s3-access). |

These credentials cannot be substituted for each other. Signing in to the CLI gives it the ability to request storage keys; it does not make the CLI token an S3 key or a wandb key.

## Sign in

[Install Flock](../installation) and [join your organization](../app/accounts#join-an-organization) first.

1. From the repository root, run:

   ```bash
   uv run flock login
   ```

   Cerulean's API is the built-in default. You do not need to provide a host.

2. Copy the one-time code and press Enter to open the authorization page. Sign in if needed, check the machine's name, platform and IP address, then select **Authorize**. On a headless machine, open the printed URL in a browser on another device.
3. When the CLI offers to log the wandb SDK in, accept if this machine will send experiments to Cerulean. This is the default choice.
4. Verify the identity and default organization:

   ```bash
   uv run flock whoami
   ```

The output lists your account, server, organizations and credential source. The machine appears under **Command line sign-ins** on your [account page](https://flock.cerulean.works/account).

## wandb SDK

The SDK keeps its own wandb key and server settings. Once the login setup succeeds, ordinary `wandb.init(...)` calls log to Flock. [Track an experiment](../app/log-runs) walks through a complete run.

This setup affects later wandb runs on the machine. If the machine also logs to wandb.ai, choose whether to change that default before accepting. For per-job configuration, use a separate wandb key and the process environment described in [Track an experiment](../app/log-runs#configure-a-shared-machine).

If you previously declined the SDK setup, run `uv run flock login` again and approve signing in again when prompted. Keeping an existing valid sign-in returns before the SDK setup step. Explicit setup flags and the behavior when wandb is absent are in the [login reference](commands#flock-login).

## Sign out and revoke access

Run this on a machine you no longer want signed in:

```bash
uv run flock logout
```

Check **Command line sign-ins** on your account page to confirm removal. If you have lost access to the machine, revoke its entry there instead. If the server could not be reached during logout, the local file is removed but you must revoke the server-side entry yourself.

Temporary S3 keys issued through that sign-in stop working with it. Its separate wandb key and browser-created persistent S3 keys can remain valid: review **Keys** and **S3 keys** and revoke those separately when retiring a machine.

If `whoami` reports `FLOCK_TOKEN` as its source, the environment token takes priority over the saved sign-in. Logout leaves that variable untouched; remove it from the job or shell and revoke its source sign-in. [Credential resolution](credentials) explains precedence and lifetimes, and [unattended jobs](authenticate-jobs) explains how to pass the token to a runner.

For another installation, the [login reference](commands#flock-login) explains how `--host` selects its API; the [hosting guide](../app/deploy) covers configuring its addresses.
