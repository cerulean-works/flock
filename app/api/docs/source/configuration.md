# Settings

Flock reads its settings from environment variables when it starts. Each setting is an environment variable named `FLOCK_<SETTING>`, such as `FLOCK_DATABASE_URL`. To change a setting, restart Flock.

## Flock settings

| Variable | Default | Meaning |
|---|---|---|
| `FLOCK_DATABASE_URL` | `postgresql://flock:flock@127.0.0.1:5432/flock` | PostgreSQL connection URL. |
| `FLOCK_FILES_DIR` | `flock-files` | Directory for uploaded run files, relative to the working directory. The Docker image sets `/data/files`. |
| `FLOCK_MIGRATE_ON_STARTUP` | `true` | Apply pending migrations on startup. With `false`, Flock refuses to start while migrations are pending. See [Migrations and upgrades](deploying#migrations-and-upgrades). |
| `FLOCK_DEFAULT_PROJECT` | `uncategorized` | Project that runs land in when the client names none. |
| `FLOCK_PUBLIC_URL` | the address of each request | External base URL of Flock, such as `https://flock.example.com`. Used in file upload URLs, and as a fallback for `FLOCK_APP_URL`. |
| `FLOCK_APP_URL` | `FLOCK_PUBLIC_URL`, else the address of each request | Base URL of the dashboard. Invitation, reset and Google sign-in links point there. |
| `FLOCK_JWT_SECRET` | a random key stored in the database | Key that signs access tokens and the Google sign-in cookie. Changing it makes existing access tokens invalid. |
| `FLOCK_ACCESS_TOKEN_TTL` | `900` (15 minutes) | Seconds an access token is valid. |
| `FLOCK_REFRESH_TOKEN_TTL` | `2592000` (30 days) | Seconds a sign-in lasts without being refreshed. |
| `FLOCK_INVITE_TTL` | `604800` (7 days) | Seconds an invitation link is valid. |
| `FLOCK_RESET_TTL` | `86400` (24 hours) | Seconds a password reset link is valid. |
| `FLOCK_GOOGLE_CLIENT_ID` | none | OAuth client ID. With the secret, it turns on Sign in with Google. See [Setting up Sign in with Google](google-sign-in). |
| `FLOCK_GOOGLE_CLIENT_SECRET` | none | That OAuth client's secret. |
| `FLOCK_GOOGLE_REDIRECT_URL` | `<FLOCK_APP_URL>/api/v1/auth/google/callback` | Callback URL registered with Google. Without `FLOCK_APP_URL`, the default uses `FLOCK_PUBLIC_URL`, else the address of the request. |
| `FLOCK_GRAVATAR` | `true` | Fetch each new account's avatar from Gravatar. The request sends Gravatar the SHA-256 hash of the account's email. See [Avatars](accounts#avatars). |
| `FLOCK_GRAVATAR_URL` | `https://gravatar.com/avatar/` | Where avatars are fetched from: the URL that the email's hash is appended to. A Libravatar-compatible server works too. |
| `FLOCK_HISTORY_CACHE_POINTS` | `20000000` | Curve points of finished runs kept in memory. Curves of active runs are always kept, in addition. See [Memory and startup time](deploying#memory-and-startup-time). |
| `FLOCK_CRASH_TIMEOUT` | `600` | Seconds without a heartbeat before an active run is marked `crashed`. See [Crash detection](deploying#crash-detection). |
| `FLOCK_POOL_SIZE` | `16` | Maximum number of database connections. |
| `FLOCK_CORS_ORIGINS` | none | Origins allowed to call the API from browsers, comma-separated, such as `https://flock.example.com`. |
| `FLOCK_LOG_LEVEL` | `info` | Log level: `debug`, `info`, `warning` or `error`. |

Values are read like this:
- **Empty means unset.** A variable set to an empty string takes its default, as Compose does for optional variables.
- **Booleans** accept `true`, `false`, `1`, `0`, `yes`, `no`, `on` and `off`, in any case. Any other value stops Flock at startup.
- **Durations** are seconds, and may have decimals, such as `0.5`.
- **Lists** are comma-separated. Spaces around items are ignored.

## Docker Compose variables

`app/compose.yaml` reads these variables from the environment or from an `.env` file next to it:

| Variable | Default | Meaning |
|---|---|---|
| `POSTGRES_PASSWORD` | `flock` | Password of the `flock` database user. Compose builds `FLOCK_DATABASE_URL` from it. |
| `POSTGRES_SHARED_BUFFERS` | `1GB` | PostgreSQL `shared_buffers`. |
| `POSTGRES_EFFECTIVE_CACHE_SIZE` | `3GB` | PostgreSQL `effective_cache_size`. |
| `APP_URL` | `http://localhost:3000` | Public dashboard URL, built into the web image. Also the default for `FLOCK_APP_URL`. |
| `APP_PORT` | `3000` | Dashboard host port. |
| `UI_VERSION` | `latest` | Tag of the `flock-ui` image. |
| `FLOCK_URL` | `http://api:8080` | API URL as the dashboard container reaches it. |
| `FLOCK_API_KEY` | unset | API key used by the dashboard when nobody is signed in. |
| `FLOCK_PORT` | `8080` | Host port Flock is published on. |
| `FLOCK_VERSION` | `latest` | Tag of the `flock-api` image that Compose builds and runs. |

Compose passes these Flock settings through, with these defaults:

| Variable | Compose default |
|---|---|
| `FLOCK_LOG_LEVEL` | `info` |
| `FLOCK_PUBLIC_URL` | unset for the API; `http://localhost:8080` for the dashboard |
| `FLOCK_APP_URL` | `APP_URL`, else `http://localhost:3000` |
| `FLOCK_JWT_SECRET` | unset |
| `FLOCK_GOOGLE_CLIENT_ID`, `FLOCK_GOOGLE_CLIENT_SECRET`, `FLOCK_GOOGLE_REDIRECT_URL` | unset |
| `FLOCK_CRASH_TIMEOUT` | `600` |
| `FLOCK_GRAVATAR` | `true` |
| `FLOCK_HISTORY_CACHE_POINTS` | `20000000` |

Compose doesn't pass the other settings, such as `FLOCK_CORS_ORIGINS` or `FLOCK_ACCESS_TOKEN_TTL`. To set one, add it to the `api` service's `environment` in a `compose.override.yaml` next to `compose.yaml`, which Compose reads automatically:

```yaml
services:
  api:
    environment:
      FLOCK_CORS_ORIGINS: https://flock.example.com
```
