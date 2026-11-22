# Self-hosted CI runners

Flock's pull request checks (`.github/workflows/pull-request.yaml`) run on four GitHub Actions runners on our Dokploy server, labelled `flock-ci`. This folder is the Compose service that runs them.

## How it works

Each replica of `compose.yaml` loops through:

1. Wipe its home directory, `/tmp` and `/dev/shm`, and copy a fresh runner from the image.
2. Sign in as the `cerulean-flock-ci-runners` GitHub App and register a [just-in-time runner](https://docs.github.com/en/rest/actions/self-hosted-runners#create-configuration-for-a-just-in-time-runner-for-a-repository) on `cerulean-works/flock`.
3. Run exactly one job, after which the runner deregisters itself and the container exits. Docker restarts it at step 1.

So four jobs run at once, and no job sees what an earlier one left behind.

## Safety

Pull request code runs on the same server as production Flock, so the service:

- **Runs jobs as an unprivileged user.** Jobs run as `runner` (uid 1001). The image removes sudo and every setuid binary, Compose drops all capabilities but the two the entrypoint needs to switch to `runner`, and `no-new-privileges` stops anything regaining them.
- **Keeps the App key from jobs.** Only the entrypoint's root process holds the key, and `runner` can't read a root process's environment. Jobs start with a clean environment. The App can only manage runners, only on this repository, and each installation token is revoked as soon as the runner is registered.
- **Lets jobs write almost nowhere.** The root filesystem is read-only, including the installed runner. Jobs can write only to their own home volume and a 2 GB `/tmp`, which are both wiped before the next job.
- **Gives jobs no way out.** There's no Docker socket and no host mount, and the runners sit on their own network, apart from `dokploy-network`.
- **Caps what a job can take.** Each runner gets at most 3 CPUs, 6 GB of memory (no swap) and 4096 processes. The kernel kills a runner first when the server runs out of memory, and every job times out after 30 minutes.

On GitHub, the repository requires approval before workflows run for every pull request from an outside contributor, not only their first.

Jobs can still reach anything the server can reach over the network.

## Deploy on Dokploy

The service is the **CI Runners** Compose service in the Flock project, sourced from this repository with the Compose path `./.github/runner/compose.yaml`. Its environment:

```dotenv
GITHUB_APP_ID=5872618
# The App's private key, base64-encoded on one line: base64 -w0 key.pem
GITHUB_APP_PRIVATE_KEY_B64=...
# Optional, with these defaults:
RUNNER_REPOSITORY=cerulean-works/flock
RUNNER_LABELS=flock-ci
RUNNER_REPLICAS=4
RUNNER_CPUS=3
RUNNER_MEMORY=6g
```

Deploys are manual: changes merged here take effect when you press **Deploy**. A deploy restarts the runners, which cancels the jobs they're running.

## Update the runner

GitHub stops sending jobs to runners that fall too far behind. To update, change the tag and digest in `Dockerfile` to the latest [`ghcr.io/actions/actions-runner`](https://github.com/actions/runner/pkgs/container/actions-runner) release, merge, and deploy. Until then, a runner updates itself before each job, which costs a download every time.

## Rotate the App key

In the organisation's **Settings**, under **Developer settings** then **GitHub Apps**, open `cerulean-flock-ci-runners` and generate a new private key. Put it in `GITHUB_APP_PRIVATE_KEY_B64`, deploy, then delete the old key on GitHub.
