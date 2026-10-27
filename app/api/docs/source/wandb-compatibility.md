# wandb compatibility

What Flock supports of the `wandb` SDK, where it behaves differently from wandb's own server, and the protocol endpoints it implements. Flock is tested with the `wandb` SDK 0.31. For how to log, see [Logging from training code](logging).

## Supported SDK features

| Feature | Supported |
|---|---|
| `wandb login --host`, `WANDB_BASE_URL` and `WANDB_API_KEY` | yes |
| `wandb.init` with `entity`, `project`, `name`, `id`, `config`, `tags`, `notes`, `group` and `job_type` | yes |
| `run.log`, with nested dicts, an explicit `step`, `NaN` and infinity, and media values | yes. Media values aren't plotted. |
| `run.summary`, and `run.define_metric(..., summary=...)` | yes |
| `run.define_metric(..., step_metric=...)`, with globs, and `hidden=True` | yes. Dashboards plot metrics against their step metric. See [Step metrics](logging#step-metrics). |
| `run.config.update`, and changing `run.tags` or `run.notes` | yes |
| `resume="allow"` and `resume="must"` | yes |
| Exit codes, preemption, and crash detection | yes |
| System metrics: CPU, GPU, memory, disk and network samples | yes |
| Console output | yes |
| Run files: `config.yaml`, `requirements.txt`, `wandb-metadata.json`, `wandb-summary.json` and other uploads | yes |
| Offline runs, synced with `wandb sync` | yes |
| Artifacts | no |
| Sweeps | no |
| Reports | no |
| The public API, `wandb.Api()` | no. Use Flock's [query API](api/queries), whose filters use the same syntax. |
| wandb's web UI | no. The links the SDK prints lead nowhere. |

Calls to features Flock doesn't support fail with `operation '<name>' is not supported by this server`, instead of an answer that could corrupt your data.

## Differences from wandb's server

- **Organizations are never created by logging.** `wandb.init(entity=...)` needs an existing organization that you belong to. Projects are created by logging, as in wandb.
- **Members can only write to their own runs.** Resuming a run that someone else logged fails, unless you're an org admin.
- **The default entity is your default organization.** It's the first organization you joined, unless you chose another one. `wandb login` shows it.
- **Your user name is your email address.** Flock reports it as the SDK's user name.
- **Deleted runs stay deleted.** If you delete a run while its client is still logging, the client finishes normally and its data is dropped. See [Deleting runs](managing-runs#deleting-runs).
- **Project names from the SDK aren't checked.** Names created through Flock's API follow [stricter rules](managing-runs#projects).
- **Flock generates display names** in wandb's `adjective-noun-N` style for runs logged without a `name`.

## Protocol endpoints

These endpoints serve the SDK. They aren't part of the documented HTTP API, and they don't appear in `/docs`.

The first two take a [wandb key](api/auth#wandb-keys), sent as the SDK does (HTTP Basic auth, as `api:<key>`), or as `Authorization: Bearer <key>` or `x-api-key: <key>`. They also take Flock's other sign-ins, a browser session or a CLI token. wandb keys work here and nowhere else (see [Credentials](api/index#credentials)).

| Endpoint | Purpose |
|---|---|
| `POST /graphql` | The GraphQL operations the SDK sends, recognized by their name. Requests may be gzipped. |
| `POST /files/{entity}/{project}/{run}/file_stream` | A run's history lines, system samples, console output, summary and exit. Gzipped bodies are accepted. |
| `PUT /storage/{entity}/{project}/{run}/{name}?sig=...` | Uploads of run files. The URL's HMAC signature replaces credentials. |
| `POST /sdk/otel/...` | The SDK's own telemetry. Flock accepts it and drops it. |

Flock answers these GraphQL operations:

| Operation | Purpose |
|---|---|
| `Viewer` | Who the wandb key belongs to: the user, their default organization and their organizations. |
| `UpsertBucket` | Create or update a run. |
| `RunResumeStatus` | The state of a run to resume: line counts, last lines, summary and config. |
| `CreateRunFiles` | Signed upload URLs for run files. |
| `RunState` | A run's state. |

It answers a few more, such as `ServerFeaturesQuery` and `MarkRunFilesUploaded`, with fixed responses. It answers any other operation with an error.

There is no general GraphQL engine. Each operation returns the fields the SDK asks for, so other GraphQL clients won't work.
