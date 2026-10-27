# flock.cli.s3

S3 access from Python, for any Flock package or training job. [`~cli.s3.credentials`] fetches a key for an organization's buckets and renews it before it expires, in the shapes boto3, Lance, s3fs and the AWS tools want.

```python
from flock.cli import s3

creds = s3.credentials(org="acme")
client = creds.boto3_client()
client.upload_file("model.pt", "checkpoints", "run-1/model.pt")
```

- **Credentials.** The Flock credential comes from [`~cli.credentials.resolve`], so `flock login` on the machine, or `FLOCK_TOKEN` in a job, is all it needs.
- **Renewal.** A key is fetched when first needed, and again once fewer than 5 minutes are left (or half its lifetime, for keys shorter than 10 minutes). Every accessor returns current values. `boto3_client()` builds on botocore's refreshable credentials, so a long-lived client renews by itself.
- **Lance.** `storage_options()` returns the current key, but Lance keeps the options it opened a dataset with. Call it again when you open a dataset after a key has expired.
- **Pickling.** An [`~cli.s3.S3Credentials`] pickles without its key, so each DataLoader worker or subprocess fetches its own.
- **Dependencies.** No S3 library is required: boto3 is imported when `boto3_client()` is called.

```python
import lance

ds = lance.dataset(creds.uri("datasets", "rows"), storage_options=creds.storage_options())
```

## Datasets

Every organization has a bucket named `flock-datasets`, which Flock creates and manages. Any Lance dataset you write into it shows up in the dashboard's **Datasets** tab, with its README, rows, schema and versions. A dataset is any folder that holds a Lance `_versions/` folder, so folders can nest to any depth.

```python
import lance
import pyarrow as pa
from flock.cli import s3

creds = s3.credentials("acme")
table = pa.table({"question": ["2 + 2?"], "answer": ["4"]})
lance.write_dataset(
    table, creds.uri("flock-datasets", "eval", "mmlu"), storage_options=creds.storage_options()
)
```

- **Path.** This writes `s3://flock-datasets/eval/mmlu`, which the tab shows as `eval/mmlu`.
- **Versions.** Each write makes a new version, as Lance always does. Append with `mode="append"`. To describe a version, pass `transaction_properties={"message": "…"}`, which the Versions tab shows.
- **README.** A `README.md` in the dataset's folder (`eval/mmlu/README.md`) is shown as its card.

[[autodoc]] cli.s3.credentials

[[autodoc]] cli.s3.S3Credentials

[[autodoc]] cli.s3.S3Error

[[autodoc]] cli.s3.endpoint

[[autodoc]] cli.s3.list_buckets

[[autodoc]] cli.s3.create_bucket

[[autodoc]] cli.s3.delete_bucket
