# Installation

`flock` is installed with the workspace. Run it with `uv run flock` from the repository root, or as `flock` once the workspace's virtual environment is active.

```bash
uv sync
uv run flock --help
```

## Signing in

1. Run `flock login`. Without `--host`, it signs in to `https://flock-api.cerulean.works`. For another Flock server, pass its API address, the same address the wandb SDK uses:

   ```bash
   uv run flock login --host https://flock-api.example.com
   ```

2. Copy the code it prints:

   ```text
   ! Copy your one-time code: WDJB-MJHT
   Press Enter to open https://flock.cerulean.works/device in your browser...
   ```

   On a machine without a browser (over SSH, or with `--no-browser`), it prints the address to open instead.

3. Open the page, sign in to Flock if you aren't already, and enter the code. The page shows the machine's name, platform and IP address. Check that it's yours, then select **Authorize**.

4. The terminal confirms the sign-in:

   ```text
   ✓ Logged in to https://flock-api.cerulean.works as ada@example.com
   ```

5. `flock login` then asks whether to also log the wandb SDK in to this Flock server. The default is no, because saying yes sends every wandb project on the machine to Flock, including ones meant for wandb.ai. See [flock login](login#the-wandb-sdk).

## Checking it worked

```bash
uv run flock whoami
```

This prints your name, the server, your organizations and where the credential came from. The machine is also listed under **Command line sign-ins** on your account page, where you can revoke it.
