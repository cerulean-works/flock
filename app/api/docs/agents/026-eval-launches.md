# 026: launching evals on Modal from the dashboard

| | |
| --- | --- |
| Status | implemented, pending review |
| Date | 2026-11-22 |
| Builds on | [017](017-hf-compat.md) (D124: the background job with state, heartbeat and cancel), [025](025-eval-results.md) (the Results tab a launched run shows on); `tests/modal_gpu.py` (the H100 image) |
| Keeps | D1 to D187; D72 (a wandb key reaches tracking only) is what D191 relies on |

## 1. Goal

`flock eval run` scores a model from a checkout on a GPU machine. Nobody can do that from the
dashboard. This stage adds a **Launch eval** button to a project: a member picks a public
Hugging Face model and public suites or tasks, and the server runs `flock eval` on a Modal H100
and logs the run to the project, where 025's Results tab shows it.

**Out of scope:** other backends (Slurm, nest), training launches, models in the Flock registry,
private datasets or models, and a queue of launches.

---

## 2. Design decisions

The numbering continues from 025 (D182 to D187).

### D188. A launch's cap is its time limit, and a monthly cap counts running launches at theirs

- **Two caps, in US dollars,** as server settings: `eval_launch_max_usd` per launch (25) and
  `eval_launch_monthly_usd` for the month over the whole server (300), with the H100 price as
  `eval_launch_usd_per_hour`. Modal bills one workspace, so the month is counted over every org.
- **The per-launch cap is enforced by Modal,** as the call's `timeout`: cap over price, less ten
  minutes for the container to start and stop (billed, but outside the call's own limit; the
  worker's `startup_timeout` is 8 of them), at most Modal's 24 hours. Containers scale down 2 s after their call instead of Modal's default minute,
  since each launch's environment gives it containers of its own. A launch can't overrun its cap,
  whatever happens to the server.
- **The monthly count never undercounts.** A running launch counts at its full cap, whenever it
  started, and so does one that ended while its call may have started unseen (`unconfirmed`: a
  lost answer to the spawn, or a replica that stopped while making the call). An ended one counts
  at its time from launch to end plus the ten minutes, which includes any wait for a GPU that
  Modal doesn't bill, in every month it ran in. Only a spawn Modal surely refused (no such app,
  a bad token) ends at no cost. An unconfirmed launch keeps its cap in every month until its
  call can't be running any more (a day in Modal's queue, its time limit and the overhead).
- **A cap too small to pay for a minute of scoring** after the overhead is refused (422) before
  any row or key is made. A purged org's launches stay as rows with no
  org (`ON DELETE SET NULL`), so they still count. A launch is refused (409) when its cap doesn't
  fit under what is left.
- **Launches are serialized** by a transaction-scoped advisory lock around the count and the
  insert, so two launches can't both take the month's last dollars.
- **No queue.** At the defaults the monthly cap allows 12 launches at once, and Modal queues GPU
  requests itself, so a queued state would add a scheduler and protect nothing.

### D189. Only public models and suites or tasks whose datasets are all public are launched

- **Why.** Modal is a third party. Private eval text (the exam sets, tc-edu, tw-text, CMMLU's
  mirror) and private checkpoints stay on our storage until someone decides otherwise.
- **The list comes from the task definitions,** not from a list kept in the app: the worker
  returns the suites and tasks whose every dataset has `private=False` (`catalogue`). The API
  can't import the task registry itself (it imports torch, about 15 s), so it asks the deployed
  worker once an hour. A suite with one private task is left out whole, as `zh_tw` is.
- **Three checks.** The API refuses a name outside the list (422), the worker refuses it again
  before any model loads, and the worker holds no Hugging Face token and no Flock storage key, so
  a private dataset or model can't be read even if both checks were wrong.
- **Models** are public Hugging Face repositories, checked without a token at launch (gated or
  private: 403 or 404) and resolved to a commit, which the worker scores. A moving branch can't
  change what a finished launch scored. The worker reads the same Hub (`HF_ENDPOINT`).

### D190. The worker is a deployed Modal app at the server's release, scoring with fixed settings

