# Datasets

Flock's hub keeps datasets next to your runs. A dataset is a [Lance](https://lance.org) dataset stored in Flock's S3 server, and every write to it makes a new version that stays readable. This page covers turning the hub on, creating a dataset, writing and reading it, managing its versions, and mirroring Hugging Face datasets.

The examples use `curl` with an access token in `$TOKEN` and Flock's URL in `$FLOCK`, as in the [Quickstart](quickstart). The endpoints are listed in [Datasets and S3 keys](api/datasets).

## Turning the hub on

The hub needs an S3-compatible server, such as Ceph RGW, with one bucket and one key that only Flock uses. Set these and restart Flock:

| Setting | Value |
|---|---|
| `FLOCK_HUB_S3_ENDPOINT` | The S3 server's URL. |
| `FLOCK_HUB_S3_ACCESS_KEY_ID`, `FLOCK_HUB_S3_SECRET_ACCESS_KEY` | The global key. |
| `FLOCK_HUB_BUCKET` | The bucket, `flock-hub` by default. |
| `FLOCK_HUB_PROXY_URL` | Where Flock reaches the hub's S3 proxy, such as `http://hub-s3:9000`. |
| `FLOCK_HUB_PROXY_PUBLIC_URL` | The proxy's URL as users reach it, if it differs. |

