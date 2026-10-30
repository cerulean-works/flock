# Python S3 API

`flock.hub.s3` exchanges a Flock sign-in for storage access to one organization. Start with [Access storage from Python](use-python-storage) or [Write a Lance dataset](use-python-storage#write-a-lance-dataset).

## Renewal and client lifetimes

| Consumer | Credential behavior |
|---|---|
| `S3Credentials` accessors | Fetch on first use and renew when fewer than five minutes remain, or half the lifetime for keys shorter than ten minutes. |
| `boto3_client()` | Uses botocore refreshable credentials. A long-lived client can renew without being recreated. |
| `storage_options()`, `env()` | Return a snapshot of current values. A consumer retaining the returned dictionary does not receive later renewals. |
| Pickled `S3Credentials` | Omits its issued key. Each unpickled process fetches its own. |

Lance retains the storage options used to open a dataset. Request current options when opening it again. An already open dataset can fail after its retained key expires.

The helper needs httpx. boto3 and other client libraries are imported only when used. [Credential resolution](credentials) determines the Flock token and host; [S3 behavior](../app/s3-behavior#keys) determines server-side lifetimes and revocation.

## Credentials

[[autodoc]] hub.s3.credentials

[[autodoc]] hub.s3.S3Credentials

[[autodoc]] hub.s3.S3Error

## Endpoint and buckets

[[autodoc]] hub.s3.endpoint

[[autodoc]] hub.s3.list_buckets

[[autodoc]] hub.s3.create_bucket

[[autodoc]] hub.s3.delete_bucket
