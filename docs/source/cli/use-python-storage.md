# Use storage from Python

Use your Flock sign-in to read and write organization storage with boto3 or publish a Lance dataset to the dashboard.

## Choose a client

Both clients use the [S3 authentication model](s3-access#how-authentication-works): Flock exchanges your machine's CLI sign-in for an ephemeral S3 key pair. They address the same organization buckets.

**boto3** works with individual objects, such as checkpoint files. **Lance** stores a versioned tabular dataset as a collection of objects; Flock recognizes datasets written under the managed `flock-datasets` bucket.

The helper uses your default organization. `s3.credentials("organization-id")` selects another organization you belong to when needed. For a job with no interactive sign-in, supply [FLOCK_TOKEN](authenticate-jobs).

## Read and write objects with boto3

[Sign in](sign-in) and install boto3:

```bash
uv pip install boto3
```

Use an existing bucket or [create one](s3-access#work-with-buckets). This example uses `checkpoints` and a small test object:

```python
from flock.hub import s3

creds = s3.credentials()
client = creds.boto3_client()
client.put_object(Bucket="checkpoints", Key="examples/check.txt", Body=b"storage works\n")
response = client.get_object(Bucket="checkpoints", Key="examples/check.txt")
assert response["Body"].read() == b"storage works\n"
client.delete_object(Bucket="checkpoints", Key="examples/check.txt")
```

The assertion verifies the round trip; the last line removes the test object. To upload a checkpoint file instead:

```python
client.upload_file("model.pt", "checkpoints", "run-1/model.pt")
```

This client uses refreshable credentials, so it can renew keys while your process runs. An [AWS profile](s3-access#configure-an-aws-profile) is another option for existing boto3 programs: `boto3.Session(profile_name="flock").client("s3")` uses the profile you configured.

## Write a Lance dataset

Install Lance and Arrow:

```bash
uv pip install pylance pyarrow
```

Choose an unused dataset path. This writes a one-row dataset, then checks that it can be read:

```python
import lance
import pyarrow as pa
from flock.hub import s3

creds = s3.credentials()
uri = creds.uri("flock-datasets", "examples", "questions")
table = pa.table({"question": ["2 + 2?"], "answer": ["4"]})
lance.write_dataset(table, uri, storage_options=creds.storage_options())
dataset = lance.dataset(uri, storage_options=creds.storage_options())
assert dataset.to_table().to_pylist() == [{"question": "2 + 2?", "answer": "4"}]
```

Open **Datasets** in [the dashboard](https://flock.cerulean.works) and navigate to `examples/questions`. The rows and schema should match the table. Append rows with `mode="append"`; each write creates another dataset version. A `README.md` object in the dataset folder supplies its dashboard card.

Lance retains the credential values used when opening a dataset. Unlike the boto3 helper, an already open dataset does not receive later renewals from `creds`. Request current `storage_options()` when opening again, and account for expiry in long-lived jobs. The [Python S3 reference](python-s3#renewal-and-client-lifetimes) covers these lifetimes and subprocess behavior.

[Storage and datasets](../app/storage-and-datasets) explains how to inspect versions, create tags and restore deleted datasets.
