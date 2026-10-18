# Settings

The tracker reads its settings from environment variables when it starts. Each setting is an environment variable named `TRACKER_<SETTING>`, such as `TRACKER_DATABASE_URL`. To change a setting, restart the tracker.

## Tracker settings

| Variable | Default | Meaning |
|---|---|---|
| `TRACKER_DATABASE_URL` | `postgresql://tracker:tracker@127.0.0.1:5432/tracker` | PostgreSQL connection URL. |
| `TRACKER_FILES_DIR` | `tracker-files` | Directory for uploaded run files, relative to the working directory. The Docker image sets `/data/files`. |
| `TRACKER_MIGRATE_ON_STARTUP` | `true` | Apply pending migrations on startup. With `false`, the tracker refuses to start while migrations are pending. See [Migrations and upgrades](deploying#migrations-and-upgrades). |
| `TRACKER_DEFAULT_ENTITY` | `flock` | Name of the organization created for the first account when there is none. |
| `TRACKER_DEFAULT_PROJECT` | `uncategorized` | Project that runs land in when the client names none. |
| `TRACKER_PUBLIC_URL` | the address of each request | External base URL of the tracker, such as `https://tracker.example.com`. Used in file upload URLs, and as a fallback for `TRACKER_APP_URL`. |
| `TRACKER_APP_URL` | `TRACKER_PUBLIC_URL`, else the address of each request | Base URL of the dashboard. Invitation, reset and Google sign-in links point there. |
| `TRACKER_JWT_SECRET` | a random key stored in the database | Key that signs access tokens and the Google sign-in cookie. Changing it makes existing access tokens invalid. |
| `TRACKER_ACCESS_TOKEN_TTL` | `900` (15 minutes) | Seconds an access token is valid. |
| `TRACKER_REFRESH_TOKEN_TTL` | `2592000` (30 days) | Seconds a sign-in lasts without being refreshed. |
| `TRACKER_INVITE_TTL` | `604800` (7 days) | Seconds an invitation link is valid. |
| `TRACKER_RESET_TTL` | `86400` (24 hours) | Seconds a password reset link is valid. |
| `TRACKER_GOOGLE_CLIENT_ID` | none | OAuth client ID. With the secret, it turns on Sign in with Google. See [Setting up Sign in with Google](google-sign-in). |
| `TRACKER_GOOGLE_CLIENT_SECRET` | none | That OAuth client's secret. |
| `TRACKER_GOOGLE_REDIRECT_URL` | `<TRACKER_APP_URL>/api/v1/auth/google/callback` | Callback URL registered with Google. Without `TRACKER_APP_URL`, the default uses `TRACKER_PUBLIC_URL`, else the address of the request. |
| `TRACKER_HISTORY_CACHE_POINTS` | `20000000` | Curve points of finished runs kept in memory. Curves of active runs are always kept, in addition. See [Memory and startup time](deploying#memory-and-startup-time). |
| `TRACKER_CRASH_TIMEOUT` | `600` | Seconds without a heartbeat before an active run is marked `crashed`. See [Crash detection](deploying#crash-detection). |
| `TRACKER_POOL_SIZE` | `16` | Maximum number of database connections. |
| `TRACKER_CORS_ORIGINS` | none | Origins allowed to call the API from browsers, comma-separated, such as `https://flock.example.com`. |
| `TRACKER_LOG_LEVEL` | `info` | Log level: `debug`, `info`, `warning` or `error`. |

Values are read like this:
- **Empty means unset.** A variable set to an empty string takes its default, as Compose does for optional variables.
- **Booleans** accept `true`, `false`, `1`, `0`, `yes`, `no`, `on` and `off`, in any case. Any other value stops the tracker at startup.
- **Durations** are seconds, and may have decimals, such as `0.5`.
- **Lists** are comma-separated. Spaces around items are ignored.

## Docker Compose variables

`compose.yaml` reads these variables from the environment or from an `.env` file next to it:

| Variable | Default | Meaning |
|---|---|---|
| `POSTGRES_PASSWORD` | `tracker` | Password of the `tracker` database user. Compose builds `TRACKER_DATABASE_URL` from it. |
| `POSTGRES_SHARED_BUFFERS` | `1GB` | PostgreSQL `shared_buffers`. |
| `POSTGRES_EFFECTIVE_CACHE_SIZE` | `3GB` | PostgreSQL `effective_cache_size`. |
| `TRACKER_PORT` | `8080` | Host port the tracker is published on. |
| `TRACKER_VERSION` | `latest` | Tag of the `flock-tracker` image that Compose builds and runs. |

Compose passes these tracker settings through, with these defaults:

| Variable | Compose default |
|---|---|
| `TRACKER_LOG_LEVEL` | `info` |
| `TRACKER_DEFAULT_ENTITY` | `flock` |
| `TRACKER_PUBLIC_URL` | unset |
| `TRACKER_APP_URL` | `http://localhost:5173`, the address of flock-ui's development server |
| `TRACKER_JWT_SECRET` | unset |
| `TRACKER_GOOGLE_CLIENT_ID`, `TRACKER_GOOGLE_CLIENT_SECRET`, `TRACKER_GOOGLE_REDIRECT_URL` | unset |
| `TRACKER_CRASH_TIMEOUT` | `600` |
| `TRACKER_HISTORY_CACHE_POINTS` | `20000000` |

Compose doesn't pass the other settings, such as `TRACKER_CORS_ORIGINS` or `TRACKER_ACCESS_TOKEN_TTL`. To set one, add it to the `tracker` service's `environment` in a `compose.override.yaml` next to `compose.yaml`, which Compose reads automatically:

```yaml
services:
  tracker:
    environment:
      TRACKER_CORS_ORIGINS: https://flock.example.com
```
