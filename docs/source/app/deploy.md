# Deploy another Flock installation

Cerulean's service is already configured for normal users. This guide is for operators creating another installation, with its own database, domains and storage. [CLI sign-in](../cli/sign-in) uses Cerulean by default; clients of your separate installation must select its API explicitly.

A deployment has an API, a dashboard and PostgreSQL, plus durable run-file storage. The dashboard's public origin, the API's public address and its internal service address are different network routes. Set them consistently before creating accounts.

## Deploy with Compose

Run the API, dashboard and PostgreSQL behind your reverse proxy.

You need a Flock checkout, Docker with Compose v2, durable storage and two public routes: one for the dashboard and one for the API. The production Compose file publishes no host ports. Your reverse proxy must reach the Compose services.

### Configure and start

1. In `app`, copy the example environment:

   ```bash
   cd app
   cp .env.example .env
   ```

2. Set these values in `.env`, using your own password and domains:

   ```dotenv
   POSTGRES_PASSWORD=replace-with-a-long-random-password
   FLOCK_WEB_URL=https://flock.example.com
   FLOCK_API_PUBLIC_URL=https://api.flock.example.com
   ```

3. Route the dashboard domain to Compose service `ui` on port `3000`, and the public API domain to service `api` on port `8080`. Keep the internal web-to-API address at its Compose default, `http://flock-api:8080`.
4. Build and start the stack:

   ```bash
   docker compose up -d --build
   docker compose ps
   ```

5. Check the public API:

   ```bash
   curl --fail https://api.flock.example.com/healthz
   curl --fail https://api.flock.example.com/readyz
   ```