- `app/api/eval_worker.py` is deployed with `modal deploy` from the release the server runs. Its
  image is built as `tests/modal_gpu.py` builds the GPU tests' image, from `uv.lock` and the
  workspace, and records `FLOCK_COMMIT` at deploy time (the image has no `.git`), so every
  launched run's provenance names the commit that scored it.
- **Only tracked files go to Modal.** The image takes the files `git ls-files` lists, without the
  web app, so an ignored file such as `app/.env` or `flock-files/` never leaves the machine it
  is deployed from. Everything that reads the checkout runs only there (`modal.is_local()`): in
  the container the file sits alone in `/root`.
- Each function runs `app/api/eval_score.py` inside the workspace's environment (`uv run`), as
  the GPU tests run pytest, because Modal runs functions with the image's own Python. It's a file
  of its own: a function's child process counts as local to Modal (`modal.is_local()`), so
  running the deploy module again there would try to build the image from a checkout that
  isn't there.
- **The instrument is not the launcher's choice:** BF16 weights at 16,384 batch tokens, the eval
  bank's settings. Only `add_bos` is offered, because Gemma models are scored with it.
- **Another GPU is another device.** The run's provenance records `NVIDIA H100`. The bank was
  scored on H200, and the docs say not to compare the two until a parity check passes.

### D191. The server's own Modal token, and a wandb key per launch that ends with it

- **Modal token.** `modal_token_id` and `modal_token_secret` in the API's environment, a token for
  the server only, not CI's. Launches are off (404) without them, the price, or a non-loopback
  `api_public_url`: without that URL the worker's wandb SDK would send the key to W&B itself,
  and with Compose's default (`localhost`) it would log to itself on Modal.
- **Logging.** The server makes a wandb key for the launcher (`eval launch {id}`) and passes it to
  the call in its environment with the tracker's URL, the org and the run's name. A wandb key
  reaches tracking only (D72): the REST API and the S3 key exchange refuse it. It is deleted
  when the launch ends, whoever ends it (the trigger of migration 0013 drops it from every
  replica's cache), so it works only while the launch runs. It is made in the transaction that
  inserts the row and names it there, so no key exists that the row doesn't name, and only that
  key is deleted (key names are free text, so never by name).
- **Never stored.** The row keeps the key's `pk` only. The key is not in the database, a
  response or a log line, and the launcher can see it in their key list while it lives.
- **The run is named before it starts** (`eval-launch-{id}`, as `WANDB_RUN_ID`), so the launch
  links to its run without asking the worker.

### D192. A launch is a row watched by one replica, and any replica takes it over

- One row per launch in `eval_launches` (migration 0026), with the call's id, the key's `pk`, the
  cap and price at launch, and the flock commit once finished. States: `running`, `finished`,
  `failed`, `cancelled`.
- **Watching.** The replica that launched it waits on the call in short slices and heartbeats
  between them. A heartbeat that finds the launch no longer running stops watching.
- **No `interrupted` state,** unlike D124's imports: the call keeps running on Modal when a replica
  stops (a deploy, a crash). Any replica's sweep takes over a launch whose heartbeat is older
  than a minute (`FOR UPDATE SKIP LOCKED`, so one replica wins), and a launch cut short between
  its row and its call ends `failed` there.
- **Cancel** (its launcher or an org admin, as D124) cancels the call with its containers, so the
  GPU stops being billed at once, then ends the row and deletes the key. While the call is still
  being made the row has no call id, so a cancel then answers 409 instead of ending a row whose
  call would run unwatched. A call made after its row was taken over and ended is cancelled by
  the replica that made it.
- **Deleting the org** doesn't cancel anything itself, so a refused delete stops nothing. The
  replica watching each of its launches sees the org's `deleted_at` at its next heartbeat and
  cancels the call, retrying every heartbeat until Modal confirms.
- **Only the call's own end ends a launch.** A poll's deadline is Python's `TimeoutError`;
  Modal's `FunctionTimeoutError` is the call's time limit and fails it. A failed question to
  Modal (its `ConnectionError`, or any of its RPC errors, which are `GRPCError`s) is asked again
  at the next heartbeat.
- **A launch past its longest life ends.** The sweep ends a running launch older than a day in
  Modal's queue plus its time limit and the overhead (Modal's limit starts only when the call
  leaves its queue). Its call can't be running then, unless Modal can't tell (launches turned
  off, a rotated token): the call is cancelled where Modal can be asked, the key goes, and the
  launch keeps its cap in the month's count (`unconfirmed`), since what it cost is unknown.
