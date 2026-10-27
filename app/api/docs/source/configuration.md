# Settings

Flock reads its settings from environment variables when it starts. Each setting is an environment variable named `FLOCK_<SETTING>`, such as `FLOCK_DATABASE_URL`. To change a setting, restart Flock.

## Flock settings

| Variable | Default | Meaning |
|---|---|---|
| `FLOCK_DATABASE_URL` | `postgresql://flock:flock@127.0.0.1:5432/flock` | PostgreSQL connection URL. |
| `FLOCK_FILES_DIR` | `flock-files` | Directory for uploaded run files, relative to the working directory. The Docker image sets `/data/files`. |
| `FLOCK_MIGRATE_ON_STARTUP` | `true` | Apply pending migrations on startup. With `false`, Flock refuses to start while migrations are pending. See [Migrations and upgrades](deploying#migrations-and-upgrades). |
| `FLOCK_API_PUBLIC_URL` | the address of each request | Public API base URL reached by training machines, such as `https://api.flock.example.com`. Used in signed file upload URLs. The dashboard uses the same variable in SDK instructions. |
| `FLOCK_WEB_URL` | `FLOCK_API_PUBLIC_URL`, else the address of each request | Public dashboard origin. Setup, invitation, reset and Google sign-in links point there. Also the web image build argument for redirects and form origin checks. |
| `FLOCK_JWT_SECRET` | a random key stored in the database | Key that signs access tokens and the Google sign-in cookie. Changing it makes existing access tokens invalid. |
| `FLOCK_ACCESS_TOKEN_TTL` | `900` (15 minutes) | Seconds an access token is valid. |
| `FLOCK_REFRESH_TOKEN_TTL` | `2592000` (30 days) | Seconds a sign-in lasts without being refreshed. |
| `FLOCK_INVITE_TTL` | `604800` (7 days) | Seconds an invitation link is valid. |
| `FLOCK_RESET_TTL` | `86400` (24 hours) | Seconds a password reset link is valid. |
| `FLOCK_DEVICE_CODE_TTL` | `900` (15 minutes) | Seconds a `flock login` code waits to be approved in the browser. See [Signing in from the command line](accounts#signing-in-from-the-command-line). |
| `FLOCK_CLI_SESSION_TTL` | `7776000` (90 days) | Seconds a command-line sign-in lasts without being used. Each use extends it. |
| `FLOCK_GOOGLE_CLIENT_ID` | none | OAuth client ID. With the secret, it turns on Sign in with Google. See [Setting up Sign in with Google](google-sign-in). |
| `FLOCK_GOOGLE_CLIENT_SECRET` | none | That OAuth client's secret. |
| `FLOCK_GOOGLE_REDIRECT_URL` | `<FLOCK_WEB_URL>/api/v1/auth/google/callback` | Callback URL registered with Google. Without `FLOCK_WEB_URL`, the default uses `FLOCK_API_PUBLIC_URL`, else the address of the request. |
| `FLOCK_GRAVATAR` | `true` | Fetch each new account's avatar from Gravatar. The request sends Gravatar the SHA-256 hash of the account's email. See [Avatars](accounts#avatars). |
| `FLOCK_GRAVATAR_URL` | `https://gravatar.com/avatar/` | Where avatars are fetched from: the URL that the email's hash is appended to. A Libravatar-compatible server works too. |
| `FLOCK_WANDB_BASE_URL` | `https://api.wandb.ai` | The W&B server that [imports](importing-from-wandb) read from, when an import doesn't name one. Set it for W&B Dedicated Cloud or a self-managed W&B server. |
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

See [Environment variables](deploying#environment-variables) for the deployment variables, their consumers, defaults and migration from previous names. Both Compose files forward the optional API settings above; empty entries use the API defaults. `FLOCK_DATABASE_URL` is constructed from `POSTGRES_PASSWORD`, and the image fixes `FLOCK_FILES_DIR` to the mounted `/data/files` directory.