Then start the S3 proxy, as [Running the hub's S3 proxy](s3-proxy) describes. Until `FLOCK_HUB_S3_ENDPOINT` is set, the hub's endpoints answer 503. All the settings are in [Settings](configuration).

Users never get the global key. They reach the bucket through the S3 proxy, which checks each request against their roles, with S3 keys of their own (see [S3 keys](#s3-keys)). Flock's own writes (cards, tags, restore, optimize, prune and mirror imports) go through the proxy too, with temporary credentials of the user who asked, so the proxy records every version's author the same way.

## Creating a dataset

A dataset belongs to an organization, and any member can create one:

```bash
curl -s --json '{"name": "fineweb-sample", "description": "10k documents from FineWeb"}' -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/datasets/flock
```

Flock answers 201 with the dataset. Its `uri`, `s3://datasets/flock/fineweb-sample`, is the Lance URI that clients write to through the proxy. It has no versions yet: `latest_version` is `null` until the first write.

Names follow the rules of project names. A dataset is `private` by default, readable by the organization's members only. An `internal` dataset is readable by anyone signed in to Flock, and only members can change it.

## Writing and reading

Every Lance commit to the dataset is a version, numbered by Lance from 1. Write with `flock.hub`, or with any Lance client given an S3 key and the proxy as its endpoint.

Flock records who wrote each version and keeps a summary of it: the Lance operation, the commit message, and the rows, bytes and fragments at that version. List the history, newest first:

```bash
curl -s -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/datasets/flock/fineweb-sample/versions
```

To set a commit message, pass it as the transaction property `__lance_commit_message`. Some Lance edits, such as deletes, can't carry one: the version's author or an org admin can then set it once with `PATCH .../versions/{version}`.

Read rows without a Lance client with `GET .../rows`. It returns a page of up to 500 rows, at the latest version or the one you ask for. Long strings are cut to 2000 characters and binary values show only their size, so a page stays small.

## The card

A dataset's card is a Markdown README. Replace it with:

```bash
curl -s -X PUT --json '{"content": "# FineWeb sample\n\n10k documents.", "message": "add a card"}' -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/datasets/flock/fineweb-sample/readme
```

Setting the card makes a new version, so each version has the card it was written with: `GET .../readme?version=3` returns the card as it was at version 3. The dataset needs a first version before it can have a card.

## Tags, restore and optimize

- **Tags** name a version, such as `v1.0`, and make no new version. `PUT .../tags/v1.0` with `{"version": 4}` creates the tag or moves it.
- **Restore** makes an earlier version the latest again, as a new version. The versions in between stay in the history.
- **Optimize** compacts small fragments into larger ones, which makes reads faster. It writes new versions with the same rows, and none when there is nothing to compact.

Any member can do these.

## Pruning old versions

Versions keep the files they use, so a dataset rewritten often grows. Pruning deletes versions older than a number of days, and the files only they use:

```bash
curl -s --json '{"older_than_days": 30}' -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/datasets/flock/fineweb-sample/prune
```

It keeps the latest version and every tagged version. Pruned versions can't be read any more, but they stay in the history, marked `pruned`, with their author and summary. Only org admins can prune, and Flock never prunes on its own.

> [!WARNING]
> Pruning and deleting a dataset can't be undone.

Deleting a dataset (org admins only) frees its name at once and deletes its data in the background. A new dataset with the same name starts empty.

## Hugging Face mirrors

Flock imports small Hugging Face datasets on request into its mirror organization, `hf`, so that training jobs read them from your S3 instead of from Hugging Face. `flock.hub`'s `load_dataset` asks for a mirror when it can't find a dataset of the name it was given (the `flock.hub` guide "Importing from Hugging Face" covers the client side), and you can ask for one yourself:

```bash
curl -s --json '{"repo": "cais/mmlu", "config": "all"}' -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/hub/mirrors
```

Flock pins the revision (`main` unless you pass `revision`) to a commit, and then:

- if it already mirrored that commit, answers 200 with the mirror;
- if it is mirroring it, answers 202 with the same mirror, so concurrent requests share one import;
- if the dataset's data files are larger than `FLOCK_HUB_AUTO_IMPORT_MAX_BYTES`, answers 422 with a `command`, the `flock-hub import` command line that imports it in stages instead (see [Too large to mirror](#too-large-to-mirror));
- otherwise starts the import and answers 202.

Poll `GET /api/v1/hub/mirrors/{id}` until `state` is `finished` or `failed`. While it runs, `progress` holds the importer's current stage.

The import runs in a separate process, `python -m flock.hub.importer`, which loads every split with `datasets.load_dataset` and writes them as one version. The mirror dataset is named `<owner>--<name>`, plus `--<config>` with a config (`hf/cais--mmlu--all` above). It is `internal`, so anyone signed in can read it, and its info has a `mirror` field naming the repo, config and commit. Its versions are authored by the user who asked for the import, the data carries the transaction property `flock.source=hf:<repo>@<commit>`, and the version with the card is tagged `hf-` and the commit's first 8 characters. A new commit of the same repo overwrites the mirror's rows in a new version, and older imports stay readable through their tags.

`FLOCK_HUB_MIRROR_ORG` names the mirror organization, `FLOCK_HUB_AUTO_IMPORT_MAX_BYTES` sets the size limit (5 GB by default), and `FLOCK_HUB_HF_TOKEN` is a Hugging Face token for gated and private datasets, which the importer gets as `HF_TOKEN` (see [Settings](configuration)).

The size counts the files the README's `configs` YAML gives the config, or else every data file of the repo (Parquet, Arrow, JSON Lines, CSV and text, compressed or not).

A failed import keeps its `error`. Asking again starts it over. A restart stops running imports and marks them failed, for the same reason.

### Too large to mirror

Only Flock's own imports and the mirror organization's members can write to the mirror organization, and its members are usually just its admins. So the `command` of a 422 imports into your default organization instead, for example `--target flock/allenai--dolma`, or `<your-org>` when you belong to none. Run it on a machine with room for the raw files, with `FLOCK_API_URL` and `FLOCK_API_KEY` set.

If you are an admin of the mirror organization, change `--target` to `hf/<owner>--<name>` to import it as the mirror itself. It is `internal`, like every dataset of the mirror organization, and `load_dataset` falls back to it when called without `hf_revision`.

## S3 keys

Tools that speak S3, such as boto3, DuckDB or Lance itself, need an S3 key. Create one on your account:

```bash
curl -s --json '{"name": "duckdb", "read_only": true}' -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/me/hub-keys
```

The answer holds `access_key_id`, `secret_access_key` and the `endpoint` to use. The secret is shown only this once. A key acts with your current roles, so it loses access when you leave an organization. Add `"org": "flock"` to limit it to one organization's datasets, and delete it to revoke it.

`flock.hub` doesn't need a key: it trades your API key for temporary S3 credentials with `POST /hub/credentials`.