- **A takeover of a launch with no call** ends it only if the row still has none; its maker may
  have attached the call meanwhile, and then the call is watched.
- **Locks** follow the catalog's order: the org's row, then the project's.
- **Its project stays put.** The worker logs to the project by name when it finishes, so a
  project with a running launch can't be renamed or deleted (409), as one with active runs can't
  be renamed. A launch holds its project's row (`FOR SHARE`) while it is registered, so a rename
  or delete either sees it or came first. Ended launches follow a rename. A deleted project's
  name reused later shows the old project's launches: a gap, as runs are addressed by name too.
- **A cancel is recorded before Modal is asked** (`cancel_requested`), so a watcher that sees the
  call end because of it records the launch `cancelled`, not `failed`.

---

## 3. Plan

### Part 1. API

- Migration `0026_eval_launches.sql`; settings; `core/eval_launches.py` (`EvalLaunches`, and
  `Modal`, the four calls to the deployed worker); routes in `api/eval_launches.py`.
- `app/api/eval_worker.py`: `catalogue` and `run_eval`.

### Part 2. Web

- A **Launch eval** dialog and the launch rows on the project page, through the web server's
  relay, as 017's import dialog and rows.

### Tests

- `tests/app/test_eval_launches.py`, with Modal as a fake and the Hub as an httpx mock: a launch
  from start to end (pinned commit, timeout from the cap, the key's environment, the key deleted
  at the end); refusals of private names, gated and private models and bad bodies, with nothing
  spawned and no key made; launches off without each setting; who may launch and cancel (an
  anonymous caller, another org, a wandb key, another member, the admin); the monthly cap; a
  failed call and a refused spawn; a restart with the takeover; the key absent from the database,
  responses and logs; deleting the org. One test lists the worker's public names from the real
  registry and checks its refusal.

### Docs

- `app/launch-evals.md` (guide, with the setup), `app/api/eval-launches.md` (reference), the
  settings in `app/configuration.md`.

---

## 4. How it was actually built

- **The design came first:** caps, which data may leave our storage, how H100 numbers relate to
  the H200 bank, and the job's shape were settled before any code, with the defaults above.
- **The registry can't be imported by the API** (torch, about 15 s), so the list of public
  suites and tasks comes from the worker (`catalogue`), and the worker checks the list again.
- **Five review rounds** changed the lifecycle more than the first draft did. Found and fixed:
  - the worker's root path, which doesn't exist in the container;
  - a poll's deadline is Python's `TimeoutError`, not Modal's, so every launch would have failed
    after one heartbeat;
  - the image took ignored files (`app/.env`), now only tracked ones;
  - `modal.is_local()` is true in a function's child process, hence `eval_score.py`;
  - keys deleted by name, now by identity, made in the row's transaction;
  - cancels and takeovers racing a spawn;
  - the month's count across month starts, purged orgs, lost spawn answers and container time
    outside the call's limit;
  - launches outliving a rotated token or a server without Modal settings;
  - projects renamed or deleted under a running launch.
- **The web side** reuses the import dialog's pieces (the relay, `datasetCall`, the rows' shape)
  and asks the API for one project's launches, since the list is limited before it reaches the
  page.
- **Parity, measured once:** OLMo 2 1B and Qwen3.5 0.8B Base at their bank pins, `base_easy` and
  `fineweb_en`, BF16 at 16,384 batch tokens with the same flock commit, scored once by the
  deployed worker on a Modal H100 and once on an H200: all 521 metric values of each model are
  bit-equal (110 tasks, equal hashes). Whether launched rows may then sit beside the bank's is
  decided outside this stage. Llama 3.2 1B, the first choice, is gated on the Hub, so a public launch
  can't load it.

## 5. Known gaps

- Models in the Flock registry can't be launched: the worker would need a credential that reads
  `flock-models`, which today also reads private ones.
- Parity is shown on two small models and one task set. A larger model, or a kernel that differs
  between the two GPUs, isn't covered; a launched run still records its device.
- A launch's cost is bounded, not measured: the row has no billed seconds from Modal.