6. Open the dashboard and [create the first account](#create-the-first-account) before allowing other people to reach the new installation.
7. On a test client, explicitly select this installation rather than Cerulean's default:

   ```bash
   uv run flock login --host https://api.flock.example.com
   ```

   Accept its SDK setup, then [log a first run](log-runs#log-a-run) and check this installation's dashboard. This verifies public API and upload routing.

The dashboard origin is embedded at web-image build time. Rebuild that image when `FLOCK_WEB_URL` changes. Keep database and files volumes durable and [back them up together](operate#back-up-and-restore).

See [Deployment reference](operations) for reverse proxy timeouts, upload limits, replica storage, Kubernetes and migration behavior. [Settings](configuration) lists every environment variable. To add organization storage, [enable S3](#enable-s3).

## Create the first account

Initialize a new Flock instance with its first administrator and organization.

[Run Flock locally](run-locally) or [deploy it](#deploy-with-compose), and know the dashboard's public address.

> [!WARNING]
> The first account becomes the server's super admin. Complete setup before exposing a new instance to other people.

### Complete setup

1. Open the dashboard's `/setup` page.
2. Create your account with a name, email and password. If Google sign-in is configured, you can use it instead.
3. Enter the first organization's display name and ID. These docs use `Flock Research` and the ID `flock`.
4. Complete setup and check that the organization appears in the dashboard and your account page.
5. [Invite your team](accounts#invite-and-manage-members), then [log a first run](log-runs#log-a-run).

The organization ID is the `entity` used by training code and appears in URLs. Choose it carefully: the display name can change, but the ID cannot.

Without the dashboard, use `POST /auth/signup` followed by `POST /orgs` under `/api/v1`; see [Authentication API](api/auth#signup) and [Organization API](api/orgs#organizations). API sign-up alone creates no organization. [Account reference](authentication#the-first-account) describes bootstrap behavior.

## Enable S3

Put Flock's proxy in front of an existing S3 server so each user can access organization buckets with their own credentials.

You need a [working Flock deployment](#deploy-with-compose), an upstream S3 server and its administrative access key, and a public domain for the S3 proxy. Both browsers and training clients must reach that domain.

### Configure and verify the proxy

1. Add the upstream credentials and public proxy URL to `app/.env`:

   ```dotenv
   FLOCK_S3_UPSTREAM_ENDPOINT=https://ceph.example.com
   FLOCK_S3_UPSTREAM_ACCESS_KEY_ID=replace-with-upstream-access-key
   FLOCK_S3_UPSTREAM_SECRET_ACCESS_KEY=replace-with-upstream-secret
   FLOCK_S3_PUBLIC_URL=https://s3.flock.example.com
   FLOCK_WEB_URL=https://flock.example.com
   ```

2. Route the S3 domain to Compose service `flock-s3` on port `8090`.
3. Start the stack with the `s3` profile, from `app`:

   ```bash
   docker compose --profile s3 up -d --build
   docker compose --profile s3 ps
   ```

   Keep this profile enabled in later deployments. A deployment service must enable the same Compose profile.

4. Sign in to the dashboard and check that **Storage** and **Datasets** appear. Open Storage and confirm that its Connect panel shows the public proxy endpoint.
5. With a CLI sign-in to this installation, [generate S3 credentials](../cli/s3-access#temporary-keys-for-one-shell), then list buckets with the AWS CLI. Upload and download a small object through the dashboard to verify the browser route and CORS too.

Use `flock-s3`, rather than the generic service name `s3`, as the reverse proxy target. See [Upgrade Flock](operate#upgrade-flock) for existing deployments with the old name.

[Settings](configuration#s3-settings) covers regions, prefixes and trash storage. [S3 behavior](s3-behavior) explains key limits and supported operations. The upstream administrative key is for the server; users receive Flock-scoped keys.

## Deploy on Docker Swarm

Run Flock as a Swarm stack so a redeploy never leaves the API or the S3 proxy without a healthy task behind Traefik.

With Compose, a redeploy stops the old API container before the new one is healthy, and Traefik answers 404 in between. The wandb SDK doesn't retry a 404, so runs logging at that moment crash. A stack starts each new task first, waits until its health check passes and watches it for 30 seconds. Only then does it stop the old one. If the new task never becomes healthy, Swarm rolls the service back and the old task keeps serving.

`app/compose.swarm.yaml` is the stack. It runs `ui`, `api`, `flock-s3` and `postgres`, and differs from `app/compose.yaml` in these ways:
- **Images come from a registry.** A stack can't build, so it pulls `flock-api` and `flock-ui` tagged `main`. CI publishes them from the latest commit on `main`.
- **Traefik routes are in the file.** Each app service has Traefik labels for its domain, so you don't add domains in Dokploy.
- **The S3 proxy always runs.** There is no `s3` profile, so set the [S3 settings](configuration#s3-settings) and an S3 domain.
- **The volumes already exist.** The stack mounts the PostgreSQL and run files volumes your Compose deployment created, by name.

You need Dokploy (it runs Docker in Swarm mode), its Traefik with the Swarm provider enabled, and three domains: the dashboard, the API and the S3 proxy. The file assumes one Swarm node. With more, use the `s3` [files backend](operations#run-files), since the `files` volume is local to one node.

### Publish the images

The `Publish images` workflow (`.github/workflows/publish-images.yaml`) builds both images from the latest commit on `main`. A new push cancels a run that's still building an older commit, so only the newest one is published and deployed. The workflow pushes the images to GHCR as `ghcr.io/<owner>/flock-api` and `ghcr.io/<owner>/flock-ui`, tagged with the commit SHA, moves their `main` tag once both are pushed, and calls Dokploy's deploy webhook. Set these in the repository's **Settings**, under **Secrets and variables** then **Actions**:

| Name | Kind | Value |
|---|---|---|
| `FLOCK_WEB_URL` | variable | The dashboard's public origin, such as `https://flock.example.com`. It's built into the dashboard image. |
| `DOKPLOY_WEBHOOK_URL` | secret | The deploy webhook of Flock's Stack service, from its **Deployments** tab in Dokploy, such as `https://dokploy.example.com/api/deploy/compose/<token>`. Turn on **Autodeploy** for the service, or Dokploy refuses the call. |

Without the secret, the workflow publishes the images and deploys nothing. Each deploy resolves `main` to the image it points at, so Swarm sees a moved tag as a change, and a rollback returns to the image that ran before.

If the service takes its Compose file from the GitHub repository, Dokploy also deploys on the push itself, before the images are built. That deploy finds `main` unchanged and changes nothing.

Dokploy needs to pull the images. If the packages are private, add GHCR under **Registry** in Dokploy with a GitHub token that has `read:packages`.

### Set the environment

The stack reads the same variables as Compose (see [Environment variables](operations#environment-variables)), plus these:

| Variable | Default | Meaning |
|---|---|---|
| `FLOCK_VERSION` | `main` | Tag of the images to run. Set a commit SHA to hold a release while `main` moves on. |
| `FLOCK_IMAGE_PREFIX` | `ghcr.io/cerulean-works` | Registry and owner the images are pulled from. |
| `FLOCK_WEB_HOST` | required | Dashboard domain, such as `flock.example.com`. |
| `FLOCK_API_HOST` | required | API domain, such as `api.flock.example.com`. |
| `FLOCK_S3_HOST` | required | S3 proxy domain, such as `s3.flock.example.com`. |
| `FLOCK_POSTGRES_VOLUME` | required | Name of the existing PostgreSQL volume, such as `flock-app_postgres-data`. |
| `FLOCK_FILES_VOLUME` | required | Name of the existing run files volume, such as `flock-app_files`. |
| `FLOCK_TRAEFIK_ENTRYPOINT` | `websecure` | Traefik entrypoint for HTTPS. |
| `FLOCK_TRAEFIK_CERTRESOLVER` | `letsencrypt` | Traefik certificate resolver. |

Check the entrypoint and resolver names in Dokploy's Traefik configuration, `/etc/dokploy/traefik/traefik.yml`. A stack prefixes volume names with its own name, which isn't the Compose project's, so the volumes are external and named in full. Find their names with `docker volume ls`.

### Move from Compose to a stack

The move itself has one short outage, because the Compose service and the stack can't both use the same domains and the same PostgreSQL volume. Pick a quiet time and tell your users.

1. Note the Compose project's volume names:

   ```bash
   docker volume ls --filter name=flock-app
   ```

2. [Back up](operate#back-up-and-restore) the database and the run files.
3. In Dokploy, stop the Compose service and remove its domains.
4. Create a Compose service of type **Stack** (or switch the existing one), with `app/compose.swarm.yaml` as its file. Give it the same environment plus the variables above, Deploy it.
5. Check the API and the S3 proxy:

   ```bash
   curl --fail https://api.flock.example.com/readyz
   curl --fail https://s3.flock.example.com/healthz
   ```

6. Sign in to the dashboard, log a test run and list a bucket.
7. Redeploy once with no change, and once after a push to `main` while a test run logs, to check that the run survives.

During a redeploy, the old and new release share the database for a minute or more. [Expand and contract migrations](operations#expand-and-contract-migrations) keep the old release working on the new schema.

## Enable Google sign-in

Let invited users sign in with their Google accounts.

You need a working dashboard, a Google Cloud project where you can create an OAuth client, and the dashboard's public HTTPS origin. The bundled dashboard already proxies Flock's Google authentication paths.

### Configure and test

1. Choose the callback URL, using your dashboard origin:

   ```text
   https://flock.example.com/api/v1/auth/google/callback
   ```

2. Create a Google OAuth client of type **Web application**. Register that exact callback under **Authorized redirect URIs**, including its scheme, port if any, and path.
3. In `app/.env`, set the client credentials and dashboard origin:

   ```dotenv
   FLOCK_WEB_URL=https://flock.example.com
   FLOCK_GOOGLE_CLIENT_ID=replace-with-client-id.apps.googleusercontent.com
   FLOCK_GOOGLE_CLIENT_SECRET=replace-with-client-secret
   ```

4. Recreate the API to load the credentials. Rebuild the web image if its origin changed:

   ```bash
   docker compose up -d --build api ui
   ```

5. Check the API's configuration endpoint:

   ```bash
   curl --fail https://api.flock.example.com/api/v1/auth/config
   ```

   Its `google` value should be `true`.

6. Sign in through the dashboard with an existing or invited account and check that the expected organization is accessible.

The browser must reach the start and callback through the same host. If you use another dashboard, forward `/api/v1/auth/google/*` with cookies and redirects intact. See [Google authentication](authentication#google-sign-in) for flow details, account linking and failure reasons. Google sign-in does not open registration to people without an invitation.
