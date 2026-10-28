# Signing in to Flock from a terminal

The `flock` command signs a machine in to Flock, the way `gh auth login` signs in to GitHub. You approve the sign-in in a browser where you're already signed in to Flock, and the machine keeps a credential of its own. Flock clients on that machine then find it without a token in an environment variable.

## What it does

- **Signs in with a code.** `flock login` prints a short code. You enter it on Flock's `/device` page in any browser, check which machine is asking, and approve. This works on a laptop and on a training node you reach over SSH.
- **Keeps one sign-in per machine.** The credential is a CLI token, saved to `~/.config/flock/credentials.json`. It lasts until it goes unused for 90 days, you revoke it on your account page, or you change your password.
- **Optionally points the wandb SDK at Flock.** After signing in, `flock login` can create a wandb key and log the wandb SDK in to the same Flock server, so `wandb.init` logs there.
- **Hands the credential to other tools.** `flock token` prints it, and Python code reads it with [`~cli.credentials.resolve`].

## Commands

| Command | Does |
|---|---|
| [`flock login`](login) | Signs this machine in, approving the sign-in in a browser |
| [`flock logout`](logout) | Ends this machine's sign-in on the server and deletes the saved credential |
| [`flock whoami`](whoami) | Shows who you're signed in as, on which server |
| [`flock token`](token) | Prints the credential, for another tool |
| [`flock s3`](s3) | S3 keys for your organizations' buckets, and the buckets themselves |

## Where to go next

- Install the command and sign in: [Installation](installation).
- Use the sign-in from CI or a remote job: [How credentials are found](credentials).
