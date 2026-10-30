# Use S3 storage

Connect AWS tools to your organization's storage. Start by choosing how the client will authenticate, then configure renewable access or request a key for one shell.

## Buckets, objects and endpoints

S3 is an object-storage interface. A **bucket** holds objects; an **object key** is the name of one object inside it. For example, `s3://checkpoints/run-1/model.pt` means the object `run-1/model.pt` in the bucket `checkpoints`. The slashes look like directories, but are part of the object's name.

An **endpoint** is the HTTP address the tool sends S3 requests to. Flock fronts Cerulean's storage with an S3-compatible proxy, so you can use AWS tools without an Amazon account. The CLI supplies the endpoint and region for you.

Buckets belong to an organization. A storage credential covers that organization's buckets, using the short bucket names you see in Flock. The default organization shown by `uv run flock whoami` is used unless you select another with `--org`.

## How authentication works

An S3 credential is a pair: an **access key ID** identifies it and a **secret access key** signs requests. Your client uses the secret to compute a signature; it does not send the secret as your account password. Flock's proxy checks the signature and the credential's access before forwarding the operation to storage.

Your CLI token is a different credential. `flock login` signs the machine in to Flock; `flock s3 credentials` exchanges that sign-in for an S3 access key and secret. You do not need Cerulean's upstream administrative storage key.

### Ephemeral and persistent keys

| | Ephemeral keys | Persistent (long-lived) keys |
|---|---|---|
| Create through | CLI or Flock's Python helpers | **S3 keys** on your account page in the browser |
| Intended for | Shells, jobs and clients that can request fresh keys | Tools that cannot renew credentials themselves |
| Lifetime | **8 hours** by default; expires automatically under the organization's policy | Up to **30 days** by default; ends at expiry or revocation, within the organization's policy |
| Tied to a CLI sign-in | Yes: ending that sign-in also ends its keys | No: CLI logout does not revoke it |
| Can be read-only | Yes | Yes |

Persistent does not necessarily mean permanent. A persistent key can still expire, and it should be revoked when the tool no longer needs it. A password change ends CLI sign-ins and their ephemeral keys; persistent S3 keys survive it until expiry or revocation. The [key-lifetime reference](../app/s3-behavior#key-lifetimes) describes the policy limits.

Your current organization membership and the key's read-only setting still control access, including for persistent keys. The dashboard obtains its own temporary keys for browser transfers; ordinary dashboard use does not require creating a persistent key.

For normal Cerulean use, start with ephemeral keys. **Renewal** means requesting a fresh pair before the current pair expires. An AWS profile configured below handles that for you. Exported shell variables contain a fixed pair, so they must be replaced after expiry.

## Configure an AWS profile

Install the AWS CLI and [sign in](sign-in) first. Use a shell without old `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` exports, which can override a profile's credentials.

1. Create the default `flock` profile:

   ```bash
   uv run flock s3 configure
   ```

   This writes the endpoint, region and a credential-fetching command to your AWS config. It asks before replacing an existing profile named `flock`; it does not save an S3 secret there.

2. Check the connection:

   ```bash
   aws s3 ls --profile flock
   ```

3. Use the bucket names from that listing. For example, if your organization has a `checkpoints` bucket:

   ```bash
   aws s3 ls s3://checkpoints/ --profile flock
   ```

The profile asks Flock for keys as needed, so later AWS CLI invocations can keep working after an earlier key expires. See [flock s3 configure](commands/s3#flock-s3-configure) for the config file location and renewal constraints.

## Temporary keys for one shell

If a tool expects environment variables, request a pair and load it into the current shell:

```bash
eval "$(uv run flock s3 credentials)"
aws s3 ls
```

The output supplies the access key, secret, endpoint and region. It contains secrets: do not paste it into logs or shared files. The bucket listing should match your organization's **Storage** page.

To request a read-only pair for one hour:

```bash
eval "$(uv run flock s3 credentials --read-only --ttl 1h)"
```

These exports do not renew. Request another pair when they expire, or use the profile for ongoing AWS CLI access. JSON and AWS credential-process outputs are documented in [S3 commands](commands/s3).

## Use a persistent key

For a client that cannot renew keys, open [your account page](https://flock.cerulean.works/account), find **S3 keys**, and create a named key for the intended organization. Choose a lifetime and read-only access when appropriate. Copy the access key ID and secret into that client's credential fields and use the endpoint and region shown by Flock.

The secret is shown when the key is created. Keep it in the tool's secret store and revoke the entry in Flock when retiring the tool. A CLI token can issue ephemeral keys, but only a browser session can create these persistent keys.

## Work with buckets

List buckets before creating one, and use the default organization unless you deliberately need another:

```bash
uv run flock s3 buckets list
uv run flock s3 buckets create checkpoints
```

Use a new name for the create command. Members can create buckets; deleting one requires an organization admin. The managed `flock-datasets` bucket already exists for Lance datasets and cannot be deleted.

> [!CAUTION]
> `flock s3 buckets delete` permanently removes the bucket and its objects. Dataset objects deleted this way do not go to the dataset trash.

[Bucket commands](commands/s3#flock-s3-buckets) cover deletion and flags. To browse or transfer objects through the dashboard, use [Storage and datasets](../app/storage-and-datasets). To work from code, use [Python storage](use-python-storage), whose boto3 helper can renew keys within a running process.
