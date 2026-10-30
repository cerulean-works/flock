# S3 storage behavior

Look up bucket naming, key lifetimes, permissions and protocol support here. To connect a client, [generate S3 credentials](../cli/s3-access#temporary-keys-for-one-shell) or [configure an AWS profile](../cli/s3-access#configure-an-aws-profile).

## How it fits together

- **The proxy** is a second service, `app.api.src.s3_proxy.app:app`, from the same image as the API. It speaks the S3 API. It checks each request's signature against a Flock key, checks the key's user still belongs to its organization, renames the bucket, and forwards the request re-signed with the global key. Bodies stream through it both ways.
- **The API** keeps the records: which buckets exist, who may use them, and the keys. It creates and deletes buckets on the S3 server.
- **The database** is shared. The proxy reads keys, memberships, buckets and key lifetimes from the API's PostgreSQL, caches them for 10 seconds, and drops its cache as soon as any of them changes (`LISTEN s3_invalidate`). It writes nothing but each key's `last_used_at`. It takes no lock, so you can run as many proxies as you need.


## Buckets

Each organization has its own buckets. Any member can create one, from the dashboard's Storage tab, with `flock s3 buckets create`, or with `POST /api/v1/orgs/{org}/buckets`. Only admins delete them.

- **Names** use lowercase letters, digits, hyphens and dots, start and end with a letter or digit, and are unique within the organization. They're at most 57 characters, less the length of `FLOCK_S3_BUCKET_PREFIX`.
- **On the S3 server** a bucket is named `{org pk}-{name}`, with the organization's number in five digits: bucket `checkpoints` of organization 17 is `00017-checkpoints`. So two organizations can each have a `checkpoints`. The name is fixed when the bucket is created, so renaming an organization leaves its buckets alone.
- **Clients use the short name.** A key covers one organization, and names resolve there: with a key for `flock`, `s3://checkpoints/run-1/model.pt` is `00017-checkpoints/run-1/model.pt`. A name the organization doesn't have is `NoSuchBucket`, even if it exists on the S3 server.
- **No renames.** S3 can't rename a bucket, and renaming in place needs Ceph's admin API, which the global key doesn't have. To change a name, create a new bucket and copy into it.
- **Deleting** frees the name in the organization at once. A background task then empties the bucket and removes it from the S3 server, and picks up where it left off after a restart. Until it's done, creating the same name again answers 409. Deleting an organization deletes its buckets the same way.
- **History.** Flock records who created and deleted each bucket, and how: from the dashboard, or from the command line (with the machine). Events from before wandb keys were limited to the wandb SDK may say through the API, with the key. The Storage tab shows it from each bucket's menu.

Buckets created on the S3 server outside Flock aren't adopted, and creating a bucket whose name already exists there answers 409.

Names starting with `flock-` are reserved for buckets Flock manages, such as `flock-datasets` (see [Datasets](#datasets)) and `flock-models` (see [Models](#models)). Buckets that already had such a name keep working.


## Keys

An S3 key covers all the buckets of one organization. Its secret is derived from the key's id and a server secret in the database's `meta` table, so it's stored nowhere and a database dump without `meta` leaks no keys. Flock shows it once, when the key is made.

There are two ways to get one:

| | Ephemeral keys | Long-lived keys |
|---|---|---|
| Made by | `flock s3 credentials`, `flock.hub.s3`, `POST /s3/credentials` | the account page, `POST /me/s3-keys` |
| In exchange for | a CLI token | a browser session only |
| Lifetime | the organization's ephemeral lifetime, or less if asked | anything up to the organization's maximum, including never if it allows |
| Ends with | the CLI sign-in it was made for (`flock logout`, revoking the machine, a password change or reset) | revoking it on the account page; it survives password changes, as wandb keys do |

Because only a browser can make long-lived keys, a leaked CLI token can only mint keys that expire within the ephemeral lifetime, and a leaked wandb key can't mint any. The account page lists every key, with the ephemeral ones folded into one line per machine, and each can be revoked. Expired keys are deleted every hour.


## Key lifetimes

Each organization has two settings, which its admins change on its page or with `PATCH /api/v1/orgs/{org}`:

| Setting | Field | Default | Meaning |
|---|---|---|---|
| Ephemeral key lifetime | `s3_ephemeral_ttl` | 8 hours | How long ephemeral keys live unless less is asked for. |
| Maximum key lifetime | `s3_key_max_ttl` | 30 days | The longest any key may live. `null` means no limit. |

- The ephemeral lifetime can't exceed the maximum. Lowering only the maximum below it lowers it too.
- Lowering the maximum applies to existing keys at once: the proxy refuses a key older than the current maximum.
- Lifetimes are seconds, at least 600. The dashboard offers 1 day, 7, 30, 90 or 365 days, or no limit, for the maximum.


## Who can do what

| | Read objects | Write and delete objects | Create buckets | Delete buckets |
|---|---|---|---|---|
| Organization admin | yes | yes, with a key that isn't read-only | yes | yes |
| Member | yes | yes, with a key that isn't read-only | yes | no |
| Anyone else | no | no | no | no |

The proxy checks memberships on every request (through its cache), so removing someone from an organization stops their keys at once. Super admins count as admins of every organization.


## The Storage tab

The dashboard's sidebar has a **Storage** item for the organization in view, at `/storage/{org}`. It lists the organization's buckets, with who created them, and creates and deletes them. A deleted bucket stays listed as "being deleted" until the S3 server has emptied it. Its Connect panel shows the endpoint and region, with snippets for `flock s3 configure`, `flock s3 credentials` and `flock.hub.s3`.

Each bucket opens a file browser, one folder level at a time:

- **Listing.** Folders first, then objects, with their size, a type from the extension, and when they were last modified. 200 entries load at a time, with "Load more" for the rest. The filter box asks the S3 server for names starting with what you type, within the folder.
- **Uploads.** Pick files, or drop files and folders on the listing; dropped folders keep their paths. Files up to 100 MiB go in one request, larger ones as a multipart upload in 16 MiB parts, four at a time, up to 50 GiB a file. Each shows its progress and can be cancelled; a cancelled or failed multipart upload is aborted.
- **Folders.** S3 has none: "Create folder" writes an empty object named `folder/`, as S3 consoles do, so the folder shows before anything is in it.
- **Deleting.** Select objects and folders (shift-click selects a range) and delete them. Deleting a folder deletes everything under it, up to 10,000 objects in one go; above that Flock refuses and names the `aws s3 rm --recursive` command to use.
- **Objects** open on a page of their own: the key, size, ETag, content type and `x-amz-meta-*` metadata, Download, Copy S3 URI, a presigned link that works for an hour, and Delete. Images, PDFs, video and audio are previewed in the browser, text up to its first 1 MiB, JSON pretty-printed and Markdown rendered. SVG is only ever shown as an image, so scripts in it don't run.

Listing and deleting go through the API. Object bytes go straight between the browser and the proxy by presigned URLs, signed with a **browser key**: an ephemeral key of your sign-in and the organization, named `browser (ephemeral)`. It's reused while it has 15 minutes left, and deleted when you sign out. The proxy answers CORS for `FLOCK_WEB_URL` only, so presigned URLs can't be used by other sites' pages; they still work with `curl`.

Bucket and folder sizes aren't shown: the S3 server reports them only through Ceph's admin API, which the global key can't use.


## Datasets

Every organization has a bucket named `flock-datasets`, which Flock creates and manages. Flock makes it when the organization is created, when the API starts (for organizations without one), and when the Datasets tab opens and finds none. If the organization already had a bucket of that name, Flock takes it over with its objects. The bucket can't be deleted, and the Storage tab marks it **Managed by Flock**.

To S3 clients it's an ordinary bucket. Write Lance datasets into it with any client, for example with [`flock.hub.s3`](../cli/use-python-storage#write-a-lance-dataset):

```python
import lance
from flock.hub import s3

creds = s3.credentials("flock")
lance.write_dataset(
    table, creds.uri("flock-datasets", "eval", "mmlu"), storage_options=creds.storage_options()
)
```

The dashboard's **Datasets** item, at `/datasets/{org}`, browses the bucket:

- **Datasets are found, not registered.** Any folder that holds a `_versions/` folder is a dataset, at any depth. Flock keeps no table of them, so whatever writes to S3 is what the tab shows. Other folders and files are listed too, and files open in the Storage tab.
- **A dataset's page** shows its `README.md` (its card, read from the dataset's folder and the same for every version), its rows, its schema and its versions. Any version can be opened, by number or by tag. The rows show images, embeddings, long text and JSON in a readable form, and open a cell in full in a side panel.
- **Deleting** moves the dataset to a trash bucket, `FLOCK_S3_TRASH_BUCKET`, which no S3 key can reach. Organization admins restore deleted datasets, or delete them for good, from the organization's settings page. After 14 days they're removed. Deleting a dataset's folder from the Storage tab or an S3 client skips the trash.
- **Renaming** copies the dataset to its new path on the S3 server, then deletes the old objects. Anything still writing to the old path makes a new dataset there.
- **Tags** name versions, and Lance never cleans up a tagged version. Add, move and remove them in the Versions tab.
- **Limits.** Deleting and renaming copy the dataset in the request, so datasets over 10,000 objects or 100 GiB are refused, with an `aws s3` command to run instead.

Lance reads go straight from the API to the S3 server with the global key. Who wrote each version isn't recorded, since Lance doesn't store it. A writer can put it in `transaction_properties`. The routes are listed in [Datasets](api/datasets).


## Models

Every organization also has a bucket named `flock-models`, which holds the versions of its [stored models](../hub/store-models). Flock creates, adopts and protects it as it does `flock-datasets`, and the Storage tab marks it **Managed by Flock**.

- **Anyone in the organization reads it** with any key, through the proxy, the Storage tab or an S3 client.
- **Only uploads write to it.** The proxy refuses every write and delete in the bucket with `AccessDenied` ("flock-models is written through flock models push"), except the writes of an open upload into its own folder by the user who opened it. Copies into the bucket are refused even inside an upload.
- **The Storage tab only reads it.** It has no upload, folder or delete actions there, and the API's object delete and folder endpoints answer 403 for it.
- **Versions are deleted through the registry,** from the Models tab or the [model endpoints](api/models#deleting), so a version can't change under an eval that scored it. Deleted versions wait 14 days in the trash.

The dashboard's **Models** item, at `/models/{org}`, lists the models. [Browse and manage models](browse-models) shows how to use it.

## What the proxy supports

Clients address buckets path-style: `<endpoint>/<bucket>/<key>`. boto3, the AWS CLI, Lance, s3fs and DuckDB all support it; `flock.hub.s3` and `flock s3 configure` set it for you.

| Supported | Operations |
|---|---|
| Objects | GetObject (with ranges), HeadObject, PutObject, DeleteObject, DeleteObjects, CopyObject |
| Multipart uploads | CreateMultipartUpload, UploadPart, UploadPartCopy, CompleteMultipartUpload, AbortMultipartUpload, ListParts |
| Buckets | ListBuckets (the organization's), HeadBucket, GetBucketLocation, ListObjects, ListObjectsV2 |

- **Copies** work between buckets of the same organization.
- **Conditional requests** (`If-None-Match`, `If-Match` and the rest) go to the S3 server as they are, so it settles races between writers.
- **aws-chunked uploads** with an unsigned trailer, which recent AWS SDKs send by default, are decoded and their trailing checksum checked. Uploads with signed chunks answer 501.
- **CreateBucket and DeleteBucket** answer `NotImplemented`: buckets are created and deleted in Flock.
- **Anything else** (ACLs, versioning, lifecycle rules, policies) answers 501 `NotImplemented`.

Refusals are S3 XML errors: `InvalidAccessKeyId`, `SignatureDoesNotMatch`, `ExpiredToken` for an expired key, `AccessDenied` (a read-only key writing, or a user no longer in the organization) and `NoSuchBucket`. Errors from the S3 server pass through with the bucket called by its short name.

> [!NOTE]
> The proxy authenticates Flock's users. Anyone holding the global key can still reach the buckets on the S3 server directly.
