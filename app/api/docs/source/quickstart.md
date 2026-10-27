# Quickstart

This page takes you from an empty machine to a logged run that you read back through the API. You start Flock with Docker Compose, create the first account and a wandb key, log a run with `wandb`, and query it. Building the image is the slowest step.

## Before you start

You need:
- Git, and Docker with Compose v2 (the `docker compose` command).
- Python with the `wandb` package: `pip install wandb`. Flock is tested with wandb 0.31.
- `curl` 7.82 or newer, for its `--json` option, and [`jq`](https://jqlang.org).

## Start Flock

The image is built from the repository, so clone it and start the Compose stack from Flock's folder:

```bash
git clone https://github.com/cerulean-works/flock.git
cd flock/app/api
docker compose -f compose.dev.yaml up -d --build
```

Compose starts PostgreSQL and Flock. Flock applies the database migrations when it starts, then listens on port 8080. Check that it answers:

```bash
curl http://localhost:8080/healthz
```

```text
{"ok":true}
```

## Create the first account

A new Flock instance has no accounts, and it refuses every request that needs one until the first account exists. The first account to sign up becomes the **super admin**. The dashboard then asks you to name and create your organization. With the API, create the account and organization explicitly:

```bash
export FLOCK=http://localhost:8080
TOKEN=$(curl -s --json '{"email": "ana@example.com", "name": "Ana Lima", "password": "correct horse battery"}' $FLOCK/api/v1/auth/signup | jq -r .access_token)
curl --json '{"name": "flock"}' -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/orgs
```

The response holds an access token, which is valid for 15 minutes, and a refresh token. From now on, signing up is closed: other people join through invitations, as [Accounts, organizations and roles](accounts#inviting-people) describes.

> [!WARNING]
> Whoever signs up first becomes the super admin. Create this account before Flock is reachable by anyone else.

## Create a wandb key[[create-an-api-key]]

The `wandb` SDK authenticates with a wandb key rather than a password. Create one with your access token:

```bash
export WANDB_API_KEY=$(curl -s --json '{"name": "laptop"}' -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/me/wandb-keys | jq -r .key)
```

The key is `flock-wandb-` followed by 40 letters and digits. Flock stores only a hash of it, so this response is the only place you see it.

## Log a run

Point the SDK at Flock:

```bash
export WANDB_BASE_URL=$FLOCK
```

Save this script as `train.py`:

```python
import math

import wandb

run = wandb.init(
    project="llm-pretrain",
    name="gpt-small-lr3e-4",
    config={"lr": 3e-4, "batch_size": 256, "model": {"layers": 12, "d_model": 768}},
    tags=["baseline"],
)
for step in range(200):
    metrics = {"loss": 4.0 * math.exp(-step / 80) + 1.5}
    if step % 50 == 0:
        metrics["val_loss"] = metrics["loss"] + 0.1
    run.log(metrics)
run.finish()
```

Run it:

```bash
python train.py
```

The run lands in the project `flock/llm-pretrain`. `wandb.init` logs into your default organization when you don't pass `entity`, and it creates the project because it doesn't exist yet.

> [!NOTE]
> The links that `wandb` prints at the end point at Flock, which has no web pages. Open runs in your dashboard, or read them through the API.

## Read it back

The REST API doesn't take wandb keys: they only work with the `wandb` SDK. Use your access token instead. It lasts 15 minutes, so if it has run out, sign in again with `POST /api/v1/auth/login`. List the project's runs with a few columns:

```bash
curl -s -H "Authorization: Bearer $TOKEN" "$FLOCK/api/v1/projects/flock/llm-pretrain/runs?columns=display_name,state,config.lr,summary.loss"
```

```json
{"total":1,"offset":0,"limit":100,"ids":["9fc2v9ee"],"columns":["display_name","state","config.lr","summary.loss"],"data":{"display_name":["gpt-small-lr3e-4"],"state":["finished"],"config.lr":[0.0003],"summary.loss":[1.8324700032052101]}}
```

The response is column-oriented: `data` holds one list per column, aligned with `ids`. Your run id differs from `9fc2v9ee`, because `wandb` generates it.

Then read the validation loss curve:

```bash
curl -s -H "Authorization: Bearer $WANDB_API_KEY" "$FLOCK/api/v1/projects/flock/llm-pretrain/history?keys=val_loss"
```

```json
{"stream":"history","x":"_step","keys":["val_loss"],"runs":[{"id":"9fc2v9ee","display_name":"gpt-small-lr3e-4","state":"finished","series":{"val_loss":{"x":[0,50,100,150],"y":[5.6,3.741045714075961,2.7460191874407607,2.213419867379714]}}}]}
```

Flock also serves interactive API documentation at `http://localhost:8080/docs`.

## Next steps

- Learn what the SDK records, and how resumes and crashes behave: [Logging from training code](logging).
- Invite your colleagues: [Accounts, organizations and roles](accounts).
- Filter runs and plot curves: [Queries and filters](api/queries).
- Run Flock for real: [Deploying and operating](deploying).
