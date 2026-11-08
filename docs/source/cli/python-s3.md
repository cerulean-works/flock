# Python S3 API

`flock.hub.s3` exchanges a Flock sign-in for storage access to one organization. Start with [Access storage from Python](use-python-storage) or [Use datasets](../hub/use-datasets).

## Renewal and client lifetimes

| Consumer | Credential behavior |
|---|---|
| `S3Credentials` accessors | Fetch on first use and renew when fewer than five minutes remain, or half the lifetime for keys shorter than ten minutes. |
| `boto3_client()` | Uses botocore refreshable credentials. A long-lived client can renew without being recreated. |
| `get_process()` followed by Lance | Sets up the current process and its spawned workers. Lance's native AWS provider renews keys using the credential process's `Expiration`. |
| `storage_options()`, `env()` | Return a snapshot of current values. A consumer retaining the returned dictionary does not receive later renewals. |
| Pickled `S3Credentials` | Omits its issued key. Each unpickled process fetches its own. |

The helper needs httpx. boto3 and other client libraries are imported only when used. [Credential resolution](credentials) determines the Flock token and host; [S3 behavior](../app/s3-behavior#keys) determines server-side lifetimes and revocation.

## Native renewal from Python

Call `get_process` before constructing Lance datasets, boto3 sessions or other AWS clients:

```python
import os

import lance
from flock.hub import s3

environment = s3.get_process(org="cerulean-labs", read_only=True)
assert os.environ["AWS_CONFIG_FILE"] == environment["AWS_CONFIG_FILE"]
dataset = lance.dataset("s3://flock-datasets/examples/questions", version=1)
```

`get_process` applies the environment itself. Its return value is a dictionary of the values it set, for inspection. You do not need to call `os.environ.update`, run `flock s3 configure`, or export variables in your shell. If you pass an explicit `env` when launching a subprocess, use `os.environ.copy()` after setup so it includes both the changes and the removals.

Setup resolves your Flock sign-in and discovers the organization's endpoint and region. It creates a temporary AWS profile with a credential-process command and updates the current process's environment. Setup does not issue an S3 key. The first authenticated storage operation obtains one through the credential process.

### How keys are fetched and renewed

1. Lance's native AWS provider reads `AWS_PROFILE` and `AWS_CONFIG_FILE` to locate the profile and its `credential_process` command.
2. The provider runs the current Python interpreter with `-m flock.hub.s3._process` and the path to the private Flock settings file. It does not search `PATH` for a `flock` executable.
3. That local subprocess reads the pinned Flock sign-in and requests an S3 key for the configured organization, TTL and read-only setting. It writes the AWS credential JSON to stdout: `Version`, `AccessKeyId`, `SecretAccessKey` and `Expiration`.
4. Lance caches the credentials and checks their expiration as storage requests are made. When renewal is due, it runs the same process again for a new key pair. There is no Python background renewal thread.

The temporary settings pin the host and token resolved during setup. Changing `FLOCK_TOKEN`, `FLOCK_HOST` or the saved sign-in later does not change that provider's identity. Revoking the pinned sign-in or losing access to the Flock API can still make a later fetch fail.

Omit access keys from `storage_options` when using this flow. Explicitly supplied keys select static authentication, so an existing client using `creds.storage_options()` does not receive credential-process renewal. Non-credential options, such as a request timeout, can still be supplied through `storage_options`.

### Environment changes

The helper changes the current process and the children it starts. It does not change the parent shell. Call it before creating clients: existing clients and sessions retain their own configuration. Calling it again selects a new temporary setup for clients created afterward.

| Variables set | Value |
|---|---|
| `AWS_PROFILE`, `AWS_DEFAULT_PROFILE` | `flock` |
| `AWS_CONFIG_FILE` | Absolute path to the temporary profile's `config` file. |
| `AWS_SHARED_CREDENTIALS_FILE` | Absolute path to an empty temporary credentials file, so a shared credentials file cannot override the profile. |
| `AWS_SDK_LOAD_CONFIG` | `1` |
| `AWS_ENDPOINT`, `AWS_ENDPOINT_URL`, `AWS_ENDPOINT_URL_S3` | The endpoint returned by Flock. |
| `AWS_REGION`, `AWS_DEFAULT_REGION` | The region returned by Flock. |
| `AWS_VIRTUAL_HOSTED_STYLE_REQUEST` | `false`, for path-style requests. |
| `ALLOW_HTTP`, `AWS_ALLOW_HTTP` | `true` for an `http://` endpoint, otherwise `false`. |
| `AWS_SKIP_SIGNATURE` | `false`, so requests are signed. |
| `AWS_EC2_METADATA_DISABLED` | `true` |
| `AWS_IGNORE_CONFIGURED_ENDPOINT_URLS` | `false` |

The following variables are removed if present. These removals are already applied to `os.environ`; they are not entries in the returned dictionary.

| Conflicting credential source | Variables removed |
|---|---|
| Static keys | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_SECURITY_TOKEN` |
| Web identity and role settings | `AWS_WEB_IDENTITY_TOKEN_FILE`, `AWS_ROLE_ARN`, `AWS_ROLE_SESSION_NAME` |
| Container credentials | `AWS_CONTAINER_CREDENTIALS_FULL_URI`, `AWS_CONTAINER_CREDENTIALS_RELATIVE_URI`, `AWS_CONTAINER_AUTHORIZATION_TOKEN`, `AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE` |
| Explicit Lance provider selection | `AWS_PROVIDER_SCHEME` |

### Temporary files and workers

Each call creates a private temporary directory with permissions `0700`. Its files have permissions `0600`:

| File | Contents |
|---|---|
| `config` | The AWS profile, endpoint, region, path-style setting and credential-process command. |
| `credentials` | An empty shared credentials file. |
| `flock.json` | The resolved Flock API host and CLI token, organization, TTL and read-only setting. |

The CLI token is kept in `flock.json`; it is not placed in the command arguments or AWS profile. No issued S3 key is written to these files. Your existing AWS config remains unchanged. The temporary directories stay available for the interpreter's lifetime and are removed on normal interpreter shutdown.

Spawned DataLoader workers inherit the environment and read these same files. Each worker opens its own Lance dataset and obtains credentials through the native provider. Persistent workers can renew keys without reopening their dataset. Keep setup in the parent, before starting workers, as in the [PyTorch example](../hub/use-datasets#read-from-worker-processes).

The training `get_dataset` helper calls `get_process(org="cerulean-labs", read_only=True)` automatically for `s3://`, `s3a://` and `s3n://` URIs. Local dataset paths skip Flock setup. See [Train with Flock](../training/trainer).

For command-line tools launched directly from a shell, [configure a persistent AWS profile](s3-access#configure-an-aws-profile) instead.

[Lance's credential-provider documentation](https://lance.org/guide/object_store/#credential-provider-selection) describes the native AWS chain. [AWS's process-provider documentation](https://docs.aws.amazon.com/sdkref/latest/guide/feature-process-credentials.html) specifies the JSON output and `Expiration` field. [Lance 14's AWS implementation](https://github.com/lance-format/lance/blob/v14.0.0/rust/lance-io/src/object_store/providers/aws.rs#L432) shows provider selection and expiry-based refresh.

## Credentials

[[autodoc]] hub.s3.get_process

[[autodoc]] hub.s3.credentials

[[autodoc]] hub.s3.S3Credentials

[[autodoc]] hub.s3.S3Error

## Endpoint and buckets

[[autodoc]] hub.s3.endpoint

[[autodoc]] hub.s3.list_buckets

[[autodoc]] hub.s3.create_bucket

[[autodoc]] hub.s3.delete_bucket
