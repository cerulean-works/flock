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
