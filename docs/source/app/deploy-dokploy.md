# Deploy on Dokploy with Docker Swarm

Run Flock on a Dokploy server as a Docker Swarm stack, so a redeploy never interrupts runs that are logging or reading from S3. Every merge to `main` builds new images and redeploys them by itself.

With Compose, a redeploy stops the old API before the new one is healthy, and Traefik answers 404 in between. The wandb SDK doesn't retry a 404, so runs logging at that moment crash. A stack starts each new task first, waits until its health check passes and watches it for 30 seconds. Only then does it stop the old one. If the new task never becomes healthy, Swarm rolls the service back and the old one keeps serving.

## What you need

- A server running [Dokploy](https://docs.dokploy.com), with shell access as a user who can run `docker`. Dokploy runs Docker in Swarm mode and comes with Traefik.
- Three domains whose DNS points at the server: the dashboard (`flock.example.com`), the API (`api.flock.example.com`) and the S3 proxy (`s3.flock.example.com`). Training machines and browsers must reach all three.
- An S3 server for the proxy to front, such as Ceph RGW, and its administrative access key. The stack always runs the S3 proxy.
- Admin access to the Flock repository on GitHub, to set a variable and a secret.

The stack assumes one Swarm node. With more nodes, use the `s3` [files backend](operations#run-files), because the run files volume lives on one node.

## 1. Check Dokploy's Traefik

The stack routes its domains with labels that Traefik's Swarm provider reads. Check that Dokploy's Traefik has it, and note its entrypoint and certificate resolver names:

```bash
sudo grep -nE '^\s*(swarm|docker):|websecure|certificatesResolvers' -A2 /etc/dokploy/traefik/traefik.yml
```

- Under `providers`, there must be a `swarm:` entry. Without it, Traefik never sees the stack and every domain answers 404.
- Each domain gets two routes: plain HTTP on the `web` entrypoint, and HTTPS on `websecure` with a certificate from the `letsencrypt` resolver. Those are Dokploy's usual names and the stack's defaults. If yours differ, set `FLOCK_TRAEFIK_HTTP_ENTRYPOINT`, `FLOCK_TRAEFIK_ENTRYPOINT` and `FLOCK_TRAEFIK_CERTRESOLVER` in [step 4](#4-set-the-environment).

Check that Dokploy's network is an overlay network that services can join:

```bash
docker network inspect dokploy-network --format '{{.Driver}} attachable={{.Attachable}}'
```

It should print `overlay attachable=true`.

## 2. Publish the images

A stack can't build images, so GitHub Actions builds them. The `Publish images` workflow (`.github/workflows/publish-images.yaml`) runs on every push to `main`:

1. It builds `flock-api` and `flock-ui` from the latest commit. A new push cancels a run that's still building an older commit, so only the newest one is published.
2. It pushes them to GHCR as `ghcr.io/<owner>/flock-api` and `ghcr.io/<owner>/flock-ui`, tagged with the commit SHA.
3. Once both are pushed, it moves their `main` tag to them, so the stack never runs an API and a dashboard from different commits.
4. It calls Dokploy's deploy webhook, if it's set ([step 6](#6-turn-on-deploys-from-github)).

The dashboard's public origin is built into its image. In the repository's **Settings**, under **Secrets and variables** then **Actions**, add a **variable**:

| Name | Value |
|---|---|
| `FLOCK_WEB_URL` | The dashboard's public origin, such as `https://flock.example.com`, with no path. |

Run the workflow once from the **Actions** tab (**Publish images**, then **Run workflow** on `main`), and check that both packages appear under the owner's **Packages**. The workflow stops before building if `FLOCK_WEB_URL` isn't set.

If the packages are private, Dokploy needs to sign in to pull them. Create a GitHub token with the `read:packages` scope, and add it in Dokploy under **Registry** with the registry URL `ghcr.io` and your GitHub username.

## 3. Create the volumes

The stack keeps PostgreSQL's data and the run files on two named volumes that it doesn't create itself. This keeps data from being tied to the stack's name, and lets an existing Compose deployment hand its volumes over (see [Move from Compose](#move-from-compose)).

For a new installation, create them on the server:

```bash
docker volume create flock_postgres-data
docker volume create flock_files
```

## 4. Set the environment

In Dokploy, create a **Compose** service in your project, and set its type to **Stack**. Point it at the Flock repository on branch `main` with the Compose path `app/compose.swarm.yaml`, or paste that file's contents as a raw Compose file.

Under **Environment**, add the settings. Dokploy passes them to the stack file, which forwards them to each service:

```dotenv
# Domains and volumes of this stack.
FLOCK_WEB_HOST=flock.example.com
FLOCK_API_HOST=api.flock.example.com
FLOCK_S3_HOST=s3.flock.example.com
FLOCK_POSTGRES_VOLUME=flock_postgres-data
FLOCK_FILES_VOLUME=flock_files

# Public addresses, as browsers and training machines reach them.
FLOCK_WEB_URL=https://flock.example.com
FLOCK_API_PUBLIC_URL=https://api.flock.example.com
FLOCK_S3_PUBLIC_URL=https://s3.flock.example.com

POSTGRES_PASSWORD=replace-with-a-long-random-password

# The S3 server the proxy fronts.
FLOCK_S3_UPSTREAM_ENDPOINT=https://ceph.example.com
FLOCK_S3_UPSTREAM_ACCESS_KEY_ID=replace-with-upstream-access-key
FLOCK_S3_UPSTREAM_SECRET_ACCESS_KEY=replace-with-upstream-secret
```

`FLOCK_WEB_URL` must match the repository variable from [step 2](#2-publish-the-images). These settings are specific to the stack:

| Variable | Default | Meaning |
|---|---|---|
| `FLOCK_WEB_HOST` | required | Dashboard domain. |
| `FLOCK_API_HOST` | required | API domain. |
| `FLOCK_S3_HOST` | required | S3 proxy domain. |
| `FLOCK_POSTGRES_VOLUME` | required | Name of the PostgreSQL volume. |
| `FLOCK_FILES_VOLUME` | required | Name of the run files volume. |
| `FLOCK_VERSION` | `main` | Image tag to run. Set a commit SHA to hold a release while `main` moves on. |
| `FLOCK_IMAGE_PREFIX` | `ghcr.io/cerulean-works` | Registry and owner the images are pulled from. Change it if you publish from a fork. |
| `FLOCK_TRAEFIK_HTTP_ENTRYPOINT` | `web` | Traefik's HTTP entrypoint. A domain behind a proxy that ends TLS, such as Cloudflare, reaches Flock through it. |
| `FLOCK_TRAEFIK_ENTRYPOINT` | `websecure` | Traefik's HTTPS entrypoint. |
| `FLOCK_TRAEFIK_CERTRESOLVER` | `letsencrypt` | Traefik's certificate resolver. |

Every other setting in [Settings](configuration), such as Google sign-in or `FLOCK_S3_WORKERS`, works as with Compose. Don't add domains under the service's **Domains** tab: the stack file already routes them, and two routes for one domain conflict.

## 5. Deploy and check it

Click **Deploy**. The first deploy pulls the images and starts PostgreSQL, the API, the S3 proxy and the dashboard. The S3 proxy restarts a few times until the API has applied the database migrations, which is expected.

Check each service on the server. Dokploy names the stack after the service's **App Name**, shown in its **General** tab, such as `flock-stack-abc123`:

```bash
docker stack services flock-stack-abc123
```

Every service should show `1/1` replicas. For the API, read the logs with `docker service logs flock-stack-abc123_api`. Then check the public routes:

```bash
curl --fail https://api.flock.example.com/readyz
curl --fail https://s3.flock.example.com/healthz
```

Open the dashboard, [create the first account](deploy#create-the-first-account), then [log a first run](log-runs#log-a-run) and list a bucket with the [S3 credentials](../cli/s3-access) of your account.

## 6. Turn on deploys from GitHub

1. In the service's **General** tab, turn on **Autodeploy**. Dokploy refuses webhook calls without it.
2. Copy the webhook URL from the service's **Deployments** tab. It looks like `https://dokploy.example.com/api/deploy/compose/<token>`.
3. In the repository's Actions settings, add it as a **secret** named `DOKPLOY_WEBHOOK_URL`.

From now on, every merge to `main` publishes new images and redeploys the stack. Each deploy resolves `main` to the image it points at, so Swarm sees a moved tag as a change and updates only the services whose image changed. A rollback returns to the image that ran before.

If the service takes its Compose file from GitHub, Dokploy also deploys on the push itself, before the images are built. That deploy finds `main` unchanged, and changes nothing.

## 7. Test a redeploy

Check that a run survives a redeploy before relying on it:

1. Start a run that logs every second or so, from a training machine.
2. Merge a change to `main`, or click **Deploy** in Dokploy.
3. Watch the API service update:

   ```bash
   docker service ps flock-stack-abc123_api
   ```

   The new task reaches `Running` before the old one shuts down.

4. Check the run in the dashboard. It keeps logging and doesn't show as crashed.

## Move from Compose[[move-from-compose]]

If Flock already runs as a Dokploy Compose service, the move has one short outage, because the Compose service and the stack can't both use the same domains and the same PostgreSQL volume. Pick a quiet time and tell your users.

1. Note the Compose project's volume names:

   ```bash
   docker volume ls --filter name=flock
   ```

2. [Back up](operate#back-up-and-restore) the database and the run files.
3. In Dokploy, stop the Compose service and remove its domains.
4. Follow steps [1](#1-check-dokploys-traefik), [2](#2-publish-the-images), [4](#4-set-the-environment), [5](#5-deploy-and-check-it) and [6](#6-turn-on-deploys-from-github), skipping [step 3](#3-create-the-volumes). Set `FLOCK_POSTGRES_VOLUME` and `FLOCK_FILES_VOLUME` to the names you noted, such as `flock-app_postgres-data` and `flock-app_files`, so the stack keeps the existing data. Keep the Compose service's other settings.
5. Once the stack works, delete the Compose service in Dokploy. Don't delete its volumes with it, since the stack uses them now.

## Migrations during a redeploy

The old and new release share the database for a minute or more while the new one starts. Migrations that remove something, such as a column, wait until no old replica is left, and a contract delay after that. See [Expand and contract migrations](operations#expand-and-contract-migrations).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Every domain answers 404 | Traefik doesn't read the stack. Check for the `swarm` provider ([step 1](#1-check-dokploys-traefik)), and that the stack's services are on `dokploy-network`. |
| The browser warns about the certificate | `FLOCK_TRAEFIK_CERTRESOLVER` doesn't match a resolver in Traefik's configuration. |
| A service stays at `0/1` with `No such image` or `denied` | The server can't pull from GHCR. Add the registry in Dokploy ([step 2](#2-publish-the-images)), and check that the workflow published the images. |
| A deploy fails with `volume ... not found` | The volume names don't exist. Create them ([step 3](#3-create-the-volumes)) or fix the names. |
| The API task keeps restarting during a deploy | The new release fails its health check, and Swarm rolls back to the old one, which keeps serving. Read the failed task's logs with `docker service logs`, as in [step 5](#5-deploy-and-check-it). |
| The workflow fails at **Deploy to Dokploy** with `Dokploy answered 301` | Dokploy's branch check failed. Check that the service's branch is `main`. |
| The workflow fails at **Deploy to Dokploy** with `Dokploy answered 400` | **Autodeploy** is off for the service. |
| The API refuses to start with `database has contract migrations this release doesn't know` | A release older than the database's schema was deployed. Deploy `main`, or a later commit. |
